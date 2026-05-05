#!/usr/bin/env python3
from __future__ import annotations

import argparse
import mimetypes
import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urljoin, urlparse
import xml.etree.ElementTree as ET

import requests
import yaml
from bs4 import BeautifulSoup, NavigableString, Tag

try:
    from markdownify import markdownify as html_to_markdown  # type: ignore
except Exception:
    html_to_markdown = None


CONTENT_NS = {"content": "http://purl.org/rss/1.0/modules/content/"}


@dataclass
class FeedMeta:
    feed_id: str
    title: str
    description: str


class WeMpRssSync:
    def __init__(self, config: dict[str, Any]) -> None:
        self.base_url = config["base_url"].rstrip("/")
        self.output_dir = Path(config.get("output_dir", "./markdown_store")).expanduser().resolve()
        self.state_path = Path(config.get("state_path", self.output_dir / "state.json")).expanduser().resolve()
        self.feed_batch_size = int(config.get("feed_batch_size", 30))
        self.article_batch_size = min(int(config.get("article_batch_size", 20)), 100)
        self.poll_interval_seconds = int(config.get("poll_interval_seconds", 600))
        self.feed_ids = set(config.get("feed_ids", []) or [])
        self.request_timeout = int(config.get("request_timeout", 30))
        self.max_feeds = config.get("max_feeds")
        self.max_articles_per_feed = config.get("max_articles_per_feed")

        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "wemp-markdown-sync/1.0",
                "Accept": "application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
            }
        )
        self.cos_cfg = config.get("tencent_cos") or {}
        self.state = self._load_state()

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"articles": {}, "feeds": {}, "last_full_sync_at": None}
        with self.state_path.open("r", encoding="utf-8") as fh:
            return json.load(fh)

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        with self.state_path.open("w", encoding="utf-8") as fh:
            json.dump(self.state, fh, ensure_ascii=False, indent=2)

    def _get_xml(self, path: str, params: dict[str, Any] | None = None) -> ET.Element:
        response = self.session.get(
            urljoin(f"{self.base_url}/", path.lstrip("/")),
            params=params,
            timeout=self.request_timeout,
        )
        response.raise_for_status()
        return ET.fromstring(response.content)

    def list_feeds(self, refresh: bool = False) -> list[FeedMeta]:
        feeds: list[FeedMeta] = []
        offset = 0
        path = "/rss/fresh" if refresh else "/rss"

        while True:
            root = self._get_xml(path, {"limit": self.feed_batch_size, "offset": offset})
            items = root.findall("./channel/item")
            if not items:
                break

            for item in items:
                feed_id = (item.findtext("id") or "").strip()
                if not feed_id:
                    continue
                if self.feed_ids and feed_id not in self.feed_ids:
                    continue
                feeds.append(
                    FeedMeta(
                        feed_id=feed_id,
                        title=(item.findtext("title") or feed_id).strip(),
                        description=(item.findtext("description") or "").strip(),
                    )
                )
                if self.max_feeds and len(feeds) >= int(self.max_feeds):
                    return feeds
            offset += self.feed_batch_size
        return feeds

    def list_feed_articles(self, feed_id: str, refresh: bool, full_scan: bool) -> Iterator[dict[str, Any]]:
        offset = 0
        yielded_count = 0

        while True:
            page_limit = self.article_batch_size
            if self.max_articles_per_feed:
                remaining = int(self.max_articles_per_feed) - yielded_count
                if remaining <= 0:
                    return
                page_limit = min(page_limit, remaining)

            root = self._get_xml(
                f"/feed/{feed_id}.rss",
                {
                    "limit": page_limit,
                    "offset": offset,
                    "is_update": str(refresh).lower(),
                },
            )
            items = root.findall("./channel/item")
            if not items:
                break

            for item in items:
                article = self._parse_article_item(feed_id, item)
                if not article["article_id"]:
                    continue
                yield article
                yielded_count += 1
                if self.max_articles_per_feed and yielded_count >= int(self.max_articles_per_feed):
                    return
            if not full_scan:
                break
            offset += self.article_batch_size

    def _parse_article_item(self, feed_id: str, item: ET.Element) -> dict[str, Any]:
        content_el = item.find("content:encoded", CONTENT_NS)
        content_html = content_el.text if content_el is not None and content_el.text else ""
        if not content_html.strip():
            content_html = item.findtext("description") or ""
            
        enclosure = item.find("enclosure")
        return {
            "feed_id": feed_id,
            "article_id": (item.findtext("id") or "").strip(),
            "title": (item.findtext("title") or "").strip(),
            "description": (item.findtext("description") or "").strip(),
            "guid": (item.findtext("guid") or "").strip(),
            "pub_date": (item.findtext("pubDate") or "").strip(),
            "cover": enclosure.attrib.get("url", "").strip() if enclosure is not None else "",
            "content_html": content_html,
        }


    def sync_all(self, refresh_feeds: bool = False) -> tuple[int, int]:
        feeds = self.list_feeds(refresh=refresh_feeds)
        total_saved = 0
        total_seen = 0
        for idx, feed in enumerate(feeds, start=1):
            print(f"[{idx}/{len(feeds)}] syncing feed {feed.title} ({feed.feed_id})", flush=True)
            self.state["feeds"][feed.feed_id] = {
                "title": feed.title,
                "description": feed.description,
                "last_synced_at": now_iso(),
            }
            articles = self.list_feed_articles(feed.feed_id, refresh=False, full_scan=True)
            for article in articles:
                total_seen += 1
                if self._save_article(feed, article):
                    total_saved += 1
            self._save_state()

        self.state["last_full_sync_at"] = now_iso()
        self._save_state()
        return total_seen, total_saved

    def sync_incremental(self) -> tuple[int, int]:
        feeds = self.list_feeds(refresh=True)
        total_seen = 0
        total_saved = 0
        for feed in feeds:
            articles = self.list_feed_articles(feed.feed_id, refresh=True, full_scan=False)
            self.state["feeds"][feed.feed_id] = {
                "title": feed.title,
                "description": feed.description,
                "last_polled_at": now_iso(),
            }
            for article in articles:
                total_seen += 1
                if self._save_article(feed, article):
                    total_saved += 1
        self._save_state()
        return total_seen, total_saved

    def _save_article(self, feed: FeedMeta, article: dict[str, Any]) -> bool:
        article_id = article["article_id"]
        state_article = self.state["articles"].get(article_id)
        rss_content = article.get("content_html", "").strip()

        if state_article:
            if len(rss_content) >= 200:
                if stable_hash(rss_content) == state_article.get("content_hash"):
                    return False
            else:
                return False
        
        if len(rss_content) < 200 and article.get("guid"):
            try:
                headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
                r = self.session.get(article["guid"], headers=headers, timeout=self.request_timeout)
                r.raise_for_status()
                article["content_html"] = r.text
            except Exception:
                pass
                
        current_hash = stable_hash(article.get("content_html", ""))
        if state_article and state_article.get("content_hash") == current_hash:
            return False
        article_dir = self.output_dir / sanitize_name(feed.title) / feed.feed_id
        article_dir.mkdir(parents=True, exist_ok=True)

        filename = build_filename(article["pub_date"], article["title"], article_id)
        target_path = article_dir / filename
        assets_dir = article_dir / f"{target_path.stem}__assets"
        markdown = build_markdown(self.session, feed, article, target_path, assets_dir, self.request_timeout, self.cos_cfg)
        target_path.write_text(markdown, encoding="utf-8")

        self.state["articles"][article_id] = {
            "feed_id": feed.feed_id,
            "feed_title": feed.title,
            "title": article["title"],
            "guid": article["guid"],
            "pub_date": article["pub_date"],
            "file_path": str(target_path),
            "assets_dir": str(assets_dir),
            "content_hash": current_hash,
            "saved_at": now_iso(),
        }
        return True


