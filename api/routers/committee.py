import json
import os
import sqlite3
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Optional

router = APIRouter(prefix="/committee", tags=["committee"])

# --- Paths ---
PREFERENCES_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "brain", "agents", "wemp_preferences.json"
)
AGENT_STORAGE_DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "data", "agent_storage.db"
)

DEFAULT_COMMITTEE_PRESETS = {
    "债券投资委员会": ["Hanson老登", "一创固收", "中金点睛", "兴证固收", "兴业研究宏观"],
    "权益投资委员会": ["中金点睛", "兴业研究宏观", "泽平宏观", "刘煜辉", "管清友"],
    "商品投资委员会": ["付鹏", "中金点睛", "Hanson老登", "兴业研究宏观"],
    "私募基金投资委员会": ["私募排排网", "中金点睛", "朝阳永续"]
}


def _load_preferences() -> dict:
    if os.path.exists(PREFERENCES_PATH):
        try:
            with open(PREFERENCES_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_preferences(prefs: dict):
    os.makedirs(os.path.dirname(PREFERENCES_PATH), exist_ok=True)
    with open(PREFERENCES_PATH, "w", encoding="utf-8") as f:
        json.dump(prefs, f, ensure_ascii=False, indent=2)


# --- 1. 获取所有委员会及其成员 ---
@router.get("/presets")
def get_presets():
    """返回合并后的委员会预设（DEFAULT + 本地覆盖）"""
    prefs = _load_preferences()
    result = {}
    for name, default_members in DEFAULT_COMMITTEE_PRESETS.items():
        key = f"mps_{name}"
        if key in prefs and prefs[key]:
            result[name] = prefs[key]
        else:
            result[name] = default_members
    return result


# --- 2. 保存某个委员会的成员列表 ---
class SaveMembersRequest(BaseModel):
    committee_name: str
    members: List[str]


@router.post("/presets")
def save_preset(request: SaveMembersRequest):
    """保存某个委员会的成员列表到本地 JSON"""
    if request.committee_name not in DEFAULT_COMMITTEE_PRESETS:
        raise HTTPException(status_code=400, detail=f"未知的委员会类型: {request.committee_name}")
    
    prefs = _load_preferences()
    key = f"mps_{request.committee_name}"
    prefs[key] = request.members
    _save_preferences(prefs)
    return {"message": "保存成功", "committee_name": request.committee_name, "members": request.members}


# --- 3. 获取可选的公众号列表（来自本地数据库） ---
@router.get("/mp_names")
def get_available_mp_names():
    """从 wemp_data.db 中获取所有已入库的公众号名称"""
    from api.core.config import settings
    db_path = settings.DATABASE_URL.replace("sqlite:///", "")
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT mp_name FROM wemp_articles ORDER BY mp_name")
        names = [row[0] for row in cursor.fetchall()]
        conn.close()
        return names
    except Exception as e:
        return []


# --- 4. 获取投委会历史会议记录（从 DolphinDB） ---
@router.get("/sessions")
def get_sessions(limit: int = 20):
    """从 DolphinDB wemp_sessions 表读取历史投委会会议"""
    try:
        from brain.logic.session_manager import list_recent_sessions
        df = list_recent_sessions(limit=limit)
        if df is None or df.empty:
            return []
        sessions = []
        for _, row in df.iterrows():
            sessions.append({
                "session_id": row.get("session_id", ""),
                "title": row.get("session_name", "未命名"),
            })
        return sessions
    except Exception as e:
        print(f"Load sessions from DolphinDB error: {e}")
        return []


# --- 5. 获取某次会议的完整对话详情 ---
@router.get("/sessions/{session_id}")
def get_session_detail(session_id: str):
    """从 DolphinDB 加载指定会议的完整对话历史"""
    try:
        from brain.logic.session_manager import load_session_detail
        detail = load_session_detail(session_id)
        if not detail:
            raise HTTPException(status_code=404, detail="会议记录未找到")
        return detail
    except HTTPException:
        raise
    except Exception as e:
        print(f"Load session detail error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/sessions/{session_id}")
def delete_history_session(session_id: str):
    """从 DolphinDB 物理删除会议记录"""
    try:
        from brain.logic.session_manager import delete_session
        success = delete_session(session_id)
        if not success:
            raise HTTPException(status_code=500, detail="删除失败")
        return {"message": "删除成功", "session_id": session_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- 6. 执行投委会圆桌讨论（流式） ---
class RunCommitteeRequest(BaseModel):
    topic: str
    committee_type: str = "债券投资委员会"
    members: List[str] = []


@router.post("/run")
def run_committee(request: RunCommitteeRequest):
    """流式执行 LangGraph 投委会圆桌讨论"""
    from fastapi.responses import StreamingResponse
    import time

    def generate():
        try:
            from brain.agents.ai_committee import get_committee_graph, load_recent_memories
            from brain.logic.session_manager import save_session_history
            from langchain_core.messages import HumanMessage

            graph = get_committee_graph()
            initial_state = {
                "messages": [HumanMessage(content=request.topic)],
                "committee_type": request.committee_type,
                "selected_members": request.members,
                "target_expert": "",
                "turns": 0,
                "next_step": "",
                "recent_memories": load_recent_memories()
            }

            all_messages = []
            for event in graph.stream(initial_state, {"recursion_limit": 60}):
                for node_name, state_update in event.items():
                    msgs = state_update.get("messages", [])
                    for msg in msgs:
                        name = getattr(msg, 'name', '') or ''
                        # Skip internal reasoning logs
                        if name.endswith('_Thought') or name == 'CIO_Reasoning':
                            continue
                        entry = {
                            "role": "user" if msg.type == "human" else "assistant",
                            "name": name or node_name,
                            "content": msg.content
                        }
                        all_messages.append(entry)
                        yield json.dumps(entry, ensure_ascii=False) + "\n"

            # Save session to DolphinDB
            session_id = time.strftime("%Y%m%d%H%M%S")
            history_for_save = [{"role": m["role"], "content": m["content"], "name": m.get("name", "")} for m in all_messages]
            try:
                save_session_history(session_id, request.topic, history_for_save, request.committee_type, request.members)
            except Exception as se:
                print(f"Save session error: {se}")

        except Exception as e:
            import traceback
            traceback.print_exc()
            yield json.dumps({"role": "assistant", "name": "系统", "content": f"执行失败: {str(e)}"}, ensure_ascii=False) + "\n"

    return StreamingResponse(generate(), media_type="application/x-ndjson")
