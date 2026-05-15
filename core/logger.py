"""
统一日志管理模块 — 集中管理所有子系统的日志输出。
"""
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

_FMT = "%(asctime)s [%(name)s] %(levelname)s  %(message)s"
_DATE_FMT = "%Y-%m-%d %H:%M:%S"
_formatter = logging.Formatter(_FMT, datefmt=_DATE_FMT)


def get_logger(name: str, filename: str = None, level=logging.INFO) -> logging.Logger:
    """
    获取一个预配置的 Logger，同时输出到控制台和独立日志文件。

    Usage:
        from core.logger import get_logger
        logger = get_logger("ingest", "ingest.log")
        logger.info("Started ingesting...")
    """
    logger = logging.getLogger(name)
    # 使用模块级标记防止 handler 重复添加（比检查 handlers 更可靠）
    if getattr(logger, '_handlers_configured', False):
        return logger

    logger.setLevel(level)
    logger.propagate = False

    # Console handler
    ch = logging.StreamHandler()
    ch.setFormatter(_formatter)
    logger.addHandler(ch)

    # File handler (rotating, 50MB per file, keep 5 backups)
    if filename:
        fh = RotatingFileHandler(
            LOG_DIR / filename,
            maxBytes=50 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8"
        )
        fh.setFormatter(_formatter)
        logger.addHandler(fh)

    logger._handlers_configured = True
    return logger


# === 预定义的系统 Loggers ===
api_logger = get_logger("api", "api.log")
ingest_logger = get_logger("ingest", "ingest.log")
committee_logger = get_logger("committee", "committee.log")
vectorize_logger = get_logger("vectorize", "vectorize.log")
