#!/usr/bin/env python3
"""
初始化本地 wemp_data.db SQLite 数据库
"""
import sqlite3
import argparse
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS wemp_articles (
    article_id    TEXT PRIMARY KEY,
    mp_id         TEXT NOT NULL,
    mp_name       TEXT,
    title         TEXT,
    source_url    TEXT,
    published_at  INTEGER,
    cover_cos     TEXT,        -- COS 封面图链接
    cover_wx      TEXT,        -- 微信原始封面图链接

    content_html  TEXT,        -- 替换了 COS 图片链接的 HTML（保留微信链接在 data-wx 属性）
    content_md    TEXT,        -- 转换后的 Markdown（图片为 COS 链接）
    content_clean TEXT,        -- 清洗后纯文本（用于 embedding）

    md_converted  INTEGER DEFAULT 0,   -- 1=已完成 markdown 转换
    embedded      INTEGER DEFAULT 0,   -- 1=已完成 embedding 写入 DolphinDB

    created_at    TEXT,
    updated_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_mp_id ON wemp_articles(mp_id);
CREATE INDEX IF NOT EXISTS idx_published_at ON wemp_articles(published_at);
CREATE INDEX IF NOT EXISTS idx_md_converted ON wemp_articles(md_converted);
CREATE INDEX IF NOT EXISTS idx_embedded ON wemp_articles(embedded);
"""


def init_db(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
    print(f"✅ 数据库初始化完成：{db_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="初始化 wemp_data.db")
    parser.add_argument("--db", type=Path, default=Path("./wemp_data.db"))
    args = parser.parse_args()
    init_db(args.db)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
