"""
系统日志管理 API — 读取各子系统的日志文件。
"""
import os
from pathlib import Path
from fastapi import APIRouter, Query
from typing import Optional

router = APIRouter(prefix="/logs", tags=["logs"])

LOG_DIR = Path(__file__).parent.parent.parent / "logs"

# 允许查看的日志文件白名单
ALLOWED_LOGS = {
    "api": "api.log",
    "ingest": "ingest.log",
    "committee": "committee.log",
    "vectorize": "vectorize.log",
}


@router.get("/list")
def list_log_files():
    """列出所有可查看的日志文件及其大小"""
    result = []
    for key, filename in ALLOWED_LOGS.items():
        filepath = LOG_DIR / filename
        result.append({
            "key": key,
            "filename": filename,
            "exists": filepath.exists(),
            "size_kb": round(filepath.stat().st_size / 1024, 1) if filepath.exists() else 0,
            "modified": filepath.stat().st_mtime if filepath.exists() else 0
        })
    return result


@router.get("/read/{log_key}")
def read_log(log_key: str, tail: int = Query(200, ge=10, le=2000)):
    """读取指定日志文件的最后 N 行"""
    if log_key not in ALLOWED_LOGS:
        return {"error": f"未知日志: {log_key}", "lines": []}
    
    filepath = LOG_DIR / ALLOWED_LOGS[log_key]
    if not filepath.exists():
        return {"filename": ALLOWED_LOGS[log_key], "lines": [], "total_lines": 0}
    
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        
        total = len(all_lines)
        lines = [line.rstrip() for line in all_lines[-tail:]]
        return {
            "filename": ALLOWED_LOGS[log_key],
            "lines": lines,
            "total_lines": total,
            "showing": len(lines)
        }
    except Exception as e:
        return {"error": str(e), "lines": []}
