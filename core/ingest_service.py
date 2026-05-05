#!/usr/bin/env python3
"""
一站式流水线：we-mp-rss API → 图片上传COS → SQLite → DolphinDB向量化
每篇文章处理完后立即写入所有存储，支持断点续传。

用法：
  # 测试5篇
  python pipeline_ingest.py --config config.yaml --limit 5

  # 全量（只处理未完成的）
  python pipeline_ingest.py --config config.yaml

  # 强制重新处理所有
  python pipeline_ingest.py --config config.yaml --force

  # 只跑到 SQLite，跳过 DolphinDB
  python pipeline_ingest.py --config config.yaml --skip-ddb
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests
import yaml

sys.path.insert(0, str(Path(__file__).parent))
from sync_wemp_markdown import (
    convert_html_to_markdown,
    extract_article_html,
    guess_image_suffix,
    reflow_markdown,
    sanitize_name,
)
from init_wemp_db import init_db


# ─────────────────────────── 配置与日志 ────────────────────────────

from core.logger import ingest_logger as logger

def load_config(path: Path) -> dict[str, Any]:
    def expand(obj):
        if isinstance(obj, dict):
            return {k: expand(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [expand(v) for v in obj]
        if isinstance(obj, str):
            # 支持 ${VAR:-default} 语法
            match = re.match(r'\$\{(.*):-(.*)\}', obj)
            if match:
                env_var, default_val = match.groups()
                return os.environ.get(env_var, default_val)
            return os.path.expandvars(obj)
        return obj
    return expand(yaml.safe_load(path.read_text("utf-8")) or {})


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def stable_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


# ─────────────────────────── COS ─────────────────────────────────

def build_cos_client(cos_cfg: dict):
    try:
        from qcloud_cos import CosConfig, CosS3Client
    except ImportError:
        raise SystemExit("请先安装：pip install cos-python-sdk-v5")
    cfg = CosConfig(Region=cos_cfg["region"], SecretId=cos_cfg["secret_id"], SecretKey=cos_cfg["secret_key"])
    return CosS3Client(cfg), cos_cfg["bucket"], cos_cfg["region"]


def cos_url(bucket: str, region: str, key: str) -> str:
    return f"https://{bucket}.cos.{region}.myqcloud.com/{key}"


def upload_to_cos(client, bucket: str, region: str, data: bytes, key: str) -> str | None:
    try:
        client.head_object(Bucket=bucket, Key=key)
        return cos_url(bucket, region, key)
    except Exception:
        pass
    try:
        client.put_object(Bucket=bucket, Key=key, Body=data)
        return cos_url(bucket, region, key)
    except Exception as e:
        logger.error(f"  ⚠️ COS上传失败 {key}: {e}")
        return None


# ─────────────────────────── API Client ──────────────────────────

class WempApi:
    def __init__(self, base_url: str, username: str = None, password: str = None, access_key: str = None, secret_key: str = None):
        self.base = base_url.rstrip("/")
        self.sess = requests.Session()
        self.sess.headers["User-Agent"] = "wemp-pipeline/2.0"
        
        if access_key and secret_key:
            self.sess.headers["Authorization"] = f"AK-SK {access_key}:{secret_key}"
            logger.info("✅ API 使用 AK/SK 认证成功")
        else:
            resp = self.sess.post(
                f"{self.base}/api/v1/wx/auth/token",
                data={"grant_type": "password", "username": username, "password": password},
                auth=("basic", "123456"), timeout=30,
            )
            resp.raise_for_status()
            self.sess.headers["Authorization"] = f"Bearer {resp.json()['access_token']}"
            logger.info("✅ API 登录成功")

    def list_articles(self, offset: int, limit: int = 100) -> dict:
        r = self.sess.get(f"{self.base}/api/v1/wx/articles",
                          params={"offset": offset, "limit": limit, "status": ""}, timeout=30)
        r.raise_for_status()
        return r.json()["data"]

    def get_article(self, aid: str) -> dict | None:
        try:
            r = self.sess.get(f"{self.base}/api/v1/wx/articles/{aid}",
                              params={"content": "true"}, timeout=60)
            r.raise_for_status()
            return r.json()["data"]
        except Exception as e:
            logger.error(f"  ⚠️ 拉取失败 {aid}: {e}")
            return None


# ─────────────────────────── 图片处理 ───────────────────────────

def process_images(img_sess, markdown, html, cos_client, bucket, region, cos_prefix, timeout):
    # 同时从 MD 和 HTML 中提取所有微信链接
    md_urls = [m.group(2).strip() for m in re.finditer(r"!\[([^\]]*)\]\((https?://[^)]+)\)", markdown)]
    html_urls = [m.group(1).strip() for m in re.finditer(r'(?:src|data-src)=["\'](https?://mmbiz\.qpic\.cn/[^"\']+)["\']', html)]
    
    # 合并并去重
    wx_urls = list(dict.fromkeys(md_urls + html_urls))
    cache: dict[str, str] = {}
    ok = fail = 0

    for idx, wx_url in enumerate(wx_urls, 1):
        suffix = guess_image_suffix(wx_url, "")
        key = f"{cos_prefix}/img_{idx:03d}{suffix}"
        try:
            r = img_sess.get(wx_url, timeout=timeout)
            r.raise_for_status()
            ct = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
            real_suffix = guess_image_suffix(wx_url, ct)
            if real_suffix != suffix:
                key = f"{cos_prefix}/img_{idx:03d}{real_suffix}"
            new_url = upload_to_cos(cos_client, bucket, region, r.content, key)
            if new_url:
                cache[wx_url] = new_url
                ok += 1
            else:
                fail += 1
        except Exception:
            fail += 1

    # 替换 Markdown
    def replace_md(m):
        raw = m.group(2).strip()
        return f"![{m.group(1)}]({cache.get(raw, raw)})"
    new_md = re.sub(r"!\[([^\]]*)\]\((https?://[^)]+)\)", replace_md, markdown)

    # 替换 HTML 中的所有图片属性
    def replace_img_tag(m):
        tag = m.group(0)
        new_tag = tag
        # 寻找并替换所有可能的链接属性
        for attr in ["src", "data-src", "data-backsrc"]:
            attr_m = re.search(f'{attr}=["\']([^"\']+)["\']', new_tag)
            if attr_m:
                raw_url = attr_m.group(1).strip()
                if raw_url in cache:
                    new_tag = new_tag.replace(attr_m.group(0), f'{attr}="{cache[raw_url]}"')
        
        if 'referrerpolicy' not in new_tag:
            new_tag = new_tag.replace("<img", '<img referrerpolicy="no-referrer"')
        return new_tag

    new_html = re.sub(r"<img[^>]+>", replace_img_tag, html)
    return new_md, new_html, ok, fail


# ─────────────────────────── 文本清洗 ───────────────────────────

def clean_for_embedding(body: str) -> str:
    lines = []
    for line in body.splitlines():
        s = line.strip()
        if not s:
            lines.append("")
            continue
        if any(s.startswith(p) for p in ("- 公众号：", "- 发布时间：", "- 原文链接：", "- 封面图：", "- 本地资源目录：")):
            continue
        if re.fullmatch(r"!\[[^\]]*\]\([^)]+\)", s):
            continue
        lines.append(line)
    text = "\n".join(lines)
    text = re.sub(r"^#\s+", "", text, flags=re.M)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


# ─────────────────────────── 分段 ───────────────────────────────

def chunk_text(text: str, max_chars: int, min_chars: int, overlap: int) -> list[str]:
    paras = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    current = ""
    for para in paras:
        candidate = f"{current}\n\n{para}".strip() if current else para
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
        if len(para) <= max_chars:
            current = para
            continue
        start = 0
        while start < len(para):
            end = min(start + max_chars, len(para))
            piece = para[start:end].strip()
            if piece:
                chunks.append(piece)
            if end >= len(para):
                break
            start = max(end - overlap, start + 1)
        current = ""
    if current:
        chunks.append(current)
    merged: list[str] = []
    for chunk in chunks:
        if merged and len(chunk) < min_chars:
            merged[-1] = (merged[-1] + "\n\n" + chunk).strip()
        else:
            merged.append(chunk)
    return merged


# ─────────────────────────── DolphinDB ──────────────────────────

def connect_ddb(cfg: dict):
    import dolphindb as ddb
    ddb_cfg = cfg["dolphindb"]
    sess = ddb.session()
    sess.connect(host=ddb_cfg["host"], port=int(ddb_cfg["port"]),
                 userid=ddb_cfg["user"], password=ddb_cfg["password"])
    return sess


def embed_texts(client, embed_cfg: dict, texts: list[str]) -> list[list[float]]:
    vectors = []
    batch_size = int(embed_cfg.get("batch_size", 32))
    dim = int(embed_cfg["dimension"])
    for i in range(0, len(texts), batch_size):
        resp = client.embeddings.create(model=embed_cfg["model"], input=texts[i:i + batch_size])
        for item in resp.data:
            vec = list(item.embedding)
            if len(vec) != dim:
                raise ValueError(f"维度不符：期望{dim}，实际{len(vec)}")
            vectors.append(vec)
    return vectors


def write_to_ddb(sess, cfg: dict, article_row: dict, chunk_rows: list[dict]) -> None:
    import dolphindb as ddb
    ddb_cfg = cfg["dolphindb"]

    # 删旧数据
    sess.upload({"_aid": [article_row["article_id"]]})
    for tbl in [ddb_cfg["articles_table"], ddb_cfg["chunks_table"]]:
        sess.run(f'''
if(existsTable("{ddb_cfg["database"]}", `{tbl})){{
    delete from loadTable("{ddb_cfg["database"]}", `{tbl}) where article_id in _aid;
}}''')

    # 写文章表
    art_df = pd.DataFrame([article_row])
    art_df["pub_time"] = pd.to_datetime(art_df["pub_time"]).dt.tz_localize(None)
    art_df["ingested_at"] = pd.to_datetime(art_df["ingested_at"]).dt.tz_localize(None)
    art_df.attrs["__DolphinDB_Type__"] = {"pub_month": ddb.settings.DT_MONTH}
    sess.upload({"_artDf": art_df})
    sess.run(f'loadTable("{ddb_cfg["database"]}", `{ddb_cfg["articles_table"]}).append!(_artDf)')

    # 写 chunk 表
    if chunk_rows:
        chunks_df = pd.DataFrame(chunk_rows)
        chunks_df["pub_time"] = pd.to_datetime(chunks_df["pub_time"]).dt.tz_localize(None)
        chunks_df["ingested_at"] = pd.to_datetime(chunks_df["ingested_at"]).dt.tz_localize(None)
        embeddings = chunks_df["embedding"].tolist()
        n = len(embeddings)
        dim = len(embeddings[0])
        flat = np.array(embeddings, dtype=np.float32).flatten()
        meta_df = chunks_df.drop(columns=["embedding"]).copy()
        meta_df.attrs["__DolphinDB_Type__"] = {"pub_month": ddb.settings.DT_MONTH}
        sess.upload({"_chunkMeta": meta_df, "_flatEmb": flat})
        sess.run(f'''
idx = (1..{n}) * {dim}
embArr = arrayVector(idx, _flatEmb)
_chunkMeta[`embedding] = embArr
reorderColumns!(_chunkMeta, `pub_month`chunk_id`article_id`content_hash`mp_id`mp_name`title`pub_time`source_url`topic_tags`chunk_no`chunk_text`chunk_len`embedding`ingested_at)
loadTable("{ddb_cfg["database"]}", `{ddb_cfg["chunks_table"]}).append!(_chunkMeta)
''')


# ─────────────────────────── 文章处理核心 ────────────────────────

def process_article(
    article: dict,
    dest_conn: sqlite3.Connection,
    img_sess: requests.Session,
    cos_client, bucket: str, region: str,
    ddb_sess, cfg: dict,
    request_timeout: int,
    force: bool,
    skip_ddb: bool,
) -> str:
    aid = article["id"]
    raw_html = article.get("content") or ""

    if not raw_html.strip():
        return "no_content"

    if not force:
        row = dest_conn.execute(
            "SELECT md_converted, embedded FROM wemp_articles WHERE article_id = ?", (aid,)
        ).fetchone()
        if row and row[0] == 1 and (skip_ddb or row[1] == 1):
            return "skipped"

    mp_id = article.get("mp_id") or "unknown"
    mp_name = article.get("mp_name") or "未知"
    title = article.get("title") or "无标题"
    url = article.get("url") or ""
    pic_url = article.get("pic_url") or ""
    pub_time = article.get("publish_time")

    # HTML → Markdown
    body_html = extract_article_html(raw_html)
    md_body = reflow_markdown(convert_html_to_markdown(body_html))

    # 图片处理
    cos_prefix = f"wemp-images/{mp_id}/{sanitize_name(aid)}__assets"
    md_body, body_html_cos, img_ok, img_fail = process_images(
        img_sess, md_body, body_html, cos_client, bucket, region, cos_prefix, request_timeout
    )

    # 封面图
    cover_cos = pic_url
    if pic_url and pic_url.startswith("http"):
        try:
            r = img_sess.get(pic_url, timeout=request_timeout)
            r.raise_for_status()
            ct = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
            suffix = guess_image_suffix(pic_url, ct)
            new_cover = upload_to_cos(cos_client, bucket, region, r.content, f"{cos_prefix}/cover{suffix}")
            if new_cover:
                cover_cos = new_cover
        except Exception:
            pass

    # 生成完整 Markdown
    meta_fm = {
        "article_id": aid, "mp_id": mp_id, "title": title,
        "source_url": url, "published_at": pub_time, "cover_image": cover_cos,
    }
    frontmatter = yaml.safe_dump(meta_fm, allow_unicode=True, sort_keys=False).strip()
    full_md = f"---\n{frontmatter}\n---\n\n# {title}\n\n{md_body.strip()}\n"
    content_clean = clean_for_embedding(md_body)
    content_hash = stable_hash(content_clean)
    now = now_iso()

    # 写 SQLite
    dest_conn.execute("""
        INSERT INTO wemp_articles
            (article_id, mp_id, mp_name, title, source_url, published_at,
             cover_cos, cover_wx, content_html, content_md, content_clean,
             md_converted, embedded, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, ?, ?)
        ON CONFLICT(article_id) DO UPDATE SET
            mp_name=excluded.mp_name,
            content_html=excluded.content_html, content_md=excluded.content_md,
            content_clean=excluded.content_clean, cover_cos=excluded.cover_cos,
            cover_wx=excluded.cover_wx, md_converted=1, updated_at=excluded.updated_at
    """, (aid, mp_id, mp_name, title, url, pub_time, cover_cos, pic_url,
          body_html_cos, full_md, content_clean, now, now))
    dest_conn.commit()

    # 向量化并写 DolphinDB
    if not skip_ddb and ddb_sess is not None and content_clean:
        ingest_cfg = cfg.get("ingest", {})
        chunks = chunk_text(
            content_clean,
            int(ingest_cfg.get("max_chunk_chars", 800)),
            int(ingest_cfg.get("min_chunk_chars", 100)),
            int(ingest_cfg.get("chunk_overlap_chars", 100)),
        )
        if chunks:
            embed_cfg = cfg["embedding"]
            # 优先从 config.yaml 读取
            api_key = cfg.get("api_keys", {}).get("dashscope", "").strip()
            if not api_key:
                api_key = os.environ.get(embed_cfg.get("api_key_env", ""), "")
            if not api_key:
                logger.warning(f"  ⚠️ Embedding API Key 未设置，跳过 DDB")
            else:
                from openai import OpenAI
                client = OpenAI(base_url=embed_cfg["base_url"], api_key=api_key)
                vectors = embed_texts(client, embed_cfg, chunks)

                pub_ts = pd.to_datetime(pub_time) if pub_time else pd.Timestamp.now()
                if getattr(pub_ts, "tzinfo", None):
                    pub_ts = pub_ts.tz_localize(None)
                pub_month = pd.Timestamp(year=pub_ts.year, month=pub_ts.month, day=1)

                art_row = {
                    "pub_month": pub_month, "article_id": aid,
                    "content_hash": content_hash, "mp_id": mp_id, "mp_name": mp_name,
                    "title": title, "pub_time": pub_ts, "source_url": url,
                    "file_path": "", "assets_dir": cos_prefix,
                    "cover_image": cover_cos, "topic_tags": "未分类",
                    "content_clean": content_clean, "content_len": len(content_clean),
                    "ingested_at": pd.Timestamp.now(),
                }
                chunk_rows = [
                    {
                        "pub_month": pub_month,
                        "chunk_id": f"{aid}__{idx:04d}",
                        "article_id": aid, "content_hash": content_hash,
                        "mp_id": mp_id, "mp_name": mp_name, "title": title,
                        "pub_time": pub_ts, "source_url": url,
                        "topic_tags": "未分类", "chunk_no": idx,
                        "chunk_text": chunk, "chunk_len": len(chunk),
                        "embedding": vec, "ingested_at": pd.Timestamp.now(),
                    }
                    for idx, (chunk, vec) in enumerate(zip(chunks, vectors), 1)
                ]
                write_to_ddb(ddb_sess, cfg, art_row, chunk_rows)

                # 更新 embedded 标志
                dest_conn.execute(
                    "UPDATE wemp_articles SET embedded=1, updated_at=? WHERE article_id=?", (now, aid)
                )
                dest_conn.commit()

    logger.info(f"  ✅ {title[:40]}  (图片 ok={img_ok} fail={img_fail})")
    return "ok"


# ─────────────────────────── 主程序 ─────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="一站式流水线：API → COS → SQLite → DolphinDB")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--dest-db", type=Path, default=Path("./data/wemp_data.db"))
    parser.add_argument("--api-url", default="http://localhost:8001")
    parser.add_argument("--api-user", default="admin")
    parser.add_argument("--api-pass", default="admin@123")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--force", action="store_true", help="强制重新处理已完成的文章")
    parser.add_argument("--skip-ddb", action="store_true", help="跳过 DolphinDB 写入（只做 SQLite）")
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--request-timeout", type=int, default=30)
    args = parser.parse_args()

    cfg = load_config(args.config)
    cos_cfg = cfg.get("tencent_cos", {})
    if not cos_cfg.get("enabled"):
        raise SystemExit("config.yaml 里 tencent_cos.enabled 未设为 true")

    cos_client, bucket, region = build_cos_client(cos_cfg)
    img_sess = requests.Session()
    img_sess.headers["User-Agent"] = "Mozilla/5.0"

    # 优先从 config.yaml 读取数据库路径
    db_url = cfg.get("database_url", "")
    if db_url.startswith("sqlite:///"):
        db_path = Path(db_url.replace("sqlite:///", ""))
    else:
        db_path = args.dest_db

    init_db(db_path)
    dest_conn = sqlite3.connect(str(db_path))
    dest_conn.row_factory = sqlite3.Row

    api = WempApi(args.api_url, args.api_user, args.api_pass)

    ddb_sess = None
    if not args.skip_ddb:
        try:
            ddb_sess = connect_ddb(cfg)
            print("✅ DolphinDB 连接成功")
        except Exception as e:
            logger.error(f"⚠️ DolphinDB 连接失败，将跳过向量写入：{e}")

    total = api.list_articles(0, 1)["total"]
    logger.info(f"📚 API 共 {total} 篇文章，开始处理...")

    counts = {"ok": 0, "skipped": 0, "no_content": 0, "error": 0}
    processed = 0
    offset = 0

    while True:
        page = api.list_articles(offset, args.page_size)
        items = page["list"]
        if not items:
            break

        for item in items:
            if args.limit and processed >= args.limit:
                break

            aid = item["id"]
            article = api.get_article(aid)
            if not article:
                counts["error"] += 1
                processed += 1
                continue
            
            # 合并列表中的元数据（如 mp_name, publish_time）到详情对象中
            for key in ["mp_name", "mp_id", "title", "publish_time", "url", "pic_url"]:
                if key in item and (key not in article or not article[key]):
                    article[key] = item[key]

            try:
                result = process_article(
                    article, dest_conn, img_sess,
                    cos_client, bucket, region,
                    ddb_sess, cfg,
                    args.request_timeout, args.force, args.skip_ddb,
                )
                counts[result] += 1
            except Exception as e:
                counts["error"] += 1
                print(f"  ❌ {item.get('title', aid)}: {e}", file=sys.stderr)

            processed += 1
            if processed % 10 == 0:
                logger.info(
                    f"进度: {processed}/{args.limit or total} "
                    f"处理={counts['ok']} 跳过={counts['skipped']} "
                    f"无内容={counts['no_content']} 失败={counts['error']}"
                )

        offset += args.page_size
        if args.limit and processed >= args.limit:
            break
        if offset >= total:
            break
        time.sleep(0.3)

    dest_conn.close()
    logger.info(f"✅ 完成：处理={counts['ok']}, 跳过={counts['skipped']}, 无内容={counts['no_content']}, 失败={counts['error']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
