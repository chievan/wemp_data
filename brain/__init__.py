# Brain 核心接口导出中心
# 统一管理 Logic, Agents 和 Tools 的对外暴露

# 1. 基础配置 (最底层，无依赖)
from .config import get_config as load_config

# 2. 逻辑层 (Logic) - 只有基础工具依赖
from .logic.deep_research import (
    stream_deep_research
)
from .logic.knowledge_base import (
    embed_query,
    search_ddb,
    stream_knowledge_analysis
)
from .logic.session_manager import (
    load_session_detail,
    delete_session,
    save_session_history,
    list_recent_sessions,
    get_available_mps,
    get_mp_preferences,
    save_mp_preferences
)

# 3. 智能体层 (Agents) - 可能依赖 Logic 或 Tools
from .agents.ai_committee import (
    get_committee_graph,
    COMMITTEE_PRESETS,
    load_recent_memories,
    save_session_memory
)

# 4. 基础工具
from .tools.research_tools import search_wemp_library
from .tools.web_search_tool import search_bocha
