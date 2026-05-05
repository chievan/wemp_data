#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

import dolphindb as ddb
import yaml


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


def build_init_script(cfg: dict[str, Any]) -> str:
    ddb_cfg = cfg["dolphindb"]
    embed_cfg = cfg["embedding"]
    db_path = ddb_cfg["database"]
    articles_table = ddb_cfg["articles_table"]
    chunks_table = ddb_cfg["chunks_table"]
    mp_profiles_table = ddb_cfg["mp_profiles_table"]
    topic_profiles_table = ddb_cfg["topic_profiles_table"]
    dim = int(embed_cfg["dimension"])
    index_type = str(embed_cfg.get("index_type", "hnsw")).lower()

    return f"""
dbPath = "{db_path}";
articlesTable = `{articles_table};
chunksTable = `{chunks_table};
mpProfilesTable = `{mp_profiles_table};
topicProfilesTable = `{topic_profiles_table};
vectorDim = {dim};
vectorIndexType = "{index_type}";

if(!existsDatabase(dbPath)){{
    months = 2020.01M..2035.12M;
    db = database(directory=dbPath, partitionType=VALUE, partitionScheme=months, engine="TSDB");
}} else {{
    db = database(dbPath);
}}

if(!existsTable(dbPath, articlesTable)){{
    schemaArticles = table(1:0,
        `pub_month`article_id`content_hash`mp_id`mp_name`title`pub_time`source_url`file_path`assets_dir`cover_image`topic_tags`content_clean`content_len`ingested_at,
        [MONTH, STRING, STRING, SYMBOL, STRING, STRING, TIMESTAMP, STRING, STRING, STRING, STRING, STRING, STRING, INT, TIMESTAMP]
    );
    createPartitionedTable(db, schemaArticles, articlesTable, `pub_month, sortColumns=`ingested_at, keepDuplicates=ALL);
}}

if(!existsTable(dbPath, chunksTable)){{
    schemaChunks = table(1:0,
        `pub_month`chunk_id`article_id`content_hash`mp_id`mp_name`title`pub_time`source_url`topic_tags`chunk_no`chunk_text`chunk_len`embedding`ingested_at,
        [MONTH, STRING, STRING, STRING, SYMBOL, STRING, STRING, TIMESTAMP, STRING, STRING, INT, STRING, INT, FLOAT[], TIMESTAMP]
    );
    createPartitionedTable(
        db,
        schemaChunks,
        chunksTable,
        `pub_month,
        sortColumns=`ingested_at,
        keepDuplicates=ALL,
        indexes={{"embedding":"vectorindex(type=" + vectorIndexType + ", dim=" + string(vectorDim) + ")"}}
    );
}}

if(!existsTable(dbPath, mpProfilesTable)){{
    schemaMpProfiles = table(1:0,
        `as_of_date`mp_id`mp_name`article_count`latest_pub_time`topic_summary`keyword_summary`profile_text`updated_at,
        [DATE, SYMBOL, STRING, INT, TIMESTAMP, STRING, STRING, STRING, TIMESTAMP]
    );
    createPartitionedTable(db, schemaMpProfiles, mpProfilesTable, `as_of_date, sortColumns=`updated_at, keepDuplicates=ALL);
}}

if(!existsTable(dbPath, topicProfilesTable)){{
    schemaTopicProfiles = table(1:0,
        `as_of_date`topic_name`article_count`mp_count`keyword_summary`profile_text`updated_at,
        [DATE, STRING, INT, INT, STRING, STRING, TIMESTAMP]
    );
    createPartitionedTable(db, schemaTopicProfiles, topicProfilesTable, `as_of_date, sortColumns=`updated_at, keepDuplicates=ALL);
}}

"OK";
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize DolphinDB VectorDB tables for we-mp-rss markdown data.")
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()

    cfg = load_config(args.config)
    ddb_cfg = cfg["dolphindb"]

    sess = ddb.session()
    sess.connect(
        host=ddb_cfg["host"],
        port=int(ddb_cfg["port"]),
        userid=ddb_cfg["user"],
        password=ddb_cfg["password"],
    )
    print(sess.run(build_init_script(cfg)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
