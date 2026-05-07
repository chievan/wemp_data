from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from pydantic import BaseModel
import json
import os
import uuid
from datetime import datetime
from pypdf import PdfReader
import dolphindb as ddb

from api.core.database import get_db
from api.models.task import IngestTask
from api.core.embeddings import embeddings
from api.core.dolphindb_vectorstore import DolphinDBVectorStore
from api.core.config import settings, PROJECT_ROOT
from core.logger import api_logger
import hashlib

router = APIRouter(prefix="/ingest", tags=["ingest"])

class IngestRequest(BaseModel):
    limit: int = 0
    force: bool = False
    skip_ddb: bool = False

@router.post("/start")
def start_ingest(request: IngestRequest, db: Session = Depends(get_db)):
    existing_task = db.query(IngestTask).filter(
        IngestTask.status.in_(["pending", "running"])
    ).first()
    
    if existing_task:
        raise HTTPException(status_code=400, detail="A task is already running or pending.")
    
    new_task = IngestTask(
        task_type="ingest",
        status="pending",
        params=json.dumps(request.model_dump())
    )
    db.add(new_task)
    db.commit()
    db.refresh(new_task)
    
    return {"message": "Ingest task queued successfully", "task_id": new_task.id}

@router.post("/start_vectorize")
def start_vectorize(db: Session = Depends(get_db)):
    existing_task = db.query(IngestTask).filter(
        IngestTask.status.in_(["pending", "running"])
    ).first()
    
    if existing_task:
        raise HTTPException(status_code=400, detail="A task is already running or pending.")
    
    new_task = IngestTask(
        task_type="vectorize",
        status="pending",
        params="{}"
    )
    db.add(new_task)
    db.commit()
    db.refresh(new_task)
    
    return {"message": "Vectorize task queued successfully", "task_id": new_task.id}

@router.get("/status")
def get_ingest_status(db: Session = Depends(get_db)):
    # Return the latest task
    task = db.query(IngestTask).order_by(IngestTask.created_at.desc()).first()
    if not task:
        return {"status": "idle"}
    
    return {
        "task_id": task.id,
        "task_type": task.task_type,
        "status": task.status,
        "created_at": task.created_at,
        "completed_at": task.completed_at
    }
    
@router.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    """上传并向量化单篇研报/文档"""
    try:
        filename = file.filename
        ext = os.path.splitext(filename)[1].lower()
        
        # 1. 读取内容
        file_content = await file.read()
        
        text_content = ""
        markdown_content = ""
        
        if ext == ".pdf":
            import pdfplumber
            import io
            with pdfplumber.open(io.BytesIO(file_content)) as pdf:
                for page in pdf.pages:
                    # 提取文本
                    page_text = page.extract_text() or ""
                    
                    # 尝试提取表格
                    tables = page.extract_tables()
                    table_md = ""
                    if tables:
                        for table in tables:
                            # 将表格转换为 Markdown 格式
                            if not table or not any(table): continue
                            headers = table[0]
                            rows = table[1:]
                            # 过滤掉全为空的行
                            rows = [r for r in rows if any(r)]
                            if not rows and not any(headers): continue
                            
                            md = "| " + " | ".join([str(h).replace("\n", " ") if h else "" for h in headers]) + " |\n"
                            md += "| " + " | ".join(["---"] * len(headers)) + " |\n"
                            for row in rows:
                                md += "| " + " | ".join([str(c).replace("\n", " ") if c else "" for c in row]) + " |\n"
                            table_md += "\n" + md + "\n"
                    
                    text_content += page_text + "\n"
                    markdown_content += page_text + "\n" + table_md + "\n"
                    
        elif ext in [".txt", ".md"]:
            text_content = file_content.decode("utf-8")
            markdown_content = text_content
        else:
            raise HTTPException(status_code=400, detail="Unsupported file type.")
        
        if not text_content.strip():
            raise HTTPException(status_code=400, detail="File is empty or could not extract text.")

        # 2. 分片
        from langchain_text_splitters import RecursiveCharacterTextSplitter
        text_splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=100)
        chunks = text_splitter.split_text(text_content)
        
        # 3. 准备向量库
        sess = ddb.session()
        sess.connect(host=settings.DDB_HOST, port=int(settings.DDB_PORT), userid=settings.DDB_USER, password=settings.DDB_PASSWORD)
        vector_store = DolphinDBVectorStore(
            session=sess, 
            embedding=embeddings, 
            database_path=settings.DDB_DATABASE, 
            table_name=settings.DDB_CHUNKS_TABLE
        )
        
        # 4. 写入 DDB
        article_id = f"up_{uuid.uuid4().hex[:8]}"
        
        # 保存文件到磁盘
        upload_dir = os.path.join(PROJECT_ROOT, "data", "uploads")
        os.makedirs(upload_dir, exist_ok=True)
        file_path = os.path.join(upload_dir, f"{article_id}{ext}")
        
        with open(file_path, "wb") as f:
            f.write(file_content)

        metadatas = [
            {
                "article_id": article_id,
                "title": filename,
                "mp_name": "用户上传",
                "pub_time": datetime.now().isoformat(),
                "chunk_no": i + 1,
            }
            for i in range(len(chunks))
        ]
        
        vector_store.add_texts(chunks, metadatas=metadatas)
        sess.close()

        # 5. 同步到 SQLite 数据库，以便在列表中显示
        try:
            db = next(get_db())
            from sqlalchemy import text
            now_ts = int(datetime.now().timestamp())
            sql = text("""
                INSERT INTO wemp_articles 
                (article_id, mp_id, mp_name, title, source_url, published_at, content_clean, content_md, md_converted, embedded, created_at, updated_at)
                VALUES (:aid, :mid, :mname, :title, :url, :pub, :content, :md, 1, 1, :now, :now)
            """)
            db.execute(sql, {
                "aid": article_id,
                "mid": "user_upload",
                "mname": "用户上传",
                "title": filename,
                "url": f"file://{file_path}",
                "pub": now_ts,
                "content": text_content[:1000], 
                "md": markdown_content,
                "now": now_ts
            })
            db.commit()
        except Exception as db_e:
            api_logger.error(f"Sync to SQLite failed: {db_e}")

        return {
            "status": "success", 
            "article_id": article_id, 
            "filename": filename, 
            "chunks": len(chunks),
            "save_path": file_path
        }
        
    except Exception as e:
        api_logger.error(f"Upload and vectorize failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
