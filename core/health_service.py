#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

def expand_env(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: expand_env(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [expand_env(v) for v in obj]
    if isinstance(obj, str):
        match = re.match(r"\$\{(.*):-(.*)\}", obj)
        if match:
            env_var, default_val = match.groups()
            return os.environ.get(env_var, default_val)
        return os.path.expandvars(obj)
    return obj


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        import yaml
    except ImportError:
        return {}
    return expand_env(yaml.safe_load(path.read_text("utf-8")) or {})


def resolve_db_path(cfg: dict[str, Any], default_path: Path) -> Path:
    db_url = cfg.get("database_url", "")
    if db_url.startswith("sqlite:///"):
        db_path = Path(db_url.replace("sqlite:///", ""))
    else:
        db_path = default_path
    if not db_path.is_absolute():
        db_path = Path.cwd() / db_path
    return db_path


def format_pub_time(value: Any) -> str:
    if value is None or value == "":
        return "-"
    raw = str(value)
    try:
        ts = int(float(raw))
        if ts > 10_000_000_000:
            ts = ts // 1000
        if 1_000_000_000 <= ts <= 3_000_000_000:
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        pass
    return raw[:19]


def _scalar(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> int:
    value = conn.execute(sql, params).fetchone()[0]
    return int(value or 0)


def collect_sqlite_health(db_path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "db_path": str(db_path),
        "exists": db_path.exists(),
        "error": "",
        "summary": {},
        "top_mps": [],
        "recent_articles": [],
    }
    if not db_path.exists():
        result["error"] = "SQLite 数据库不存在"
        return result

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        tables = {
            row["name"]
            for row in conn.execute(
                "select name from sqlite_master where type = 'table'"
            ).fetchall()
        }
        if "wemp_articles" not in tables:
            result["error"] = "缺少 wemp_articles 表"
            return result

        total = _scalar(conn, "select count(*) from wemp_articles")
        md_done = _scalar(conn, "select count(*) from wemp_articles where md_converted = 1")
        embedded_done = _scalar(conn, "select count(*) from wemp_articles where embedded = 1")
        missing_mp = _scalar(
            conn,
            "select count(*) from wemp_articles where mp_name is null or trim(mp_name) = '' or mp_name = '未知'",
        )
        missing_title = _scalar(
            conn,
            "select count(*) from wemp_articles where title is null or trim(title) = ''",
        )
        missing_pub = _scalar(
            conn,
            "select count(*) from wemp_articles where published_at is null or trim(published_at) = ''",
        )
        missing_clean = _scalar(
            conn,
            "select count(*) from wemp_articles where content_clean is null or trim(content_clean) = ''",
        )
        with_cover = _scalar(
            conn,
            "select count(*) from wemp_articles where cover_cos is not null and trim(cover_cos) != ''",
        )

        pub_rows = conn.execute(
            """
            select published_at from wemp_articles
            where published_at is not null and trim(published_at) != ''
            order by cast(published_at as integer) asc
            """
        ).fetchall()
        oldest_pub = format_pub_time(pub_rows[0]["published_at"]) if pub_rows else "-"
        latest_pub = format_pub_time(pub_rows[-1]["published_at"]) if pub_rows else "-"

        result["summary"] = {
            "total_articles": total,
            "md_converted": md_done,
            "pending_md": max(total - md_done, 0),
            "embedded": embedded_done,
            "pending_embedding": max(total - embedded_done, 0),
            "missing_mp_name": missing_mp,
            "missing_title": missing_title,
            "missing_pub_time": missing_pub,
            "missing_clean_text": missing_clean,
            "with_cover": with_cover,
            "oldest_pub_time": oldest_pub,
            "latest_pub_time": latest_pub,
        }

        result["top_mps"] = [
            dict(row)
            for row in conn.execute(
                """
                select
                    coalesce(nullif(trim(mp_name), ''), '(缺失公众号)') as mp_name,
                    count(*) as article_count
                from wemp_articles
                group by coalesce(nullif(trim(mp_name), ''), '(缺失公众号)')
                order by article_count desc
                limit 15
                """
            ).fetchall()
        ]
        recent_rows = conn.execute(
            """
            select article_id, mp_name, title, published_at, md_converted, embedded
            from wemp_articles
            order by cast(published_at as integer) desc
            limit 12
            """
        ).fetchall()
        result["recent_articles"] = [
            {
                "article_id": row["article_id"],
                "mp_name": row["mp_name"] or "(缺失公众号)",
                "title": row["title"] or "(无标题)",
                "published_at": format_pub_time(row["published_at"]),
                "md_converted": int(row["md_converted"] or 0),
                "embedded": int(row["embedded"] or 0),
            }
            for row in recent_rows
        ]
    except Exception as exc:
        result["error"] = str(exc)
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return result


def collect_ddb_health(cfg: dict[str, Any]) -> dict[str, Any]:
    ddb_cfg = cfg.get("dolphindb", {})
    result: dict[str, Any] = {"enabled": bool(ddb_cfg), "error": "", "summary": {}}
    if not ddb_cfg:
        result["error"] = "config.yaml 中缺少 dolphindb 配置"
        return result
    try:
        import dolphindb as ddb

        sess = ddb.session()
        sess.connect(
            host=ddb_cfg["host"],
            port=int(ddb_cfg["port"]),
            userid=ddb_cfg["user"],
            password=ddb_cfg["password"],
        )
        db_path = ddb_cfg["database"]
        articles_table = ddb_cfg["articles_table"]
        chunks_table = ddb_cfg["chunks_table"]
        script = f"""
        result = dict(STRING, ANY)
        if(existsTable("{db_path}", `{articles_table})){{
            t = loadTable("{db_path}", `{articles_table})
            result["article_count"] = exec count(*) from t
            result["latest_pub_time"] = exec max(pub_time) from t
        }} else {{
            result["article_count"] = 0
            result["latest_pub_time"] = NULL
        }}
        if(existsTable("{db_path}", `{chunks_table})){{
            c = loadTable("{db_path}", `{chunks_table})
            result["chunk_count"] = exec count(*) from c
            mpNames = exec distinct(mp_name) from c
            result["mp_count"] = size(mpNames)
        }} else {{
            result["chunk_count"] = 0
            result["mp_count"] = 0
        }}
        result
        """
        result["summary"] = sess.run(script)
        sess.close()
    except Exception as exc:
        result["error"] = str(exc)
    return result


def collect_health(config_path: Path, db_path: Path, with_ddb: bool = False) -> dict[str, Any]:
    cfg = load_config(config_path)
    resolved_db_path = resolve_db_path(cfg, db_path)
    health = {
        "config_path": str(config_path),
        "sqlite": collect_sqlite_health(resolved_db_path),
    }
    if with_ddb:
        health["dolphindb"] = collect_ddb_health(cfg)
    return health


def render_text_report(health: dict[str, Any]) -> str:
    sqlite_info = health["sqlite"]
    lines = [
        "Wemp 数据健康检查",
        f"配置文件: {health['config_path']}",
        f"SQLite: {sqlite_info['db_path']}",
    ]
    if sqlite_info.get("error"):
        lines.append(f"错误: {sqlite_info['error']}")
        return "\n".join(lines)

    summary = sqlite_info["summary"]
    lines.extend(
        [
            "",
            "核心指标",
            f"- 文章总数: {summary['total_articles']}",
            f"- Markdown 已转换: {summary['md_converted']} / 待处理 {summary['pending_md']}",
            f"- Embedding 已标记: {summary['embedded']} / 待处理 {summary['pending_embedding']}",
            f"- 公众号缺失: {summary['missing_mp_name']}",
            f"- 标题缺失: {summary['missing_title']}",
            f"- 发布时间缺失: {summary['missing_pub_time']}",
            f"- 清洗文本缺失: {summary['missing_clean_text']}",
            f"- 封面图数量: {summary['with_cover']}",
            f"- 文章时间范围: {summary['oldest_pub_time']} -> {summary['latest_pub_time']}",
            "",
            "公众号分布 Top 15",
        ]
    )
    for row in sqlite_info["top_mps"]:
        lines.append(f"- {row['mp_name']}: {row['article_count']}")

    lines.append("")
    lines.append("最近文章")
    for row in sqlite_info["recent_articles"]:
        flags = f"md={row['md_converted']} emb={row['embedded']}"
        lines.append(f"- {row['published_at']} | {row['mp_name']} | {row['title']} ({flags})")

    ddb_info = health.get("dolphindb")
    if ddb_info:
        lines.extend(["", "DolphinDB"])
        if ddb_info.get("error"):
            lines.append(f"- 错误: {ddb_info['error']}")
        else:
            for key, value in ddb_info.get("summary", {}).items():
                lines.append(f"- {key}: {value}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="检查 Wemp 本地库与可选 DolphinDB 状态")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"), help="配置文件路径")
    parser.add_argument("--db", type=Path, default=Path("./data/wemp_data.db"), help="SQLite 数据库路径")
    parser.add_argument("--with-ddb", action="store_true", help="同时检查 DolphinDB 表计数")
    parser.add_argument("--json", action="store_true", help="输出 JSON，便于脚本解析")
    args = parser.parse_args()

    health = collect_health(args.config, args.db, with_ddb=args.with_ddb)
    if args.json:
        print(json.dumps(health, ensure_ascii=False, indent=2, default=str))
    else:
        print(render_text_report(health))
    return 1 if health["sqlite"].get("error") else 0


if __name__ == "__main__":
    raise SystemExit(main())
