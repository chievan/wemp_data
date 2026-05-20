import uuid
from datetime import datetime
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
        """
        专用：向 DolphinDB 写入向量块。
        metadatas 必须包含 schema 要求的关键字段：article_id, mp_name, title, pub_time 等。
        """
        import pandas as pd
        import numpy as np
        
        if metadatas is None:
            raise ValueError("metadatas is required for DolphinDBVectorStore")
            
        # 1. 生成 Embeddings (分批处理，兼容 DashScope 单次最高 10 条的限制)
        texts_list = list(texts)
        vectors = []
        batch_size = 10
        for i in range(0, len(texts_list), batch_size):
            batch = texts_list[i : i + batch_size]
            vectors.extend(self.embedding.embed_documents(batch))
        
        # 2. 准备数据框
        rows = []
        for i, (text, meta, vec) in enumerate(zip(texts, metadatas, vectors)):
            pub_ts = pd.to_datetime(meta.get("pub_time") or datetime.now())
            if getattr(pub_ts, "tzinfo", None):
                pub_ts = pub_ts.tz_localize(None)
            pub_month = pd.Timestamp(year=pub_ts.year, month=pub_ts.month, day=1)
            
            row = {
                "pub_month": pub_month,
                "chunk_id": meta.get("chunk_id", str(uuid.uuid4())),
                "article_id": meta.get("article_id", "manual_upload"),
                "content_hash": meta.get("content_hash", ""),
                "mp_id": meta.get("mp_id", "user"),
                "mp_name": meta.get("mp_name", "用户上传"),
                "title": meta.get("title", "未命名文档"),
                "pub_time": pub_ts,
                "source_url": meta.get("source_url", ""),
                "topic_tags": meta.get("topic_tags", "未分类"),
                "chunk_no": meta.get("chunk_no", i + 1),
                "chunk_text": text,
                "chunk_len": len(text),
                "embedding": vec,
                "ingested_at": pd.Timestamp.now(),
            }
            rows.append(row)
            
        # 3. 写入 DolphinDB
        df = pd.DataFrame(rows)
        # 处理嵌套的 embedding 列表为 arrayVector
        embeddings = df["embedding"].tolist()
        n = len(embeddings)
        dim = len(embeddings[0])
        flat = np.array(embeddings, dtype=np.float32).flatten()
        
        meta_df = df.drop(columns=["embedding"]).copy()
        meta_df.attrs["__DolphinDB_Type__"] = {"pub_month": ddb.settings.DT_MONTH}
        
        var_meta = f"meta_{uuid.uuid4().hex[:8]}"
        var_flat = f"flat_{uuid.uuid4().hex[:8]}"
        self.sess.upload({var_meta: meta_df, var_flat: flat})
        
        script = f"""
        idx = (1..{n}) * {dim}
        embArr = arrayVector(idx, {var_flat})
        {var_meta}[`embedding] = embArr
        // 确保列顺序一致
        reorderColumns!({var_meta}, `pub_month`chunk_id`article_id`content_hash`mp_id`mp_name`title`pub_time`source_url`topic_tags`chunk_no`chunk_text`chunk_len`embedding`ingested_at)
        loadTable("{self.database_path}", "{self.table_name}").append!({var_meta})
        """
        try:
            self.sess.run(script)
        finally:
            self.sess.run(f"undef([`{var_meta}, `{var_flat}])")
            
        return [r["chunk_id"] for r in rows]

    def similarity_search(self, query: str, k: int = 4, filter: Optional[dict] = None, **kwargs: Any) -> List[Document]:
        # Embed the query and convert to float32 numpy array for Dolphindb compatibility
        import numpy as np
        import re
        
        query_embedding = self.embedding.embed_query(query)
        flat_qvec = np.array(query_embedding, dtype=np.float32)
        
        var_name = f"q_vec_{uuid.uuid4().hex}"
        self.sess.upload({var_name: flat_qvec})
        
        # 构建 Where 子句
        where_clause = ""
        if filter:
            conditions = []
            for key, val in filter.items():
                if isinstance(val, str):
                    conditions.append(f"{key} == '{val}'")
                else:
                    conditions.append(f"{key} == {val}")
            if conditions:
                where_clause = "where " + " and ".join(conditions)

        # 智能历史模式检测：如果查询明确包含历史指标词，则不进行时效性衰减
        has_history_indicator = bool(re.search(r'(20\d{2}|历史|往期|以前|回顾|过去|老数据)', query))
        lambda_decay = 0.0 if has_history_indicator else 0.05

        script = f"""
        qVec = float({var_name})
        curr_ts = now()
        tbl = loadTable("{self.database_path}", "{self.table_name}")
        {f"tbl = select * from tbl {where_clause}" if where_clause else ""}
        res = select *, rowCosine(embedding, qVec) * exp(-{lambda_decay} * (curr_ts - pub_time)/86400000.0) as score from tbl
        select * from res order by score desc limit {k}
        """
        
        try:
            res_df = self.sess.run(script)
        finally:
            self.sess.run(f"undef(`{var_name})")
            
        docs = []
        if res_df is not None and not res_df.empty:
            for _, row in res_df.iterrows():
                metadata = row.to_dict()
                page_content = metadata.pop("chunk_text", "")
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
