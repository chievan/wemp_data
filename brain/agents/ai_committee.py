from typing import Annotated, List, Tuple, Union, TypedDict
import os
import sqlite3
import time
import json
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from brain.tools.research_tools import search_wemp_library, search_wemp_library_by_keywords
from brain.config import get_config as load_config
from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from brain.prompts.ai_committee import (
    CIO_SUPERVISOR_SYSTEM_PROMPT, 
    CIO_RESOLUTION_PROMPT, 
    EXPERT_KEYWORDS_PROMPT, 
    EXPERT_SYSTEM_PROMPT,
    EXPERT_HUMAN_TEMPLATE
)
from core.logger import committee_logger as logger

# --- 数据库路径 ---
DB_WEMP_DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "wemp_data.db")

# --- 记忆辅助函数 ---
def load_recent_memories(limit=3):
    try:
        conn = sqlite3.connect(DB_WEMP_DATA)
        cursor = conn.cursor()
        # Check if table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='agent_memory'")
        if not cursor.fetchone():
            conn.close()
            return []
        cursor.execute("SELECT memory FROM agent_memory ORDER BY created_at DESC LIMIT ?", (limit,))
        rows = cursor.fetchall()
        conn.close()
        memories = [json.loads(row[0]) if isinstance(row[0], str) else row[0] for row in rows if row[0]]
        return [f"• {m.get('consensus', '')}" for m in memories if m.get("consensus")]
    except Exception as e: 
        logger.error(f"Load memory error: {e}")
        return []

