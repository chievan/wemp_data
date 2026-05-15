import os
from pathlib import Path
from pydantic_settings import BaseSettings
import re

# Calculate project root (assuming this file is in api/core/config.py)
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()

# --- 统一配置源：直接复用 brain.config 单例，避免重复解析 yaml ---
from brain.config import cfg as _brain_cfg

def _expand_env_vars(obj):
    """递归处理字典中的环境变量占位符 ${VAR:-default}"""
    if isinstance(obj, dict):
        return {k: _expand_env_vars(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_expand_env_vars(i) for i in obj]
    elif isinstance(obj, str):
        # 匹配 ${VAR:-default} 或 ${VAR}
        match = re.match(r'^\$\{(?P<var>[^:-]+)(?::-(?P<default>.*))?\}$', obj)
        if match:
            var_name = match.group('var')
            default_val = match.group('default') or ""
            return os.environ.get(var_name, default_val)
    return obj

# 兼容接口：供 articles.py、ingest_task_runner.py 等使用
_yaml_cfg = _expand_env_vars(_brain_cfg.get_all())

def _load_yaml_config() -> dict:
    """向后兼容：返回已展开环境变量的完整配置字典"""
    return _yaml_cfg


class Settings(BaseSettings):
    PROJECT_NAME: str = "Wemp Data API"
    API_V1_STR: str = "/api/v1"

    # SQLite
    DATABASE_URL: str = _yaml_cfg.get("database_url", "sqlite:///./data/wemp_data.db")

    # DolphinDB
    DDB_HOST: str = _yaml_cfg.get("dolphindb", {}).get("host", os.environ.get("WEMP_HOST", "localhost"))
    DDB_PORT: int = _yaml_cfg.get("dolphindb", {}).get("port", 8848)
    DDB_USER: str = _yaml_cfg.get("dolphindb", {}).get("user", "admin")
    DDB_PASSWORD: str = _yaml_cfg.get("dolphindb", {}).get("password", "")
    DDB_DATABASE: str = _yaml_cfg.get("dolphindb", {}).get("database", "dfs://wemp_vector")
    DDB_CHUNKS_TABLE: str = _yaml_cfg.get("dolphindb", {}).get("chunks_table", "wemp_chunks")

    # LLM & Embeddings
    DEEPSEEK_API_KEY: str = _yaml_cfg.get("api_keys", {}).get("deepseek", "")
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com/v1"

    EMBEDDING_API_KEY: str = _yaml_cfg.get("api_keys", {}).get("dashscope", "")
    EMBEDDING_BASE_URL: str = _yaml_cfg.get("embedding", {}).get("base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    EMBEDDING_MODEL: str = _yaml_cfg.get("embedding", {}).get("model", "text-embedding-v4")
    EMBEDDING_DIMENSION: int = _yaml_cfg.get("embedding", {}).get("dimension", 1024)

    class Config:
        case_sensitive = True

settings = Settings()
