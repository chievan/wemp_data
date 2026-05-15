from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional

from api.core.database import get_db
import sqlite3
from api.core.config import settings

router = APIRouter(prefix="/articles", tags=["articles"])

class ArticleMeta(BaseModel):
    article_id: str
    mp_name: str
    title: str
    source_url: str
    published_at: Optional[str]
    embedded: int

class ArticleListResponse(BaseModel):
    total: int
    items: List[ArticleMeta]

@router.get("", response_model=ArticleListResponse)
def get_articles(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    search: Optional[str] = None,
    embedded: Optional[int] = Query(None, description="Filter by embedding status: 1 for embedded, 0 for not")
):
    # Since the old schema doesn't have an SQLAlchemy model yet, we can just use raw SQLite here
    # or define the model. Raw SQL is fine for now.
    db_path = settings.DATABASE_URL.replace("sqlite:///", "")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    query = "SELECT article_id, mp_name, title, source_url, published_at, embedded FROM wemp_articles"
    count_query = "SELECT count(*) FROM wemp_articles"
    params = []
    where_clauses = []
    
    if search:
        where_clauses.append("(title LIKE ? OR mp_name LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])
    
    if embedded is not None:
        if embedded == 1:
            where_clauses.append("embedded = 1")
        else:
            where_clauses.append("(embedded IS NULL OR embedded = 0)")

    if where_clauses:
        clause = " WHERE " + " AND ".join(where_clauses)
        query += clause
        count_query += clause
        
    query += " ORDER BY published_at DESC LIMIT ? OFFSET ?"
    
    total = cursor.execute(count_query, params).fetchone()[0]
    
    params.extend([limit, skip])
    rows = cursor.execute(query, params).fetchall()
    
    items = []
    for row in rows:
        items.append(ArticleMeta(
            article_id=row["article_id"],
            mp_name=row["mp_name"],
            title=row["title"],
            source_url=row["source_url"],
            published_at=str(row["published_at"]) if row["published_at"] else None,
            embedded=row["embedded"] or 0
        ))
        
    conn.close()
    return ArticleListResponse(total=total, items=items)

@router.get("/stats")
def get_stats():
    import requests
    from api.core.config import _load_yaml_config
    import dolphindb as ddb
    
    # Dynamically load the latest config on every request
    _yaml_cfg = _load_yaml_config()
    
    stats = {
        "remote_total": 0,
        "local_total": 0,
        "embedded_total": 0,
        "md_converted": 0,
        "mp_count": 0,
        "api_status": "异常"
    }
    
    try:
        db_path = settings.DATABASE_URL.replace("sqlite:///", "")
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        stats["local_total"] = cursor.execute("SELECT count(*) FROM wemp_articles").fetchone()[0]
        stats["md_converted"] = cursor.execute("SELECT count(*) FROM wemp_articles WHERE content_md IS NOT NULL AND content_md != ''").fetchone()[0]
        stats["mp_count"] = cursor.execute("SELECT count(DISTINCT mp_name) FROM wemp_articles").fetchone()[0]
        conn.close()
    except Exception as e:
        print("Local stats error:", e)

    try:
        api_url = _yaml_cfg.get("base_url", "http://127.0.0.1:8001").rstrip("/")
        we_mp_rss_cfg = _yaml_cfg.get("we_mp_rss", {})
        ak = we_mp_rss_cfg.get("access_key")
        sk = we_mp_rss_cfg.get("secret_key")
        
        sess = requests.Session()
        if ak and sk:
            sess.headers["Authorization"] = f"AK-SK {ak}:{sk}"
        else:
            username = "admin"
            password = "123"
            basic_auth = we_mp_rss_cfg.get("basic_auth", "")
            resp = sess.post(f"{api_url}/api/v1/wx/auth/token", data={"grant_type": "password", "username": username, "password": password}, auth=("basic", basic_auth), timeout=5)
            if resp.status_code == 200:
                sess.headers["Authorization"] = f"Bearer {resp.json()['access_token']}"
        
        if "Authorization" in sess.headers:
            r = sess.get(f"{api_url}/api/v1/wx/articles", params={"offset": 0, "limit": 1}, timeout=5)
            if r.status_code == 200:
                stats["remote_total"] = r.json()["data"]["total"]
                stats["api_status"] = "正常"
            else:
                stats["api_status"] = "授权失败或拒接"
        else:
            stats["api_status"] = "无法获取 Token"
    except Exception as e:
        print("Remote stats error:", e)

    try:
        dcfg = _yaml_cfg.get("dolphindb", {})
        ddb_sess = ddb.session()
        ddb_sess.connect(host=settings.DDB_HOST, port=int(settings.DDB_PORT), userid=settings.DDB_USER, password=settings.DDB_PASSWORD)
        cnt = ddb_sess.run(f'count(exec distinct(article_id) from loadTable("{settings.DDB_DATABASE}", "{settings.DDB_CHUNKS_TABLE}"))')
        stats["embedded_total"] = int(cnt)
        ddb_sess.close()
    except Exception as e:
        print("DDB stats error:", e)
        
    return stats

@router.get("/{article_id}")
def get_article(article_id: str):
    db_path = settings.DATABASE_URL.replace("sqlite:///", "")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    row = cursor.execute("SELECT * FROM wemp_articles WHERE article_id = ?", (article_id,)).fetchone()
    conn.close()
    
    if not row:
        return {"error": "not found"}
        
    return dict(row)
