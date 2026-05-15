import asyncio
import json
import logging
import sqlite3
import traceback
from pathlib import Path

# Add core to sys.path is not needed since we run from project root and can import core.ingest_service
import core.ingest_service as old_ingest
from api.core.config import settings, _yaml_cfg
from api.core.database import SessionLocal
from api.models.task import IngestTask
from core.logger import ingest_logger as logger

def run_ingest_sync(task_id: int, limit: int = 0, force: bool = False, skip_ddb: bool = False):
    """
    Synchronous function that runs the old ingest pipeline logic.
    We pass in the original YAML config dictionary because old_ingest heavily relies on it.
    """
    db_session = SessionLocal()
    task = db_session.query(IngestTask).filter(IngestTask.id == task_id).first()
    if not task:
        db_session.close()
        return

    def log_to_db(msg: str):
        # Helper to append logs to the DB for frontend to see
        logger.info(msg)
        try:
            current_logs = task.logs or ""
            task.logs = current_logs + msg + "\n"
            db_session.commit()
        except Exception:
            pass

    log_to_db(f"Starting ingest task {task_id}. limit={limit}, force={force}, skip_ddb={skip_ddb}")

    try:
        cfg = _yaml_cfg
        cos_cfg = cfg.get("tencent_cos", {})
        if not cos_cfg.get("enabled"):
            raise ValueError("tencent_cos.enabled is not true in config")

        cos_client, bucket, region = old_ingest.build_cos_client(cos_cfg)
        
        import requests
        img_sess = requests.Session()
        img_sess.headers["User-Agent"] = "Mozilla/5.0"

        # Database connections
        # We reuse the raw sqlite3 connection for compatibility with old_ingest.py
        db_path = settings.DATABASE_URL.replace("sqlite:///", "")
        old_ingest.init_db(Path(db_path))
        dest_conn = sqlite3.connect(db_path)
        dest_conn.row_factory = sqlite3.Row

        # API setup
        # The base URL should be loaded from config
        api_url = cfg.get("base_url", "http://localhost:8001")
        
        # Wemp API user credentials (from config)
        wemp_creds = cfg.get("we_mp_rss", {})
        ak = wemp_creds.get("access_key")
        sk = wemp_creds.get("secret_key")
        basic_auth = wemp_creds.get("basic_auth", "")
        api = old_ingest.WempApi(api_url, access_key=ak, secret_key=sk, basic_auth=basic_auth)

        ddb_sess = None
        if not skip_ddb:
            try:
                ddb_sess = old_ingest.connect_ddb(cfg)
                log_to_db("DolphinDB connected successfully.")
            except Exception as e:
                log_to_db(f"DolphinDB connection failed: {e}")

        # ─── 阶段 1: 批量获取所有 API 文章 ID（只拿 ID，不做详情请求）───
        log_to_db("Fetching all article IDs from API...")
        all_api_ids = set()
        api_id_to_item = {}  # id -> item 映射，用于补充 get_article 缺失的字段
        offset = 0
        page_size = 100  # API limit=500 返回 422，用 100
        while True:
            page = api.list_articles(offset, page_size)
            items = page.get("list", [])
            if not items:
                break
            for item in items:
                all_api_ids.add(item["id"])
                api_id_to_item[item["id"]] = item
            if len(items) < page_size:
                break
            offset += page_size
        log_to_db(f"Total articles in API: {len(all_api_ids)}")

        # ─── 阶段 2: 查本地已存在的 ID ───
        local_existing = set()
        cursor = dest_conn.execute("SELECT article_id FROM wemp_articles WHERE md_converted = 1")
        for row in cursor:
            local_existing.add(row[0])
        log_to_db(f"Already in local DB: {len(local_existing)}")

        # ─── 阶段 3: 差集 = 需要处理的文章 ID ───
        if not force:
            ids_to_process = all_api_ids - local_existing
            counts = {"ok": 0, "skipped": len(local_existing & all_api_ids), "no_content": 0, "error": 0}
        else:
            ids_to_process = all_api_ids
            counts = {"ok": 0, "skipped": 0, "no_content": 0, "error": 0}
        log_to_db(f"Need to process: {len(ids_to_process)} (skipped: {counts['skipped']})")

        # ─── 阶段 4: 只拉取需要处理的文章详情 ───
        processed = 0
        request_timeout = 30
        # 按 ID 排序，保持稳定的处理顺序
        sorted_ids = sorted(ids_to_process)
        if limit:
            sorted_ids = sorted_ids[:limit]

        for aid in sorted_ids:
            article = api.get_article(aid)
            if not article:
                counts["error"] += 1
                list_item = api_id_to_item.get(aid, {})
                title = list_item.get("title", aid)
                url = list_item.get("url", "")
                log_to_db(f"❌ 拉取失败: {title[:50]} | {url}")
                processed += 1
                continue

            # 补充 list 接口中可能有的字段
            # 补充 list 接口中的字段（get_article 可能缺失）
            list_item = api_id_to_item.get(aid, {})
            for key in ["mp_name", "mp_id", "title", "publish_time", "url", "pic_url"]:
                if key in list_item and (key not in article or not article[key]):
                    article[key] = list_item[key]

            try:
                result = old_ingest.process_article(
                    article, dest_conn, img_sess,
                    cos_client, bucket, region,
                    ddb_sess, cfg,
                    request_timeout, force, skip_ddb
                )
                counts[result] += 1
                if result == "ok":
                    title = article.get("title", aid)
                    log_to_db(f"✅ {title[:50]}")
            except Exception as e:
                counts["error"] += 1
                log_to_db(f"❌ Error processing {article.get('title', aid)}: {e}")

            processed += 1
            if processed % 2000 == 0 or processed == len(sorted_ids):
                log_to_db(f"Progress: {processed}/{len(sorted_ids)} (ok={counts['ok']}, no_content={counts['no_content']}, skipped={counts['skipped']}, err={counts['error']})")
                
        dest_conn.close()
        log_to_db(f"Task Complete! ok={counts['ok']}, skipped={counts['skipped']}, no_content={counts['no_content']}, err={counts['error']}")

    except Exception as e:
        error_trace = traceback.format_exc()
        log_to_db(f"CRITICAL ERROR:\n{error_trace}")
        raise e
    finally:
        db_session.close()


async def execute_ingest_task(task_id: int, params_json: str):
    """
    Async wrapper to run the synchronous ingest task in a thread pool.
    """
    import json
    params = {}
    if params_json:
        try:
            params = json.loads(params_json)
        except:
            pass
            
    limit = params.get("limit", 0)
    force = params.get("force", False)
    skip_ddb = params.get("skip_ddb", False)
    
    await asyncio.to_thread(run_ingest_sync, task_id, limit, force, skip_ddb)