def stable_hash(content: str) -> str:
    import hashlib

    safe_content = content or ""
    return hashlib.sha1(safe_content.encode("utf-8")).hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def sanitize_name(name: str) -> str:
    name = re.sub(r"[\\/:*?\"<>|]+", "_", name).strip()
    name = re.sub(r"\s+", " ", name)
    # 将 120 缩短为 50，避免中文字符导致超 255 bytes 限制
    return name[:50] or "untitled"



def slugify_title(title: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|]+", " ", title).strip()
    cleaned = re.sub(r"\s+", "-", cleaned)
    # 将 100 缩短为 50，保留前 50 个字符作为标题足以辨认
    return cleaned[:50] or "article"



def parse_pub_date(pub_date: str) -> str:
    if not pub_date:
        return "unknown-date"
    try:
        dt = parsedate_to_datetime(pub_date)
        return dt.astimezone().strftime("%Y-%m-%d")
    except Exception:
        return "unknown-date"


def build_filename(pub_date: str, title: str, article_id: str) -> str:
    return f"{parse_pub_date(pub_date)}__{slugify_title(title)}__{sanitize_name(article_id)}.md"


def build_markdown(
    session: requests.Session,
    feed: FeedMeta,
    article: dict[str, Any],
    target_path: Path,
    assets_dir: Path,
    request_timeout: int,
    cos_cfg: dict[str, Any] | None = None,
) -> str:
    body_html = extract_article_html(article.get("content_html", ""))
    markdown_body = reflow_markdown(convert_html_to_markdown(body_html))
    markdown_body = localize_markdown_images(session, markdown_body, target_path, assets_dir, request_timeout, cos_cfg)
    metadata = {
        "feed_id": feed.feed_id,
        "feed_title": feed.title,
        "article_id": article["article_id"],
        "title": article["title"],
        "source_url": article["guid"],
        "published_at": article["pub_date"],
        "cover_image": article["cover"],
        "saved_at": now_iso(),
    }
    frontmatter = yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False).strip()
    frontmatter = f"---\n{frontmatter}\n---\n"
    header = f"# {article['title']}\n\n"
    source_lines = [
        f"- 公众号：{feed.title}",
        f"- 发布时间：{article['pub_date'] or '未知'}",
        f"- 原文链接：{article['guid'] or '未知'}",
    ]
    if article.get("cover"):
        source_lines.append(f"- 封面图：{article['cover']}")
    if assets_dir.exists():
        source_lines.append(f"- 本地资源目录：{assets_dir.name}")
    source_block = "\n".join(source_lines) + "\n\n"
    return frontmatter + "\n" + header + source_block + markdown_body.strip() + "\n"


