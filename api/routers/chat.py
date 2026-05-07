import os
import re
import dolphindb as ddb
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional, List

from langchain_openai import ChatOpenAI
from langchain_community.embeddings import DashScopeEmbeddings
from langchain_core.prompts import ChatPromptTemplate

from api.core.config import settings
from api.core.dolphindb_vectorstore import DolphinDBVectorStore
from core.logger import api_logger
from brain.prompts.knowledge_base import KNOWLEDGE_SYSTEM_PROMPT, KNOWLEDGE_USER_TEMPLATE


router = APIRouter(prefix="/chat", tags=["chat"])

class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    enable_web: bool = False
    model: str = "deepseek-v4-flash"
    filter_article_id: Optional[str] = None

from api.core.embeddings import embeddings
from api.core.database import SessionLocal
from api.models.chat import ChatSession


def get_llm(model_name: str = "deepseek-v4-flash"):
    provider_config = {
        "openai_api_key": settings.EMBEDDING_API_KEY if model_name.startswith("qwen") else settings.DEEPSEEK_API_KEY,
        "openai_api_base": settings.EMBEDDING_BASE_URL if model_name.startswith("qwen") else settings.DEEPSEEK_BASE_URL,
    }
    return ChatOpenAI(model=model_name, streaming=True, **provider_config)

@router.post("/session")
async def create_or_update_session(request: dict):
    """创建或更新会话元数据（如绑定文章ID）"""
    sid = request.get("session_id")
    aid = request.get("article_id")
    title = request.get("title")
    if not sid:
        raise HTTPException(status_code=400, detail="session_id is required")
    
    db = SessionLocal()
    try:
        session = db.query(ChatSession).filter(ChatSession.session_id == sid).first()
        if not session:
            session = ChatSession(session_id=sid, article_id=aid, title=title)
            db.add(session)
        else:
            if aid: session.article_id = aid
            if title: session.title = title
        db.commit()
        return {"status": "success", "session_id": sid, "article_id": session.article_id}
    finally:
        db.close()

@router.post("")
async def chat_with_docs(request: ChatRequest):
    try:
        # 0. 检查会话深度绑定的文章
        effective_article_id = request.filter_article_id
        if not effective_article_id and request.session_id:
            db = SessionLocal()
            session = db.query(ChatSession).filter(ChatSession.session_id == request.session_id).first()
            if session and session.article_id:
                effective_article_id = session.article_id
            db.close()

        # 1. 技能识别与 Prompt 注入 (@skill)
        persona_prompt = ""
        target_name = ""
        match = re.search(r'@(\S+)', request.message)
        
        if match:
            target_name = match.group(1)
            from api.routers.skills import get_skills, SKILLS_DIR
            installed_skills = get_skills()
            target_skill = next((s for s in installed_skills if s.get('id') == target_name or s.get('name') == target_name), None)
            
            if target_skill:
                skill_path = os.path.join(SKILLS_DIR, target_skill['id'])
                # 尝试读取 SKILL.md 作为身份设定
                for md_file in ["SKILL.md", "skill.md", "README.md"]:
                    p = os.path.join(skill_path, md_file)
                    if os.path.exists(p):
                        with open(p, "r", encoding="utf-8", errors='ignore') as f:
                            persona_prompt = f"\n\n【你现在的身份设定】:\n{f.read()}\n"
                        break
            
        # 2. 知识库检索 (RAG)
        sess = ddb.session()
        sess.connect(host=settings.DDB_HOST, port=int(settings.DDB_PORT), userid=settings.DDB_USER, password=settings.DDB_PASSWORD)
        vector_store = DolphinDBVectorStore(session=sess, embedding=embeddings, database_path=settings.DDB_DATABASE, table_name=settings.DDB_CHUNKS_TABLE)
        
        k_value = 10 if effective_article_id else 5
        search_filter = {"article_id": effective_article_id} if effective_article_id else None
        docs = vector_store.similarity_search(request.message, k=k_value, filter=search_filter)
        sess.close()

        # 3. 联网搜索
        web_results = []
        if request.enable_web:
            try:
                from brain.tools.web_search_tool import search_bocha
                web_results = search_bocha(request.message)
            except: pass

        # 4. 构建上下文
        context_parts = [f"资料[{i+1}] {doc.page_content}" for i, doc in enumerate(docs)]
        if web_results:
            context_parts.extend([f"网络资料[{i+len(docs)+1}] {item['snippet']}" for i, item in enumerate(web_results)])
        
        final_context = "\n\n".join(context_parts) if context_parts else "暂无参考资料。"

        # 5. 构建 System Prompt
        if persona_prompt:
            # 如果有专家分身，将分身设定与基础知识库要求结合
            base_system = f"你现在正在扮演一个特定的金融专家。请严格按照以下专家设定回答问题。\n{persona_prompt}\n\n{KNOWLEDGE_SYSTEM_PROMPT}"
        elif effective_article_id:
            # 专项问答模式下的提示词增强
            base_system = f"你现在是这份特定研究报告的分析专家。请严格基于提供的资料进行深度分析和回答，不要引用资料以外的常识，如果资料中没有提到相关信息，请直接说明。\n\n{KNOWLEDGE_SYSTEM_PROMPT}"
        else:
            base_system = KNOWLEDGE_SYSTEM_PROMPT
        
        full_prompt = ChatPromptTemplate.from_messages([
            ("system", base_system),
            ("human", KNOWLEDGE_USER_TEMPLATE),
        ])


        # 6. 流式返回
        async def generate():
            if target_name:
                yield f"✨ 已激活专家分身：`@{target_name}`\n\n"
            
            current_llm = get_llm(request.model)
            formatted = full_prompt.format_messages(context=final_context, query=request.message)
            async for chunk in current_llm.astream(formatted):
                if chunk.content: yield chunk.content
            
            # 附录：参考资料
            if docs or web_results:
                refs = "\n\n[WEMP_REFS_START]\n"
                # 本地文档
                for i, d in enumerate(docs):
                    title = d.metadata.get('title','本地资料')[:50]
                    url = d.metadata.get('source_url','#')
                    refs += f"{i+1}. [{title}]({url}) (本地知识库)\n"
                # 联网搜索
                for i, item in enumerate(web_results):
                    idx = i + len(docs) + 1
                    refs += f"{idx}. [{item['title'][:50]}]({item['url']}) (网络搜索)\n"
                yield refs
                    
        return StreamingResponse(generate(), media_type="text/event-stream")
    except Exception as e:
        api_logger.error(f"Chat error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
