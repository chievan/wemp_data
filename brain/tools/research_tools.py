import os
import yaml
import re
import numpy as np
import pandas as pd
from pathlib import Path
from langchain_core.tools import tool
from openai import OpenAI
from brain.config import cfg
from brain.logic.session_manager import get_ddb_session

# --- 工具集 (Tools) ---

@tool
def search_wemp_library_by_keywords(keywords: str, limit: int = 5):
    """
    通过关键词在研报文库中进行全文搜索。
    当语义搜索失效或需要查找特定词汇（如具体的债券名、人名、政策名）时使用。
    """
    sess = get_ddb_session()
    ddb_cfg = cfg.get("dolphindb", {})
    try:
        # 构建多重 LIKE 语句
        kw_list = keywords.split()
        like_clauses = " and ".join([f"chunk_text like '%{kw}%'" for kw in kw_list])
        
        script = f"""
        t = loadTable("{ddb_cfg['database']}", "{ddb_cfg['chunks_table']}")
        select mp_name, title, pub_time, source_url, chunk_text from t 
        where {like_clauses}
        limit {limit}
        """
        df = sess.run(script)
        if df is None or df.empty:
            return f"未找到包含关键词 '{keywords}' 的文章。"
            
        results = []
        for i, row in df.iterrows():
            results.append(f"[{i+1}] 来源: {row['mp_name']} | 标题: {row['title']} | 时间: {row['pub_time']} | 链接: {row['source_url']}\n【内容摘要】: {row['chunk_text']}\n---")
        return "\n".join(results)
    except Exception as e:
        print(f"⚠️ Keyword search error: {e}")
        return f"关键词搜索出错: {str(e)}"

@tool
def search_wemp_library(query: str, mp_name: str = None, top_k: int = 5):
    """
    在私人研报文库（DolphinDB）中进行语义搜索（向量搜索）。
    如果指定了 mp_name，则只搜索该公众号的内容。
    适合处理模糊的、概念性的问题。
    """
    # 1. 生成向量 (使用单例配置)
    embed_cfg = cfg.get("embedding", {})
    api_key = cfg.get("api_keys", {}).get("dashscope", "")
    
    client = OpenAI(api_key=api_key, base_url=embed_cfg["base_url"])
    resp = client.embeddings.create(
        model=embed_cfg["model"], 
        input=[query],
        dimensions=int(embed_cfg["dimension"]),
        encoding_format="float"
    )
    query_vec = resp.data[0].embedding
    flat_qvec = np.array(query_vec, dtype=np.float32)

    # 2. 获取共享 Session
    sess = get_ddb_session()
    ddb_cfg = cfg.get("dolphindb", {})
    try:
        sess.upload({"queryVec": flat_qvec})
        
        # 动态构建 WHERE 子句
        where_clause = ""
        if mp_name:
            where_clause = f"where mp_name = '{mp_name}'"
        
        # 3. 构造时效性加权查询
        script = f"""
        qVec = float(queryVec)
        curr_ts = now()
        select top {top_k} mp_name, title, pub_time, source_url, chunk_text, 
               rowCosine(embedding, qVec) / (1.0 + (curr_ts - pub_time)/1000000000/86400 * 0.1) as time_weighted_score 
        from loadTable("{ddb_cfg['database']}", "{ddb_cfg['chunks_table']}")
        {where_clause}
        order by time_weighted_score desc
        """
        df = sess.run(script)
        
        if df is None or df.empty:
            return "文库中暂无相关语义匹配数据。"
            
        results = []
        for i, row in df.iterrows():
            results.append(f"[{i+1}] 来源: {row['mp_name']} | 标题: {row['title']} | 时间: {row['pub_time']} | 链接: {row['source_url']}\n【内容摘要】: {row['chunk_text']}\n---")
        return "\n".join(results)
            
    except Exception as e:
        print(f"⚠️ Vector search error: {e}")
        return f"语义搜索出错: {str(e)}。建议尝试关键词搜索。"
