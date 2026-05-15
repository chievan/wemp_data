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

        total = api.list_articles(0, 1)["total"]
        log_to_db(f"Total articles in API: {total}")

        counts = {"ok": 0, "skipped": 0, "no_content": 0, "error": 0}
        processed = 0
        offset = 0
        page_size = 50
        request_timeout = 30

        while True:
            page = api.list_articles(offset, page_size)
            items = page["list"]
            if not items:
                break

            for item in items:
                if limit and processed >= limit:
                    break

                aid = item["id"]
                article = api.get_article(aid)
                if not article:
                    counts["error"] += 1
                    processed += 1
                    continue
                
                for key in ["mp_name", "mp_id", "title", "publish_time", "url", "pic_url"]:
                    if key in item and (key not in article or not article[key]):
                        article[key] = item[key]

                try:
                    result = old_ingest.process_article(
                        article, dest_conn, img_sess,
                        cos_client, bucket, region,
                        ddb_sess, cfg,
                        request_timeout, force, skip_ddb
                    )
                    counts[result] += 1
                except Exception as e:
                    counts["error"] += 1
                    log_to_db(f"Error processing {item.get('title', aid)}: {e}")

                processed += 1
                if processed % 5 == 0:
                    log_to_db(f"Progress: {processed}/{limit or total} (ok={counts['ok']}, skipped={counts['skipped']}, err={counts['error']})")

            offset += page_size
            if limit and processed >= limit:
                break
            if offset >= total:
                break
                
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