def extract_article_html(full_html: str) -> str:
    if not full_html:
        return ""
    soup = BeautifulSoup(full_html, "html.parser")
    # 微信公众号常用的内容容器
    for selector in [
        "#js_content",
        ".rich_media_content",
        "#img-content",
        "#activity-detail",
        "article",
    ]:
        target = soup.select_one(selector)
        if target:
            # 仅删除脚本和 iframe 等危险或多余的标签，保留 style 和 svg
            for tag in target.select("script, iframe, noscript"):
                tag.decompose()
            
            # 确保 img 标签有正确的 src
            for img in target.find_all("img"):
                if img.get("data-src") and not img.get("src"):
                    img["src"] = img.get("data-src")
            
            # 如果原页面有 style 标签，尝试把它们也带入（可选）
            # 这里我们至少保留 target 内部的 style
            return str(target)
            
    # 如果找不到特定的容器，则清理 body
    for tag in soup.select("script, iframe, noscript"):
        tag.decompose()
    return str(soup.body if soup.body else soup)



def convert_html_to_markdown(html: str) -> str:
    if html_to_markdown is not None:
        text = html_to_markdown(
            html,
            heading_style="ATX",
            bullets="-",
            strip=["script", "style"],
        )
        return normalize_markdown(text)
    soup = BeautifulSoup(html, "html.parser")
    return normalize_markdown(render_node(soup).strip())


