#!/usr/bin/env python3
import os
import requests
import dolphindb as ddb
import pandas as pd
from openai import OpenAI
from pathlib import Path
import yaml
from datetime import datetime, timedelta
import sys

def expand_env(obj):
    if isinstance(obj, dict):
        return {k: expand_env(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [expand_env(v) for v in obj]
    if isinstance(obj, str):
        # 支持 ${VAR:-default} 语法
        import re
        match = re.match(r'\$\{(.*):-(.*)\}', obj)
        if match:
            env_var, default_val = match.groups()
            return os.environ.get(env_var, default_val)
        return os.path.expandvars(obj)
    return obj

def load_config(path_str):
    path = Path(path_str)
    return expand_env(yaml.safe_load(path.read_text("utf-8")) or {})

def fetch_latest_articles(cfg, hours=24):
    """从 DolphinDB 获取过去 N 小时的文章"""
    ddb_cfg = cfg["dolphindb"]
    sess = ddb.session()
    sess.connect(
        host=ddb_cfg["host"],
        port=int(ddb_cfg["port"]),
        userid=ddb_cfg["user"],
        password=ddb_cfg["password"],
    )
    
    script = f"""
    select top 100 title, mp_name, pub_time, content_clean, source_url 
    from loadTable("{ddb_cfg['database']}", `{ddb_cfg['articles_table']}) 
    order by pub_time desc
    """
    df = sess.run(script)
    sess.close()
    
    if df is None or df.empty:
        return pd.DataFrame()
        
    df['pub_time'] = pd.to_datetime(df['pub_time'])
    cutoff_time = datetime.now() - timedelta(hours=hours)
    recent_df = df[df['pub_time'] >= cutoff_time]
    return recent_df

def generate_morning_briefing(df: pd.DataFrame, api_key: str) -> str:
    """调用 LLM 生成固收晨报"""
    if df.empty:
        return "过去24小时内没有监控到新的公众号文章更新。"
        
    import re
    context_parts = []
    link_map = {}
    
    for i, row in df.iterrows():
        idx = str(i + 1)
        title = str(row['title'])
        mp = str(row['mp_name'])
        text = str(row['content_clean'])[:1500] 
        url = str(row.get('source_url', '#'))
        if not url or url == 'nan':
            url = '#'
            
        context_parts.append(f"[{idx}] 来源：{mp} | 标题：《{title}》\n内容：{text}")
        link_map[idx] = f"[{mp}]({url})"
        
    context = "\n\n---\n\n".join(context_parts)
    today_str = datetime.now().strftime("%Y-%m-%d")
    system_prompt = f"""你是一位资深的【固收首席分析师】。基于资料回答，控制在1000字内。使用[序号]标注来源。"""

    client = OpenAI(base_url="https://api.deepseek.com", api_key=api_key)
    response = client.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"请根据以下最新文章生成晨报：\n\n{context}"},
        ],
        temperature=0.3,
    )
    ai_content = response.choices[0].message.content
    def replacer(match):
        idx_str = match.group(1)
        if idx_str in link_map:
            return f"（{link_map[idx_str]}）"
        return match.group(0)
    return re.sub(r'\[(\d+)\]', replacer, ai_content)

def send_webhook(markdown_content: str, webhook_url: str):
    if not webhook_url:
        print("未配置 WEBHOOK_URL")
        return
    payload = {"msgtype": "markdown", "markdown": {"content": markdown_content}}
    headers = {'Content-Type': 'application/json'}
    try:
        resp = requests.post(webhook_url, json=payload, headers=headers)
        if resp.json().get("errcode", 0) != 0:
            print(f"❌ Webhook 报错: {resp.json()}")
        else:
            print("✅ Webhook 发送成功！")
    except Exception as e:
        print(f"❌ Webhook 异常: {e}")

def setup_logging():
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"briefing_{datetime.now().strftime('%Y%m%d')}.log"
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(log_file, encoding="utf-8"), logging.StreamHandler()]
    )
    return logging.getLogger("briefing")

logger = setup_logging()

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--hours", type=int, default=24)
    args = parser.parse_args(sys.argv[1:] if len(sys.argv) > 1 else [])

    cfg = load_config(str(args.config))
    
    # 优先从 config 读取
    deepseek_key = cfg.get("api_keys", {}).get("deepseek", "").strip()
    if not deepseek_key:
        deepseek_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        
    if not deepseek_key:
        logger.error("❌ DEEPSEEK_API_KEY 缺失")
        sys.exit(1)
        
    logger.info("正在拉取文章...")
    recent_articles = fetch_latest_articles(cfg, hours=args.hours)
    if not recent_articles.empty:
        logger.info("正在生成晨报...")
        briefing = generate_morning_briefing(recent_articles, deepseek_key)
        webhook_url = os.environ.get("WEBHOOK_URL", "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=a1171db7-0b2b-4b7e-8fbe-edc0599f1725") 
        send_webhook(briefing, webhook_url)
    else:
        logger.info("今日无新文章")

if __name__ == "__main__":
    main()
