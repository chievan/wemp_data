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

router = APIRouter(prefix="/chat", tags=["chat"])

class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    enable_web: bool = False
    model: str = "deepseek-v4-flash"

# Initialize Embeddings
embeddings = DashScopeEmbeddings(
    dashscope_api_key=settings.EMBEDDING_API_KEY,
    model=settings.EMBEDDING_MODEL
)

def get_llm(model_name: str = "deepseek-v4-flash"):
    provider_config = {
        "openai_api_key": settings.EMBEDDING_API_KEY if model_name.startswith("qwen") else settings.DEEPSEEK_API_KEY,
        "openai_api_base": settings.EMBEDDING_BASE_URL if model_name.startswith("qwen") else settings.DEEPSEEK_BASE_URL,
    }
    return ChatOpenAI(model=model_name, streaming=True, **provider_config)

@router.post("")
async def chat_with_docs(request: ChatRequest):
    try:
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
        docs = vector_store.similarity_search(request.message, k=5)
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
        base_system = "你是一个专业的金融投研助手。基于以下检索到的上下文资料来回答用户的问题。如果你不知道答案，就说你不知道。"
        if persona_prompt:
            base_system = f"你现在正在扮演一个特定的金融专家。请严格按照以下专家设定的研究框架、逻辑偏好和语言风格来回答问题。\n{persona_prompt}\n\n基于以下资料回答:"
        
        full_prompt = ChatPromptTemplate.from_messages([
            ("system", base_system),
            ("human", "上下文:\n{context}\n\n问题: {input}"),
        ])

        # 6. 流式返回
        async def generate():
            if target_name:
                yield f"✨ 已激活专家分身：`@{target_name}`\n\n"
            
            current_llm = get_llm(request.model)
            formatted = full_prompt.format_messages(context=final_context, input=request.message)
            async for chunk in current_llm.astream(formatted):
                if chunk.content: yield chunk.content
            
            # 附录：参考资料
            if docs or web_results:
                refs = "\n\n[WEMP_REFS_START]\n"
                # 本地文档
                for i, d in enumerate(docs):
                    title = d.metadata.get('title','本地资料')[:50]
                    url = d.metadata.get('source_url','#')
                    refs += f"{i+1}. {title} [来源]({url})\n"
                # 联网搜索
                for i, item in enumerate(web_results):
                    idx = i + len(docs) + 1
                    refs += f"{idx}. {item['title'][:50]} [网络搜索]({item['url']})\n"
                yield refs
                    
        return StreamingResponse(generate(), media_type="text/event-stream")
    except Exception as e:
        api_logger.error(f"Chat error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