def render_node(node: Any, indent: int = 0) -> str:
    if isinstance(node, NavigableString):
        text = str(node)
        return collapse_inline_whitespace(text)
    if not isinstance(node, Tag):
        return ""

    if node.name in {"script", "style", "iframe", "noscript"}:
        return ""
    if node.name == "br":
        return "\n"
    if node.name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        level = int(node.name[1])
        return "\n" + "#" * level + " " + render_children(node, indent).strip() + "\n\n"
    if node.name in {"p", "div", "section", "article"}:
        content = render_children(node, indent).strip()
        return (content + "\n\n") if content else ""
    if node.name in {"strong", "b"}:
        content = render_children(node, indent).strip()
        return f"**{content}**" if content else ""
    if node.name in {"em", "i"}:
        content = render_children(node, indent).strip()
        return f"*{content}*" if content else ""
    if node.name == "a":
        text = render_children(node, indent).strip() or node.get("href", "").strip()
        href = node.get("href", "").strip()
        return f"[{text}]({href})" if href else text
    if node.name == "img":
        src = node.get("src") or node.get("data-src") or ""
        alt = node.get("alt", "").strip()
        return f"\n![{alt}]({src})\n\n" if src else ""
    if node.name == "ul":
        lines = []
        for li in node.find_all("li", recursive=False):
            item = render_children(li, indent + 2).strip().replace("\n", " ")
            if item:
                lines.append(" " * indent + f"- {item}")
        return "\n".join(lines) + "\n\n" if lines else ""
    if node.name == "ol":
        lines = []
        for idx, li in enumerate(node.find_all("li", recursive=False), start=1):
            item = render_children(li, indent + 3).strip().replace("\n", " ")
            if item:
                lines.append(" " * indent + f"{idx}. {item}")
        return "\n".join(lines) + "\n\n" if lines else ""
    if node.name == "table":
        return render_table(node) + "\n\n"
    if node.name in {"span", "font", "blockquote", "tbody", "thead", "tr", "td", "th"}:
        return render_children(node, indent)
    return render_children(node, indent)


def render_children(node: Tag, indent: int = 0) -> str:
    return "".join(render_node(child, indent) for child in node.children)


def collapse_inline_whitespace(text: str) -> str:
    if not text.strip():
        return " " if "\n" not in text else "\n"
    return re.sub(r"[ \t\r\f\v]+", " ", text)


def render_table(table: Tag) -> str:
    rows = []
    for tr in table.find_all("tr"):
        cells = []
        for cell in tr.find_all(["th", "td"], recursive=False):
            cell_text = normalize_markdown(render_children(cell).strip()).replace("\n", " ")
            cells.append(cell_text or " ")
        if cells:
            rows.append(cells)
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    rows = [row + [" "] * (width - len(row)) for row in rows]
    header = rows[0]
    sep = ["---"] * width
    body = rows[1:]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(sep) + " |",
    ]
    for row in body:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def normalize_markdown(text: str) -> str:
    text = text.replace("\xa0", " ")
    text = re.sub(r"\n{3,}", "\n\n", text)
    lines = [line.rstrip() for line in text.splitlines()]
    return "\n".join(lines).strip() + "\n"


def reflow_markdown(text: str) -> str:
    blocks = [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]
    out: list[str] = []
    buffer: list[str] = []

    def flush_buffer() -> None:
        if not buffer:
            return
        joined = " ".join(part.strip() for part in buffer if part.strip())
        joined = cleanup_inline_spacing(joined)
        if joined:
            out.append(joined)
        buffer.clear()

    for block in blocks:
        if is_structural_block(block):
            flush_buffer()
            out.append(block)
            continue
        buffer.append(block)
        if ends_sentence(block):
            flush_buffer()

    flush_buffer()
    return "\n\n".join(out).strip() + "\n"


def is_structural_block(block: str) -> bool:
    stripped = block.strip()
    if not stripped:
        return True
    if stripped.startswith(("#", "- ", "* ", "> ", "| ")):
        return True
    if re.match(r"^\d+\.\s", stripped):
        return True
    if stripped.startswith("![](") or stripped.startswith("!["):
        return True
    if re.fullmatch(r"\*\*[^*]+\*\*", stripped):
        return True
    return False


def ends_sentence(block: str) -> bool:
    stripped = block.strip()
    return bool(stripped) and stripped[-1] in "。！？；：:.!?;)]）】」』\"”"