def save_session_memory(session_id, consensus_text):
    try:
        conn = sqlite3.connect(DB_WEMP_DATA)
        cursor = conn.cursor()
        # Ensure table exists
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS agent_memory (
                session_id TEXT PRIMARY KEY,
                memory TEXT,
                created_at INTEGER,
                updated_at INTEGER
            )
        """)
        now = int(time.time())
        memory_json = json.dumps({"consensus": consensus_text}, ensure_ascii=False)
        conn.execute("""
            INSERT INTO agent_memory (session_id, memory, created_at, updated_at) 
            VALUES (?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET memory=excluded.memory, updated_at=excluded.updated_at
        """, (session_id, memory_json, now, now))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Save memory error: {e}")

# --- 1. 投委会成员配置 ---
DEFAULT_COMMITTEE_PRESETS = {
    "债券投资委员会": ["Hanson老登", "一创固收", "中金点睛", "兴证固收", "兴业研究宏观"],
    "权益投资委员会": ["中金点睛", "兴业研究宏观", "泽平宏观", "刘煜辉", "管清友"],
    "商品投资委员会": ["付鹏", "中金点睛", "Hanson老登", "兴业研究宏观"],
    "私募基金投资委员会": ["私募排排网", "中金点睛", "朝阳永续"]
}

def get_committee_presets():
    presets = DEFAULT_COMMITTEE_PRESETS.copy()
    p_path = os.path.join(os.path.dirname(__file__), "wemp_preferences.json")
    if os.path.exists(p_path):
        try:
            with open(p_path, "r", encoding="utf-8") as f:
                prefs = json.load(f)
                for k, v in prefs.items():
                    if k.startswith("mps_"):
                        type_name = k.replace("mps_", "")
                        if v: 
                            presets[type_name] = v
        except: pass
    return presets

COMMITTEE_PRESETS = get_committee_presets()

def load_expert_skill(expert_name: str):
    p_curr = os.path.dirname(os.path.abspath(__file__)) # brain/agents
    p_brain = os.path.dirname(p_curr) # brain
    skill_path = os.path.join(p_brain, "skills", expert_name, "SKILL.md")
    
    if os.path.exists(skill_path):
        try:
            content = open(skill_path, 'r', encoding='utf-8').read()
            return f"\n--- 你的专属研究技能指南 ({expert_name}) ---\n{content}\n"
        except: 
            pass
    return ""

# 1. 定义状态
class CommitteeState(TypedDict):
    messages: Annotated[List[BaseMessage], add_messages]
    committee_type: str
    selected_members: List[str] 
    target_expert: str 
    turns: int 
    next_step: str
    recent_memories: List[str]

def get_committee_graph():
    cfg = load_config()
    llm = ChatOpenAI(
        model="deepseek-chat",
        api_key=cfg["api_keys"]["deepseek"],
        base_url="https://api.deepseek.com/v1",
        temperature=0.3
    )

    # --- 1. CIO 调度节点 ---
    def cio_supervisor_node(state: CommitteeState):
        comm_type = state.get("committee_type", "债券投资委员会")
        members = state.get("selected_members", [])
        current_turns = state.get("turns", 0)
        
        expert_messages = [m for m in state["messages"] if getattr(m, "name", "") in members]
        distinct_experts = set([getattr(m, "name", "") for m in expert_messages])
        
        if len(distinct_experts) < len(members):
            next_researcher = [m for m in members if m not in distinct_experts][0]
            cio_reasoning = f"[INTERNAL_LOG] 识别到委员会成员『{next_researcher}』尚未发言。指令：启动该委员的研报库与博查实时检索程序。"
            cio_msg = f"【全员预研】请『{next_researcher}』委员结合您的历史研报和全网最新动态，给出专业意见。"
            return {
                "messages": [AIMessage(content=cio_reasoning, name="CIO_Reasoning"), AIMessage(content=cio_msg, name="CIO")],
                "next_step": "ExpertNode",
                "target_expert": next_researcher
            }

        import datetime
        current_date = datetime.datetime.now().strftime("%Y-%m-%d")
        mem_str = "\n".join(state.get("recent_memories", []))
        history_context = f"\n【近期共识回顾】：\n{mem_str}\n" if mem_str else ""

        prompt = CIO_SUPERVISOR_SYSTEM_PROMPT.format(comm_type=comm_type, history_context=history_context, current_date=current_date)
        
        if current_turns > 0:
            prompt += "\n\n【特别提醒】：本次会议已进行过交叉验证。请根据最新回答判断，如果问题已查清且各方逻辑闭环，请务必直接回复 'FINISH' 结束会议，绝对不要重复问刚才问过的问题！"

        # 格式化消息上下文，把 name 显式注入 content 中，防止模型底层 API 忽略 name 字段导致"脸盲"
        formatted_messages = []
        for m in state["messages"]:
            speaker = getattr(m, "name", "")
            if isinstance(m, AIMessage) and speaker:
                formatted_messages.append(AIMessage(content=f"【发言人: {speaker}】\n{m.content}", name=speaker))
            elif isinstance(m, HumanMessage):
                formatted_messages.append(HumanMessage(content=f"【用户核心议题】\n{m.content}"))
            else:
                formatted_messages.append(m)

        messages = [{"role": "system", "content": prompt}] + formatted_messages
        response = llm.invoke(messages)
        decision = response.content.strip()

        # 初轮 round-robin 阶段 turns 不计数，因此只需要强制 1 轮交叉验证即可避免重复
        min_turns_for_finish = 1
        # 硬性上限：防止无限循环（1轮初始发言 + 1轮交叉验证 + CIO决议）
        max_turns = len(members) + 3
        
        if current_turns >= max_turns or ("FINISH" in decision and current_turns >= min_turns_for_finish):
            final_res = llm.invoke([{"role": "system", "content": CIO_RESOLUTION_PROMPT}] + formatted_messages)
            return {
                "messages": [AIMessage(content=final_res.content, name="CIO_Resolution")],
                "next_step": "END", 
                "target_expert": ""
            }
        
        logger.info(f"CIO decision: {decision}")
        
        # 从 CIO 回复中提取目标专家名（尝试匹配『xxx』）
        import re
        target = members[0]
        found = re.findall(r"[『「]([^』」]+)[』」]", decision)
        if found:
            # 第一个匹配到的是被要求做交叉验证的专家
            for name in found:
                if name in members:
                    target = name
                    break
        
        # 如果 CIO 想 FINISH 但轮次不够，强制追加一轮交叉验证
        if "FINISH" in decision and current_turns < min_turns_for_finish:
            decision = f"请『{target}』委员对前文各位委员的核心观点进行交叉验证与补充。"

        return {
            "messages": [AIMessage(content=decision, name="CIO")],
            "next_step": "ExpertNode",
            "target_expert": target,
            "turns": current_turns + 1
        }

    # --- 2. 动态专家节点 (增强：私有历史 + 博查全网实时) ---
    def dynamic_expert_node(state: CommitteeState):
        expert_name = state.get("target_expert", "未知专家")
        my_skill = load_expert_skill(expert_name).replace("{", "{{").replace("}", "}}")
        
        import datetime
        import json
        import re
        current_date = datetime.datetime.now().strftime("%Y-%m-%d")
        
        # 1. 提炼搜索关键词与时间区间
        kw_prompt = EXPERT_KEYWORDS_PROMPT.format(expert_name=expert_name, current_date=current_date)
        
        # 过滤对话历史，只保留人类议题和 CIO 调度指令，防止其他专家的发言对关键词提炼进行上下文污染与注意力偏移
        context_msgs = []
        for msg in state["messages"]:
            if isinstance(msg, HumanMessage):
                context_msgs.append(f"用户核心议题：{msg.content}")
            elif getattr(msg, "name", "") == "CIO":
                context_msgs.append(f"CIO调度指令：{msg.content}")
                
        context_str = "\n".join(context_msgs)
        
        # 构造高精度的系统和用户提示词，确保模型在单次会话中稳定输出纯 JSON
        kw_system = "你是一个高精度的投研检索参数提取工具。你必须且只能输出纯 JSON，严禁输出任何 Markdown 标记（如 ```json）、任何闲聊、解释或前导/后导文字。"
        kw_user = f"""当前系统时间是 {current_date}。
当前讨论上下文：
{context_str}

请结合上下文，为委员【{expert_name}】分析其检索私有研报库和全网所需的最具针对性的 2-3 个核心关键词，以及时间过滤范围（转换成 YYYY-MM-DD 格式，若无限制则为 null）。

你必须且只能输出如下格式的 JSON 对象：
{{
    "keywords": "关键词1 关键词2",
    "start_time": "开始日期 YYYY-MM-DD 或 null",
    "end_time": "结束日期 YYYY-MM-DD 或 null"
}}"""
        
        kw_res = llm.invoke([
            ("system", kw_system),
            ("human", kw_user)
        ])
        
        keywords = ""
        start_time = None
        end_time = None
        
        try:
            clean_content = kw_res.content.strip()
            # 【鲁棒性超强增强】：利用正则截取首尾花括号，强行抽离出干净的 JSON，彻底免疫 Markdown 标记与前导/后导口语化文字
            json_match = re.search(r'(\{.*\})', clean_content, re.DOTALL)
            if json_match:
                clean_content = json_match.group(1)
                
            params = json.loads(clean_content)
            keywords = params.get("keywords", "").strip()
            start_time = params.get("start_time")
            end_time = params.get("end_time")
            if start_time == "null" or start_time == "": start_time = None
            if end_time == "null" or end_time == "": end_time = None
        except Exception:
            # 降级处理：若 JSON 解析失败，取整段作为关键词
            keywords = kw_res.content.strip()
            
        logger.info(f"Expert {expert_name} time-aware query: keywords='{keywords}', start_time='{start_time}', end_time='{end_time}'")

        # 2. 执行双重检索
        # A. 私有历史
        private_results = search_wemp_library.invoke({
            "query": keywords, 
            "mp_name": expert_name,
            "start_time": start_time,
            "end_time": end_time
        })
        # B. 博查全网
        from brain.tools.web_search_tool import search_bocha
        web_results = search_bocha(keywords, count=5)
        
        # 3. 资料解析与数量统计 (Logic Trace 仅保留统计，不列标题)
        sources = []
        # 解析私有库来源
        private_count = 0
        for line in private_results.split("\n"):
            if "标题:" in line:
                private_count += 1
                parts = line.split("|")
                title = next((p for p in parts if "标题:" in p), "未知标题").replace("标题:", "").strip()
                link = next((p for p in parts if "链接:" in p), "").replace("链接:", "").strip()
                sources.append({"title": title, "link": link, "type": "私有历史"})
        
        web_context = ""
        web_count = 0
        if isinstance(web_results, list):
            web_count = len(web_results)
            web_context = "\n【全网实时资讯】：\n"
            for item in web_results:
                idx = len(sources) + 1
                sources.append({"title": item["title"], "link": item["url"], "type": "全网实时"})
                web_context += f"资料[{idx}] 来源：{item['source']} | 标题：《{item['title']}》\n内容：{item['snippet']}\n"
        
        # 极简版的 Logic Trace
        expert_thought = f"[INTERNAL_LOG] 专家正在思考... 关键词：『{keywords}』 | 状态：已成功加载 {private_count} 条私有历史 + {web_count} 条博查实时动态。"
        
        # 4. 生成深度分析回答
        prompt_node = ChatPromptTemplate.from_messages([
            ("system", EXPERT_SYSTEM_PROMPT.format(expert_name=expert_name, my_skill=my_skill, current_date=current_date)),
            MessagesPlaceholder(variable_name="chat_history"),
            ("human", EXPERT_HUMAN_TEMPLATE),
        ])
        
        messages = prompt_node.format_messages(chat_history=state["messages"], private_data=private_results, web_data=web_context)
        llm_res = llm.invoke(messages)
        
        # 5. 后端统一追加参考资料 (确保 [序号] 与 AI 标注一致)
        final_content = llm_res.content
        if sources:
            final_content += "\n\n参考资料：\n"
            for i, s in enumerate(sources[:8]):
                idx = i + 1
                final_content += f"* [{idx}] [{s['title']}]({s['link']}) ({s['type']})\n"
        
        return {
            "messages": [AIMessage(content=expert_thought, name=f"{expert_name}_Thought"), AIMessage(content=final_content, name=expert_name)],
        }

    # 3. 构建图
    builder = StateGraph(CommitteeState)
    builder.add_node("CIO", cio_supervisor_node)
    builder.add_node("ExpertNode", dynamic_expert_node)
    builder.set_entry_point("CIO")
    builder.add_edge("ExpertNode", "CIO")
    builder.add_conditional_edges(
        "CIO",
        lambda x: "END" if x["next_step"] == "END" else "CALL_EXPERT",
        {"CALL_EXPERT": "ExpertNode", "END": END}
    )

    return builder.compile()

if __name__ == "__main__":
    graph = get_committee_graph()
    print("✅ 投委会智库 3.0 (联网增强版) 已就绪！")
