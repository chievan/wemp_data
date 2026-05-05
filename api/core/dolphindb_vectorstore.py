import uuid
from typing import Any, Iterable, List, Optional
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import VectorStore
import dolphindb as ddb

class DolphinDBVectorStore(VectorStore):
    """
    LangChain VectorStore adapter for DolphinDB.
    Specifically customized for the wemp_data chunks table.
    """
    def __init__(self, session: ddb.session, embedding: Embeddings, database_path: str, table_name: str):
        self.sess = session
        self.embedding = embedding
        self.database_path = database_path
        self.table_name = table_name

    def add_texts(self, texts: Iterable[str], metadatas: Optional[List[dict]] = None, **kwargs: Any) -> List[str]:
        # Currently, ingestion is handled by ingest_service.py which writes directly to DolphinDB.
        # So we leave this not implemented or simple log.
        raise NotImplementedError("For wemp_data, insertion is handled by the dedicated ingest service.")

    def similarity_search(self, query: str, k: int = 4, **kwargs: Any) -> List[Document]:
        # Embed the query
        query_embedding = self.embedding.embed_query(query)
        
        # Upload query vector to DolphinDB session
        # Generate a unique variable name to avoid conflicts if queried concurrently
        # (Though ddb.session is not thread-safe by default, we'll keep it simple for now)
        var_name = f"q_vec_{uuid.uuid4().hex}"
        self.sess.upload({var_name: query_embedding})
        
        # Build exact search SQL using each(dot{, q_vec}, embedding)
        # Assuming the text column is 'chunk_text'
        script = f"""
        tbl = loadTable("{self.database_path}", "{self.table_name}")
        res = select *, each(dot{{, {var_name}}}, embedding) as score from tbl
        select * from res order by score desc limit {k}
        """
        
        try:
            res_df = self.sess.run(script)
        finally:
            # Clean up the uploaded variable
            self.sess.run(f"undef(`{var_name})")
            
        docs = []
        if res_df is not None and not res_df.empty:
            for _, row in res_df.iterrows():
                metadata = row.to_dict()
                page_content = metadata.pop("chunk_text", "")
                
                # Remove embedding from metadata to save memory
                if "embedding" in metadata:
                    del metadata["embedding"]
                
                docs.append(Document(page_content=page_content, metadata=metadata))
                
        return docs

    @classmethod
    def from_texts(
        cls,
        texts: List[str],
        embedding: Embeddings,
        metadatas: Optional[List[dict]] = None,
        **kwargs: Any,
    ) -> "DolphinDBVectorStore":
        raise NotImplementedError("Not applicable for read-only DolphinDB setup.")
