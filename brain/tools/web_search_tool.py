import os
import requests
from brain.config import cfg

def search_bocha(query: str, count: int = 8, freshness: str = "noLimit"):
    """
    使用博查 (Bocha AI) API 进行全网检索
    """
    api_key = cfg.get("api_keys", {}).get("bocha_search", "").strip()
    if not api_key:
        api_key = os.environ.get("BOCHA_SEARCH_API_KEY", "").strip()
    
    if not api_key:
        return {"error": "未找到 BOCHA_SEARCH_API_KEY，请在 config.yaml 中配置。"}

    url = "https://api.bochaai.com/v1/web-search"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "query": query,
        "freshness": freshness,
        "summary": True,  # 开启博查的 RAG 摘要功能
        "count": count
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        # 博查返回结构：data -> webPages -> value
        raw_results = data.get("data", {}).get("webPages", {}).get("value", [])
        
        results = []
        seen_titles = set()
        seen_snippets = set()

        for item in raw_results:
            title = item.get("name", "").strip()
            url = item.get("url", "")
            snippet = item.get("summary") or item.get("snippet") or ""
            
            # 极简去重逻辑：标题完全相同，或摘要前 30 个字符相同（排除空摘要）
            snippet_key = snippet[:30] if len(snippet) > 30 else snippet
            
            if title in seen_titles:
                continue
            if snippet_key and snippet_key in seen_snippets:
                continue
            
            seen_titles.add(title)
            if snippet_key:
                seen_snippets.add(snippet_key)

            results.append({
                "title": title,
                "url": url,
                "snippet": snippet,
                "source": item.get("siteName", "博查全网检索")
            })
        return results
    except Exception as e:
        return {"error": f"博查检索请求失败: {str(e)}"}
