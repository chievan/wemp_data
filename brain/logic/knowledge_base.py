import os
import numpy as np
import pandas as pd
import dolphindb as ddb
from openai import OpenAI
from brain.config import cfg
from brain.logic.session_manager import get_ddb_session
from brain.tools.web_search_tool import search_bocha
from brain.prompts.knowledge_base import KNOWLEDGE_SYSTEM_PROMPT, KNOWLEDGE_USER_TEMPLATE

def embed_query(query):
    """
    向量化查询文本
    """
    embed_cfg = cfg.get("embedding", {})
    api_key = cfg.get("api_keys", {}).get("dashscope", "").strip()
    client = OpenAI(base_url=embed_cfg["base_url"], api_key=api_key)
    resp = client.embeddings.create(
        model=embed_cfg["model"],
        input=query,
        dimensions=int(embed_cfg["dimension"]),
        encoding_format="float",
    )
    return list(resp.data[0].embedding)

def search_ddb(query_vec, top_k, topic="", mp=""):
    """
    研报向量库搜索
    """
    sess = get_ddb_session()
    ddb_cfg = cfg.get("dolphindb", {})
    try:
        flat_qvec = np.array(query_vec, dtype=np.float32)
        # upload 是为了将本地向量传给服务端进行矩阵运算
        sess.upload({"queryVec": flat_qvec})
        
        filters = []
        if topic: filters.append(f'topic_tags like "%{topic}%"')
        if mp: filters.append(f'mp_name like "%{mp}%"')
        where_clause = ("where " + " and ".join(filters)) if filters else ""
        
        script = f"""
            qVec = float(queryVec)
            select top {top_k} chunk_id, mp_name, title, pub_time, source_url, chunk_text, rowCosine(embedding, qVec) as score
            from loadTable("{ddb_cfg['database']}", "{ddb_cfg['chunks_table']}")
            {where_clause}
            order by score desc
        """
        return sess.run(script)
    except Exception as e:
        print(f"⚠️ Search DDB Error: {e}")
        return pd.DataFrame()
    # 注意：此处不再调用 sess.close()，由管理器统一维护生命周期

def stream_knowledge_analysis(query, top_k=5, topic_filter="", mp_filter="", enable_web=True):
    """
    研报知识库核心逻辑：向量化 -> 检索 -> 融合 -> 总结
    """
    # 1. 向量化
    query_vec = embed_query(query)
    
    # 2. 私有库检索
    ddb_results = search_ddb(query_vec, top_k, topic_filter, mp_filter)
    
    # 3. 博查检索
    web_results = []
    if enable_web:
        web_results = search_bocha(query)
    
    # 4. 资料组装
    context_parts = []
    sources = []
    
    if ddb_results is not None and not ddb_results.empty:
        context_parts.append("### [私有研报库资料]")
        for i, row in ddb_results.iterrows():
            idx = i + 1
            context_parts.append(f"资料[{idx}] 来源：{row['mp_name']} | 标题：《{row['title']}》\n内容：{row['chunk_text']}")
            sources.append({"title": row['title'], "url": row['source_url'], "type": f"私有库·{row['mp_name']}"})
    
    if web_results:
        context_parts.append("\n### [博查全网实时资讯]")
        start_idx = len(ddb_results) + 1 if (ddb_results is not None and not ddb_results.empty) else 1
        for i, item in enumerate(web_results):
            idx = start_idx + i
            context_parts.append(f"资料[{idx}] 来源：{item['source']} | 标题：《{item['title']}》\n内容：{item['snippet']}")
            sources.append({"title": item['title'], "url": item['url'], "type": f"全网实时·{item['source']}"})

    context = "\n\n".join(context_parts)
    
    # 5. AI 调用 (使用单例配置)
    api_key = cfg.get("api_keys", {}).get("deepseek", "")
    client = OpenAI(base_url="https://api.deepseek.com/v1", api_key=api_key)
    response = client.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {"role": "system", "content": KNOWLEDGE_SYSTEM_PROMPT},
            {"role": "user", "content": KNOWLEDGE_USER_TEMPLATE.format(context=context, query=query)}
        ],
        stream=True
    )
    
    full_response = ""
    for chunk in response:
        delta = chunk.choices[0].delta.content
        if delta:
            full_response += delta
            yield delta, full_response, sources, context_parts