def cleanup_inline_spacing(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([，。！？；：,.!?;:])", r"\1", text)
    text = re.sub(r"([（【“‘])\s+", r"\1", text)
    text = re.sub(r"\s+([）】”’])", r"\1", text)
    text = re.sub(r"(\d)\s+(年|月|日|天|周|季|亿元|亿|万亿|bp|BPs|%|个)", r"\1\2", text)
    text = re.sub(r"(年|月|日|天|周|季)\s+(\d)", r"\1\2", text)
    text = re.sub(r"([A-Za-z])\s+([A-Za-z])", r"\1\2", text)
    text = re.sub(r"(\d)\s*-\s*(\d)", r"\1-\2", text)
    text = re.sub(r"\s+/", "/", text)
    text = re.sub(r"/\s+", "/", text)
    return text


def build_cos_client(cos_cfg: dict[str, Any]):
    """初始化腾讯云 COS 客户端，返回 (client, bucket, region)"""
    try:
        from qcloud_cos import CosConfig, CosS3Client
    except ImportError:
        raise SystemExit("请先安装：pip install cos-python-sdk-v5")
    config = CosConfig(
        Region=cos_cfg["region"],
        SecretId=cos_cfg["secret_id"],
        SecretKey=cos_cfg["secret_key"],
    )
    return CosS3Client(config), cos_cfg["bucket"], cos_cfg["region"]


def upload_image_to_cos(
    session: requests.Session,
    url: str,
    cos_client,
    bucket: str,
    region: str,
    cos_key: str,
    request_timeout: int,
) -> str | None:
    """下载图片后直接上传到 COS，返回 COS URL，失败返回 None"""
    try:
        response = session.get(url, timeout=request_timeout, stream=True)
        response.raise_for_status()
        data = response.content
        cos_client.put_object(Bucket=bucket, Key=cos_key, Body=data)
        return f"https://{bucket}.cos.{region}.myqcloud.com/{cos_key}"
    except Exception:
        return None


def localize_markdown_images(
    session: requests.Session,
    markdown: str,
    target_path: Path,
    assets_dir: Path,
    request_timeout: int,
    cos_cfg: dict[str, Any] | None = None,
) -> str:
    pattern = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
    matches = list(pattern.finditer(markdown))
    if not matches:
        return markdown

    # 判断是否启用 COS
    use_cos = cos_cfg and cos_cfg.get("enabled")
    cos_client = bucket = region = None
    if use_cos:
        cos_client, bucket, region = build_cos_client(cos_cfg)

    cache: dict[str, str] = {}
    image_index = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal image_index
        alt_text = match.group(1)
        raw_url = match.group(2).strip()
        if not raw_url.startswith(("http://", "https://")):
            return match.group(0)
        if raw_url in cache:
            return f"![{alt_text}]({cache[raw_url]})"

        image_index += 1
        suffix = guess_image_suffix(raw_url, "")

        if use_cos:
            # 上传到 COS，key 用文章路径保持可追溯
            rel_dir = assets_dir.relative_to(assets_dir.parent.parent.parent.parent) \
                if assets_dir.parts else assets_dir
            cos_key = f"wemp-images/{target_path.parent.name}/{target_path.stem}__assets/img_{image_index:03d}{suffix}"
            result = upload_image_to_cos(
                session, raw_url, cos_client, bucket, region, cos_key, request_timeout
            )
            if result is None:
                cache[raw_url] = raw_url
                return match.group(0)
            cache[raw_url] = result
            return f"![{alt_text}]({result})"
        else:
            # 原有逻辑：下载到本地
            assets_dir.mkdir(parents=True, exist_ok=True)
            local_path = download_image(session, raw_url, assets_dir, image_index, request_timeout)
            if local_path is None:
                cache[raw_url] = raw_url
                return match.group(0)
            relative_path = local_path.relative_to(target_path.parent).as_posix()
            cache[raw_url] = relative_path
            return f"![{alt_text}]({relative_path})"

    localized = pattern.sub(replace, markdown)
    # 本地模式下清理空 assets 目录
    if not use_cos and assets_dir.exists() and not any(assets_dir.iterdir()):
        assets_dir.rmdir()
    return localized


def download_image(
    session: requests.Session,
    url: str,
    assets_dir: Path,
    index: int,
    request_timeout: int,
) -> Path | None:
    try:
        response = session.get(url, timeout=request_timeout, stream=True)
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
        suffix = guess_image_suffix(url, content_type)
        target = assets_dir / f"img_{index:03d}{suffix}"
        with target.open("wb") as fh:
            for chunk in response.iter_content(chunk_size=1024 * 64):
                if chunk:
                    fh.write(chunk)
        return target
    except Exception:
        return None


def guess_image_suffix(url: str, content_type: str) -> str:
    parsed = urlparse(url)
    suffix = Path(parsed.path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg"}:
        return suffix
    guessed = mimetypes.guess_extension(content_type or "")
    if guessed:
        return ".jpg" if guessed == ".jpe" else guessed
    wx_fmt = re.search(r"wx_fmt=([a-zA-Z0-9]+)", url)
    if wx_fmt:
        ext = wx_fmt.group(1).lower()
        if ext == "jpeg":
            return ".jpg"
        return f".{ext}"
    return ".img"


def load_config(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync we-mp-rss articles into Markdown files.")
    parser.add_argument("--config", type=Path, help="YAML config file path")
    parser.add_argument("--base-url", help="we-mp-rss service base url")
    parser.add_argument("--output-dir", help="Markdown output directory")
    parser.add_argument("--state-path", help="State file path")
    parser.add_argument("--feed-id", action="append", dest="feed_ids", help="Only sync the specified feed_id")
    parser.add_argument("--poll-interval", type=int, help="Polling interval seconds for watch mode")
    parser.add_argument("--watch", action="store_true", help="Keep polling for new articles after the initial full sync")
    parser.add_argument("--skip-backfill", action="store_true", help="Skip initial historical sync")
    parser.add_argument("--incremental-once", action="store_true", help="Run one incremental sync and exit")
    parser.add_argument("--refresh-feeds", action="store_true", help="Refresh feed list before full sync")
    parser.add_argument("--max-feeds", type=int, help="Only sync the first N feeds, useful for testing")
    parser.add_argument("--max-articles-per-feed", type=int, help="Only sync the first N articles per feed, useful for testing")
    return parser.parse_args()


def merge_config(args: argparse.Namespace, file_config: dict[str, Any]) -> dict[str, Any]:
    config = dict(file_config)
    if args.base_url:
        config["base_url"] = args.base_url
    if args.output_dir:
        config["output_dir"] = args.output_dir
    if args.state_path:
        config["state_path"] = args.state_path
    if args.feed_ids:
        config["feed_ids"] = args.feed_ids
    if args.poll_interval:
        config["poll_interval_seconds"] = args.poll_interval
    if args.max_feeds is not None:
        config["max_feeds"] = args.max_feeds
    if args.max_articles_per_feed is not None:
        config["max_articles_per_feed"] = args.max_articles_per_feed
    if "base_url" not in config:
        raise SystemExit("--base-url 或配置文件里的 base_url 必填")
    return config


def main() -> int:
    args = parse_args()
    file_config = load_config(args.config)
    config = merge_config(args, file_config)
    syncer = WeMpRssSync(config)

    if args.incremental_once:
        seen, saved = syncer.sync_incremental()
        print(f"incremental sync complete: seen={seen}, saved={saved}", flush=True)
        return 0

    if not args.skip_backfill:
        seen, saved = syncer.sync_all(refresh_feeds=args.refresh_feeds)
        print(f"historical sync complete: seen={seen}, saved={saved}", flush=True)

    if not args.watch:
        return 0

    print(f"watch mode started, polling every {syncer.poll_interval_seconds}s", flush=True)
    while True:
        try:
            seen, saved = syncer.sync_incremental()
            print(f"incremental sync: seen={seen}, saved={saved}, at={now_iso()}", flush=True)
        except KeyboardInterrupt:
            print("stopped by user", flush=True)
            return 0
        except Exception as exc:
            print(f"incremental sync failed: {exc}", file=sys.stderr, flush=True)
        time.sleep(syncer.poll_interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
