import yaml
import os
import re
from pathlib import Path

class Config:
    _instance = None
    _config_data = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(Config, cls).__new__(cls)
            cls._instance._load()
        return cls._instance

    def _load(self):
        # 寻找配置文件路径
        root = Path(__file__).parent.parent
        
        # 依次尝试可能的配置文件名
        candidates = [
            root / "config.yaml",
            root / "config.server.yaml",
            Path("config.yaml"),
            Path("config.server.yaml"),
            root / "config.example.yaml"
        ]
        
        config_path = None
        for p in candidates:
            if p.exists():
                config_path = p
                break
        
        if not config_path:
            raise FileNotFoundError(f"未找到任何配置文件 (config.yaml, config.server.yaml, etc.)")

        with open(config_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        # --- 环境变量扩展逻辑 (支持 ${VAR:-DEFAULT}) ---
        pattern = re.compile(r'\$\{([^}:]+)(?::-([^}]+))?\}')
        def replace_env(match):
            env_var = match.group(1)
            default = match.group(2)
            return os.environ.get(env_var, default if default else "")
            
        expanded_content = pattern.sub(replace_env, content)
        self._config_data = yaml.safe_load(expanded_content)

    def get_all(self):
        return self._config_data

    def get(self, key, default=None):
        return self._config_data.get(key, default)

# 全局唯一实例
cfg = Config()

def get_config():
    """替代原有的 load_config，提供高性能、支持环境变量扩展的单例配置访问"""
    return cfg.get_all()
