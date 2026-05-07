from langchain_community.embeddings import DashScopeEmbeddings
from api.core.config import settings

embeddings = DashScopeEmbeddings(
    dashscope_api_key=settings.EMBEDDING_API_KEY,
    model=settings.EMBEDDING_MODEL
)
