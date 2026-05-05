import dolphindb as ddb
import pandas as pd
import json
import datetime
import os
from pathlib import Path
from brain.config import cfg

# --- 全局连接持久化 ---
# 注意：在多线程（如 Streamlit）环境下，使用 ThreadLocal 或简单锁会更安全
import threading
_DDB_LOCK = threading.Lock()
_CACHED_SESSION = None

def get_ddb_session():
    """
    带断线重连保护的高性能 DolphinDB 会话管理器。
    """
    global _CACHED_SESSION
    c = cfg.get("dolphindb", {})
    
    with _DDB_LOCK:
        # 1. 如果缓存存在，进行深度检查
        if _CACHED_SESSION is not None:
            try:
                # 执行一个极其轻量的查询来验证链路
                _CACHED_SESSION.run("1")
                return _CACHED_SESSION
            except:
                # 连接失效，静默清理
                try: _CACHED_SESSION.close()
                except: pass
                _CACHED_SESSION = None
        
        # 2. 创建新连接
        try:
            s = ddb.session()
            s.connect(c["host"], int(c["port"]), c["user"], c["password"])
            _CACHED_SESSION = s
            print("🔄 DolphinDB Connection Re-established.")
            return s
        except Exception as e:
            print(f"❌ DolphinDB Connection Critical Failure: {e}")
            raise e

# --- 会话逻辑 (Logic) ---

def load_session_detail(session_id):
    """从数据库加载会话历史"""
    s = get_ddb_session()
    try:
        db_name = cfg.get('dolphindb', {}).get('database')
        df = s.run(f"select * from loadTable('{db_name}', 'wemp_sessions') where session_id = '{session_id}'")
        if not df.empty:
            return {
                "history": json.loads(df['history_json'][0]),
                "type": df['committee_type'][0]
            }
        return None
    except Exception as e:
        print(f"⚠️ Load session detail error: {e}")
        return None

def delete_session(session_id):
    """从数据库物理删除会话"""
    s = get_ddb_session()
    try:
        db_name = cfg.get('dolphindb', {}).get('database')
        s.run(f"delete from loadTable('{db_name}', 'wemp_sessions') where session_id = '{session_id}'")
        return True
    except Exception as e:
        print(f"❌ Delete session error: {e}")
        return False

def save_session_history(session_id, query, messages, committee_type, selected_mps):
    """保存/更新会话历史"""
    s = get_ddb_session()
    try:
        db_name = cfg.get('dolphindb', {}).get('database')
        name_tag = query[:50] if query else "未命名议题"
        df_save = pd.DataFrame({
            'session_id': [session_id],
            'session_name': [name_tag],
            'history_json': [json.dumps(messages, ensure_ascii=False)],
            'mps_results_json': [json.dumps([], ensure_ascii=False)],
            'committee_type': [committee_type],
            'selected_mps_json': [json.dumps(selected_mps, ensure_ascii=False)],
            'last_active': [pd.Timestamp.now()]
        })
        s.upload({'ns': df_save})
        s.run(f"upsert!(loadTable('{db_name}', 'wemp_sessions'), ns, keyColNames=`session_id)")
        return True
    except Exception as e:
        print(f"❌ Save session history error: {e}")
        return False

def list_recent_sessions(limit=15):
    """获取最近的议题列表"""
    s = get_ddb_session()
    try:
        db_name = cfg.get('dolphindb', {}).get('database')
        return s.run(f"select session_id, session_name from loadTable('{db_name}', 'wemp_sessions') order by last_active desc limit {limit}")
    except Exception as e:
        print(f"⚠️ List sessions error: {e}")
        return pd.DataFrame()

def get_available_mps():
    """获取所有可用的公众号/专家名单"""
    s = get_ddb_session()
    try:
        db_name = cfg.get('dolphindb', {}).get('database')
        table_name = cfg.get('dolphindb', {}).get('articles_table', 'wemp_articles')
        return s.run(f"select distinct(mp_name) as mp_name from loadTable('{db_name}', '{table_name}')").mp_name.tolist()
    except Exception as e:
        print(f"⚠️ Get available MPs error: {e}")
        return []

# --- 用户偏好逻辑 (Preferences) ---

def _get_pref_path():
    """统一获取偏好文件路径"""
    root = Path(__file__).parent.parent.parent
    return root / "brain" / "agents" / "wemp_preferences.json"

def get_mp_preferences(committee_type):
    """获取指定委员会的专家偏好名单"""
    p_path = _get_pref_path()
    if p_path.exists():
        try:
            with open(p_path, "r", encoding="utf-8") as f:
                prefs = json.load(f)
                return prefs.get(f"mps_{committee_type}")
        except Exception as e:
            print(f"⚠️ Read preferences error: {e}")
    return None

def save_mp_preferences(committee_type, mps):
    """保存专家偏好名单"""
    p_path = _get_pref_path()
    p_dir = p_path.parent
    
    prefs = {}
    if p_path.exists():
        try:
            with open(p_path, "r", encoding="utf-8") as f:
                prefs = json.load(f)
        except: pass
    
    prefs[f"mps_{committee_type}"] = mps
    try:
        p_dir.mkdir(parents=True, exist_ok=True)
        with open(p_path, "w", encoding="utf-8") as f:
            json.dump(prefs, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"❌ Save preferences error: {e}")
        return False
