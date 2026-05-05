import dolphindb as ddb
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional

from langchain_openai import ChatOpenAI
from langchain_community.embeddings import DashScopeEmbeddings
from langchain.chains import create_retrieval_chain
from langchain.chains.combine_documents import create_stuff_documents_chain
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

# We can reuse one session, but for DolphinDB it's better to manage it carefully.
# For simplicity, we create a global session here, but in production, we might use a pool.
ddb_session = ddb.session()
ddb_session.connect(host=settings.DDB_HOST, port=int(settings.DDB_PORT), userid=settings.DDB_USER, password=settings.DDB_PASSWORD)

# Initialize Embeddings
embeddings = DashScopeEmbeddings(
    dashscope_api_key=settings.EMBEDDING_API_KEY,
    model=settings.EMBEDDING_MODEL
)

# Initialize VectorStore
vector_store = DolphinDBVectorStore(
    session=ddb_session,
    embedding=embeddings,
    database_path=settings.DDB_DATABASE,
    table_name=settings.DDB_CHUNKS_TABLE
)

# Default llm settings will be overridden per request if needed
def get_llm(model_name: str = "deepseek-v4-flash"):
    if model_name.startswith("qwen"):
        # Use DashScope/Aliyun for Qwen models
        return ChatOpenAI(
            openai_api_key=settings.EMBEDDING_API_KEY,
            openai_api_base=settings.EMBEDDING_BASE_URL,
            model=model_name,
            streaming=True
        )
    else:
        # Use DeepSeek provider
        return ChatOpenAI(
            openai_api_key=settings.DEEPSEEK_API_KEY,
            openai_api_base=settings.DEEPSEEK_BASE_URL,
            model=model_name,
            streaming=True
        )

system_prompt = (
    "你是一个专业的金融投研助手。基于以下检索到的上下文资料来回答用户的问题。\n"
    "如果你不知道答案，就说你不知道，不要试图编造。\n"
    "直接给出你的专业回答，不要在回答末尾自行列出'参考资料'或'资料来源'章节，因为系统会自动在回答后附带这些信息。\n\n"
    "上下文资料:\n{context}"
)

prompt = ChatPromptTemplate.from_messages([
    ("system", system_prompt),
    ("human", "{input}"),
])

# 投研对话路由
@router.post("")
async def chat_with_docs(request: ChatRequest):
    try:
        # Connect fresh for this request
        sess = ddb.session()
        sess.connect(host=settings.DDB_HOST, port=int(settings.DDB_PORT), userid=settings.DDB_USER, password=settings.DDB_PASSWORD)
        
        vector_store = DolphinDBVectorStore(
            session=sess,
            embedding=embeddings,
            database_path=settings.DDB_DATABASE,
            table_name=settings.DDB_CHUNKS_TABLE
        )
        
        # 1. Retrieve synchronously
        docs = vector_store.similarity_search(request.message, k=5)
        
        # We can close the session now that we have the documents
        sess.close()

        # 2. Web Search
        web_results = []
        if request.enable_web:
            try:
                from brain.tools.web_search_tool import search_bocha
                web_results = search_bocha(request.message)
            except Exception as e:
                api_logger.warning(f"Web search failed: {e}")

        # 3. Format Context and References
        context_parts = []
        references_md = "\n\n[WEMP_REFS_START]\n"
        
        if docs:
            for i, doc in enumerate(docs):
                idx = i + 1
                mp_name = doc.metadata.get("mp_name", "Unknown")
                title = doc.metadata.get("title", "Unknown")
                source_url = doc.metadata.get("source_url", "#")
                # Truncate title for cleaner list
                display_title = title[:60] + "..." if len(title) > 60 else title
                context_parts.append(f"资料[{idx}] 来源：{mp_name} | 标题：《{title}》\n内容：{doc.page_content}")
                references_md += f"{idx}. [{display_title}]({source_url}) · *{mp_name}*\n"

        if web_results:
            start_idx = len(docs) + 1 if docs else 1
            for i, item in enumerate(web_results):
                idx = start_idx + i
                title = item['title']
                display_title = title[:60] + "..." if len(title) > 60 else title
                context_parts.append(f"资料[{idx}] 来源：{item['source']} | 标题：《{item['title']}》\n内容：{item['snippet']}")
                references_md += f"{idx}. [{display_title}]({item['url']}) · *{item['source']}*\n"

        final_context = "\n\n".join(context_parts)
        if not final_context:
            final_context = "没有任何参考资料。"

        # 4. Generate asynchronously
        async def generate():
            # Stuff documents chain expects {"context": docs, "input": query}
            # Since we manually formatted context, we can just pass docs for internal chain format
            # BUT wait, question_answer_chain takes `docs` objects to format them using its own prompt!
            # Let's bypass question_answer_chain and just use the LLM directly with our prompt.
            # Use requested model
            current_llm = get_llm(request.model)
            formatted_prompt = prompt.format_messages(context=final_context, input=request.message)
            async for chunk in current_llm.astream(formatted_prompt):
                if chunk.content:
                    yield chunk.content
            
            # Yield references at the end
            if docs or web_results:
                yield references_md
                    
        return StreamingResponse(generate(), media_type="text/event-stream")
    except Exception as e:
        api_logger.error(f"Chat error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
