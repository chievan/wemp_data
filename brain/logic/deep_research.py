import os
import re
from openai import OpenAI
from brain.config import cfg
from brain.tools.research_tools import search_wemp_library
from brain.tools.web_search_tool import search_bocha
from brain.prompts.deep_research import DEEP_RESEARCH_SYSTEM_PROMPT, DEEP_RESEARCH_USER_TEMPLATE

def stream_deep_research(topic, report_type="标准报告", detail_level="标准"):
    """
    深度研究核心逻辑（大脑层）
    负责：检索、Prompt 编排、AI 调用、数据流返回
    """
    api_key = cfg.get("api_keys", {}).get("deepseek", "")
    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com/v1")
    
    # 1. 深度检索数据
    private_data = search_wemp_library.invoke({"query": topic, "top_k": 10})
    web_data_raw = search_bocha(topic, count=8)
    
    sources = []
    # 提取私有库来源 (支持更稳健的正则匹配)
    private_items = re.findall(r"\[(\d+)\] 来源: (.*?) \| 标题: (.*?) \| 时间:.*? \| 链接: (.*?)\n", private_data)
    for _, mp, t_title, url in private_items:
        sources.append({"title": f"{mp}·{t_title}", "url": url, "type": "私有库"})

    web_data = ""
    if isinstance(web_data_raw, list):
        for item in web_data_raw:
            web_data += f"来源：{item['source']} | 标题：{item['title']}\n摘要：{item['snippet']}\n\n"
            sources.append({"title": item["title"], "url": item["url"], "type": "全网实时"})

    # 2. 编排提示词 (从 prompts 库获取)
    prompt = DEEP_RESEARCH_USER_TEMPLATE.format(
        topic=topic,
        report_type=report_type,
        detail_level=detail_level,
        private_data=private_data,
        web_data=web_data
    )

    # 3. 发起 AI 调用
    response = client.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {"role": "system", "content": DEEP_RESEARCH_SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ],
        stream=True
    )
    
    # 4. 返回迭代器：(当前字符, 最终完整内容, 来源列表)
    full_report = ""
    for chunk in response:
        content = chunk.choices[0].delta.content
        if content:
            full_report += content
            yield content, full_report, sources
