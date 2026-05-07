#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

import dolphindb as ddb
import pandas as pd
import yaml
from openai import OpenAI


@dataclass
class ArticleRecord:
    article_id: str
    mp_id: str
    mp_name: str
    title: str
    pub_time: pd.Timestamp
    pub_month: pd.Timestamp
    source_url: str
    file_path: str
    assets_dir: str
    cover_image: str
    topic_tags: str
    content_clean: str
    content_len: int
    content_hash: str


def expand_env(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: expand_env(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [expand_env(v) for v in obj]
    if isinstance(obj, str):
        return os.path.expandvars(obj)
    return obj


def load_config(path: Path) -> dict[str, Any]:
    return expand_env(yaml.safe_load(path.read_text("utf-8")) or {})


def parse_markdown_file(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_text("utf-8")
    if raw.startswith("---\n"):
        _, rest = raw.split("---\n", 1)
        fm, body = rest.split("\n---\n", 1)
        meta = yaml.safe_load(fm) or {}
        return meta, body.strip()
    return {}, raw.strip()


def clean_markdown_for_embedding(body: str) -> str:
    lines = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            lines.append("")
            continue
        if stripped.startswith("- 公众号："):
            continue
        if stripped.startswith("- 发布时间："):
            continue
        if stripped.startswith("- 原文链接："):
            continue
        if stripped.startswith("- 封面图："):
            continue
        if stripped.startswith("- 本地资源目录："):
            continue
        if re.fullmatch(r"!\[[^\]]*\]\([^)]+\)", stripped):
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


def detect_topics(text: str, topic_rules: dict[str, list[str]]) -> list[str]:
    hits = []
    lowered = text.lower()
    for topic, keywords in topic_rules.items():
        if any(str(keyword).lower() in lowered for keyword in keywords):
            hits.append(topic)
    return hits or ["未分类"]


def split_paragraphs(text: str) -> list[str]:
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    return [re.sub(r"\s+", " ", p).strip() for p in paras if p.strip()]


def chunk_text(text: str, max_chars: int, min_chars: int, overlap_chars: int) -> list[str]:
    paragraphs = split_paragraphs(text)
    if not paragraphs:
        return []

    chunks: list[str] = []
    current = ""
    for para in paragraphs:
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
            start = max(end - overlap_chars, start + 1)
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


def stable_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def parse_pub_time(value: str) -> pd.Timestamp:
    if not value:
        return pd.Timestamp.now()
    try:
        ts = pd.to_datetime(value)
        if getattr(ts, "tzinfo", None) is not None:
            return ts.tz_localize(None)
        return ts
    except Exception:
        return pd.Timestamp.now()


def to_month(ts: pd.Timestamp) -> pd.Timestamp:
    return pd.Timestamp(year=ts.year, month=ts.month, day=1)


def build_article_record(path: Path, meta: dict[str, Any], cfg: dict[str, Any]) -> tuple[ArticleRecord, list[str]]:
    body = clean_markdown_for_embedding(parse_markdown_file(path)[1])
    pub_time = parse_pub_time(str(meta.get("published_at", "")))
    topics = detect_topics(body, cfg.get("topic_rules", {}))
    article = ArticleRecord(
        article_id=str(meta.get("article_id", path.stem)),
        mp_id=str(meta.get("feed_id", "")),
        mp_name=str(meta.get("feed_title", path.parent.parent.name)),
        title=str(meta.get("title", path.stem)),
        pub_time=pub_time,
        pub_month=to_month(pub_time),
        source_url=str(meta.get("source_url", "")),
        file_path=str(path),
        assets_dir=str(path.with_suffix("").as_posix() + "__assets"),
        cover_image=str(meta.get("cover_image", "")),
        topic_tags=",".join(topics),
        content_clean=body,
        content_len=len(body),
        content_hash=stable_hash(body),
    )
    return article, topics


def connect_ddb(cfg: dict[str, Any]) -> ddb.session:
    ddb_cfg = cfg["dolphindb"]
    sess = ddb.session()
    sess.connect(
        host=ddb_cfg["host"],
        port=int(ddb_cfg["port"]),
        userid=ddb_cfg["user"],
        password=ddb_cfg["password"],
    )
    return sess


def fetch_existing_hashes(sess: ddb.session, cfg: dict[str, Any]) -> dict[str, str]:
    ddb_cfg = cfg["dolphindb"]
    exists = sess.run(f'existsTable("{ddb_cfg["database"]}", "{ddb_cfg["articles_table"]}")')
    if not exists:
        print("💡 数据库表还不存在，视为全量新入库。")
        return {}

    script = f'select article_id, content_hash from loadTable("{ddb_cfg["database"]}", "{ddb_cfg["articles_table"]}")'
    result = sess.run(script)
    if result is None or len(result) == 0:
        print("💡 数据库中尚未发现已存文章指纹。")
        return {}
    
    # 转换为字典，确保 ID 和 Hash 都是纯字符串并去除两端空格
    hashes = {
        str(k).strip(): str(v).strip() 
        for k, v in zip(result["article_id"], result["content_hash"])
    }
    print(f"✅ 从数据库加载了 {len(hashes)} 条文章指纹。示例 ID: {list(hashes.keys())[:3]}")
    return hashes


def delete_existing_articles(sess: ddb.session, cfg: dict[str, Any], article_ids: list[str]) -> None:
    if not article_ids:
        return
    sess.upload({"articleIdsToDelete": article_ids})
    ddb_cfg = cfg["dolphindb"]
    script = f"""
if(existsTable("{ddb_cfg['database']}", `{ddb_cfg['articles_table']})){{
    delete from loadTable("{ddb_cfg['database']}", `{ddb_cfg['articles_table']}) where article_id in articleIdsToDelete;
}}
if(existsTable("{ddb_cfg['database']}", `{ddb_cfg['chunks_table']})){{
    delete from loadTable("{ddb_cfg['database']}", `{ddb_cfg['chunks_table']}) where article_id in articleIdsToDelete;
}}
"""
    sess.run(script)


def init_embedding_client(cfg: dict[str, Any]) -> tuple[OpenAI, dict[str, Any]]:
    embed_cfg = cfg["embedding"]
    api_key = os.environ.get(embed_cfg["api_key_env"], "")
    if not api_key:
        raise SystemExit(f"环境变量 {embed_cfg['api_key_env']} 未设置，无法生成向量。")
    client = OpenAI(base_url=embed_cfg["base_url"], api_key=api_key)
    return client, embed_cfg


def embed_texts(client: OpenAI, embed_cfg: dict[str, Any], texts: list[str]) -> list[list[float]]:
    vectors: list[list[float]] = []
    # 强制硬限制：DashScope 接口单次请求最高只能处理 10 个片段
    batch_size = int(embed_cfg.get("batch_size", 10))
    if batch_size > 10:
        batch_size = 10
    
    expected_dim = int(embed_cfg["dimension"])
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        resp = client.embeddings.create(model=embed_cfg["model"], input=batch)
        for item in resp.data:
            vec = list(item.embedding)
            if len(vec) != expected_dim:
                raise ValueError(f"embedding 维度不匹配，期望 {expected_dim}，实际 {len(vec)}")
            vectors.append(vec)
    return vectors


def make_dataframes(cfg: dict[str, Any], files: list[Path], existing_hashes: dict[str, str]) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    ingest_cfg = cfg["ingest"]
    articles_rows = []
    chunk_rows = []
    texts_to_embed = []
    changed_article_ids = []

    for path in files:
        meta, _ = parse_markdown_file(path)
        article, _topics = build_article_record(path, meta, cfg)
        
        # 统一去除 ID 空格后再比对
        clean_id = str(article.article_id).strip()
        if existing_hashes.get(clean_id) == article.content_hash:
            continue
        changed_article_ids.append(article.article_id)
        articles_rows.append(
            {
                "pub_month": article.pub_month,
                "article_id": article.article_id,
                "content_hash": article.content_hash,
                "mp_id": article.mp_id,
                "mp_name": article.mp_name,
                "title": article.title,
                "pub_time": article.pub_time,
                "source_url": article.source_url,
                "file_path": article.file_path,
                "assets_dir": article.assets_dir,
                "cover_image": article.cover_image,
                "topic_tags": article.topic_tags,
                "content_clean": article.content_clean,
                "content_len": article.content_len,
                "ingested_at": pd.Timestamp.now(),
            }
        )
        chunks = chunk_text(
            article.content_clean,
            int(ingest_cfg["max_chunk_chars"]),
            int(ingest_cfg["min_chunk_chars"]),
            int(ingest_cfg["chunk_overlap_chars"]),
        )
        for idx, chunk in enumerate(chunks, start=1):
            chunk_rows.append(
                {
                    "pub_month": article.pub_month,
                    "chunk_id": f"{article.article_id}__{idx:04d}",
                    "article_id": article.article_id,
                    "content_hash": article.content_hash,
                    "mp_id": article.mp_id,
                    "mp_name": article.mp_name,
                    "title": article.title,
                    "pub_time": article.pub_time,
                    "source_url": article.source_url,
                    "topic_tags": article.topic_tags,
                    "chunk_no": idx,
                    "chunk_text": chunk,
                    "chunk_len": len(chunk),
                    "ingested_at": pd.Timestamp.now(),
                }
            )
            texts_to_embed.append(chunk)

    articles_df = pd.DataFrame(articles_rows)
    chunks_df = pd.DataFrame(chunk_rows)
    if not articles_df.empty:
        articles_df["pub_time"] = pd.to_datetime(articles_df["pub_time"]).dt.tz_localize(None)
        articles_df["ingested_at"] = pd.to_datetime(articles_df["ingested_at"]).dt.tz_localize(None)
        articles_df.attrs["__DolphinDB_Type__"] = {"pub_month": ddb.settings.DT_MONTH}
    if not chunks_df.empty:
        chunks_df["pub_time"] = pd.to_datetime(chunks_df["pub_time"]).dt.tz_localize(None)
        chunks_df["ingested_at"] = pd.to_datetime(chunks_df["ingested_at"]).dt.tz_localize(None)
        chunks_df.attrs["__DolphinDB_Type__"] = {"pub_month": ddb.settings.DT_MONTH}
    return articles_df, chunks_df, changed_article_ids, texts_to_embed


def append_tables(sess: ddb.session, cfg: dict[str, Any], articles_df: pd.DataFrame, chunks_df: pd.DataFrame) -> None:
    ddb_cfg = cfg["dolphindb"]
    if not articles_df.empty:
        sess.upload({"articlesDf": articles_df})
        sess.run(f'loadTable("{ddb_cfg["database"]}", `{ddb_cfg["articles_table"]}).append!(articlesDf)')
    if not chunks_df.empty:
        # 上传不含 embedding 的元数据 DataFrame
        meta_df = chunks_df.drop(columns=["embedding"]).copy()
        meta_df.attrs["__DolphinDB_Type__"] = {"pub_month": ddb.settings.DT_MONTH}

        # 将 embedding 展平为 1D float32 数组上传
        # 避免依赖 Python API 对多维数组的类型推断（易误识别为 NANOTIMESTAMP）
        embedding_list = chunks_df["embedding"].tolist()
        n_chunks = len(embedding_list)
        dim = int(len(embedding_list[0]))
        flat_emb = np.array(embedding_list, dtype=np.float32).flatten()

        sess.upload({"chunkMetaDf": meta_df, "flatEmbedding": flat_emb})
        sess.run(
            f'''
idx = (1..{n_chunks}) * {dim}
embArrVec = arrayVector(idx, flatEmbedding)
chunkMetaDf[`embedding] = embArrVec
// DolphinDB append! 按列位置匹配，必须与 DFS 表列顺序一致：
// ..., chunk_len, embedding, ingested_at
reorderColumns!(chunkMetaDf, `pub_month`chunk_id`article_id`content_hash`mp_id`mp_name`title`pub_time`source_url`topic_tags`chunk_no`chunk_text`chunk_len`embedding`ingested_at)
loadTable("{ddb_cfg["database"]}", `{ddb_cfg["chunks_table"]}).append!(chunkMetaDf)
'''
        )
    if cfg["ingest"].get("flush_cache_after_write", True):
        sess.run("flushTSDBCache()")


def generate_keywords(texts: list[str], limit: int = 12) -> str:
    stopwords = {"我们", "你们", "他们", "以及", "可以", "已经", "一个", "进行", "对于", "因为", "就是", "这个", "那个", "并且", "如果", "当前", "相关"}
    counter: Counter[str] = Counter()
    for text in texts:
        words = re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,}", text)
        for word in words:
            if word in stopwords:
                continue
            counter[word] += 1
    return ",".join(word for word, _ in counter.most_common(limit))


def refresh_profiles(sess: ddb.session, cfg: dict[str, Any]) -> None:
    ddb_cfg = cfg["dolphindb"]
    articles = sess.run(f'select * from loadTable("{ddb_cfg["database"]}", `{ddb_cfg["articles_table"]})')
    if articles is None or len(articles) == 0:
        print("no articles in DolphinDB, skip profile refresh")
        return

    as_of_date = pd.Timestamp.now().normalize()
    mp_rows = []
    topic_rows = []
    topic_to_texts: dict[str, list[str]] = defaultdict(list)
    topic_to_mps: dict[str, set[str]] = defaultdict(set)

    for (mp_id, mp_name), group in articles.groupby(["mp_id", "mp_name"], dropna=False):
        texts = group["content_clean"].astype(str).tolist()
        topic_counter: Counter[str] = Counter()
        for tags in group["topic_tags"].fillna("").astype(str):
            for topic in [t for t in tags.split(",") if t]:
                topic_counter[topic] += 1
                topic_to_texts[topic].extend(texts)
                topic_to_mps[topic].add(str(mp_id))
        topic_summary = ",".join(f"{k}:{v}" for k, v in topic_counter.most_common(6))
        keyword_summary = generate_keywords(texts)
        latest_pub_time = pd.to_datetime(group["pub_time"]).max()
        profile_text = (
            f"{mp_name} 最近共收录 {len(group)} 篇文章，最近发布时间 {latest_pub_time}。"
            f"主要关注主题：{topic_summary or '未分类'}。"
            f"高频关键词：{keyword_summary or '暂无'}。"
        )
        mp_rows.append(
            {
                "as_of_date": as_of_date,
                "mp_id": mp_id,
                "mp_name": mp_name,
                "article_count": len(group),
                "latest_pub_time": latest_pub_time,
                "topic_summary": topic_summary,
                "keyword_summary": keyword_summary,
                "profile_text": profile_text,
                "updated_at": pd.Timestamp.now(),
            }
        )

    for topic, texts in topic_to_texts.items():
        keyword_summary = generate_keywords(texts)
        article_count = int((articles["topic_tags"].fillna("").astype(str).str.contains(topic)).sum())
        mp_count = len(topic_to_mps[topic])
        profile_text = f"主题 {topic} 当前覆盖文章 {article_count} 篇，涉及公众号 {mp_count} 个。高频关键词：{keyword_summary or '暂无'}。"
        topic_rows.append(
            {
                "as_of_date": as_of_date,
                "topic_name": topic,
                "article_count": article_count,
                "mp_count": mp_count,
                "keyword_summary": keyword_summary,
                "profile_text": profile_text,
                "updated_at": pd.Timestamp.now(),
            }
        )

    sess.upload({"currentAsOfDate": [as_of_date.date()]})
    sess.run(
        f'''
delete from loadTable("{ddb_cfg["database"]}", `{ddb_cfg["mp_profiles_table"]}) where as_of_date in currentAsOfDate;
delete from loadTable("{ddb_cfg["database"]}", `{ddb_cfg["topic_profiles_table"]}) where as_of_date in currentAsOfDate;
'''
    )
    mp_df = pd.DataFrame(mp_rows)
    topic_df = pd.DataFrame(topic_rows)
    if not mp_df.empty:
        mp_df["latest_pub_time"] = pd.to_datetime(mp_df["latest_pub_time"]).dt.tz_localize(None)
        mp_df["updated_at"] = pd.to_datetime(mp_df["updated_at"]).dt.tz_localize(None)
        mp_df.attrs["__DolphinDB_Type__"] = {"as_of_date": ddb.settings.DT_DATE}
        sess.upload({"mpProfileDf": mp_df})
        sess.run(f'loadTable("{ddb_cfg["database"]}", `{ddb_cfg["mp_profiles_table"]}).append!(mpProfileDf)')
    if not topic_df.empty:
        topic_df["updated_at"] = pd.to_datetime(topic_df["updated_at"]).dt.tz_localize(None)
        topic_df.attrs["__DolphinDB_Type__"] = {"as_of_date": ddb.settings.DT_DATE}
        sess.upload({"topicProfileDf": topic_df})
        sess.run(f'loadTable("{ddb_cfg["database"]}", `{ddb_cfg["topic_profiles_table"]}).append!(topicProfileDf)')


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest markdown articles into DolphinDB VectorDB.")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--refresh-profiles", action="store_true")
    args = parser.parse_args()

    cfg = load_config(args.config)
    sess = connect_ddb(cfg)

    markdown_root = Path(cfg["ingest"]["markdown_root"])
    files = sorted(markdown_root.rglob("*.md"))
    files = [f for f in files if f.name != "state.json"]
    if args.limit:
        files = files[: args.limit]

    existing_hashes = fetch_existing_hashes(sess, cfg)
    articles_df, chunks_df, changed_article_ids, texts_to_embed = make_dataframes(cfg, files, existing_hashes)
    print(f"files scanned={len(files)}, changed_articles={len(changed_article_ids)}, chunks={len(chunks_df)}")
    if articles_df.empty or chunks_df.empty:
        if args.refresh_profiles:
            refresh_profiles(sess, cfg)
        return 0

    client, embed_cfg = init_embedding_client(cfg)
    embeddings = embed_texts(client, embed_cfg, texts_to_embed)
    chunks_df["embedding"] = pd.Series(embeddings, dtype="object")

    delete_existing_articles(sess, cfg, changed_article_ids)
    append_tables(sess, cfg, articles_df, chunks_df)
    print(f"ingest complete: articles={len(articles_df)}, chunks={len(chunks_df)}")

    if args.refresh_profiles:
        refresh_profiles(sess, cfg)
        print("profiles refreshed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
