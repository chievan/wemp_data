# Wemp 投研智能数据门户 (Wemp Data Intelligence Portal)

Wemp 是一款面向专业金融投研领域的**全栈 AI 数据中台**。它实现了从研报采集、自动化清洗、向量化入库到 AI 辅助决策的完整链路。

---

## 🌟 核心功能

*   **研报知识库 (RAG)**: 支持 DeepSeek V4 (Flash/Pro) 与 通义千问 (Qwen-Plus/Max) 多模型切换，集成私有库与全网实时检索。
*   **时间感知 RAG (Temporal RAG)**: 自研毫秒级时效性重排，基于时序大底座 (DolphinDB) 的指数衰减公式，支持智能 Agent 提取时间边界与多分区并行剪裁。
*   **智能投委会**: 基于 LangGraph 的多 Agent 协作系统，模拟专家辩论决策。
*   **数据资产中心**: 全量数据同步、向量化任务控制与研报管理。
*   **系统日志监控**: 独立页面实时监控后端（API、Ingest、Vectorize）日志。

---

## 📂 项目目录结构

```text
wemp_data/
├── api/                # 后端核心服务 (FastAPI)
│   ├── core/           # 核心配置 (数据库连接、配置加载)
│   ├── models/         # 数据模型 (Pydantic & SQLAlchemy)
│   ├── routers/        # 业务路由 (Chat, Ingest, Articles, Logs)
│   ├── services/       # 异步任务处理 (Task Runners)
│   └── main.py         # 后端入口 (含内置任务 Worker)
├── brain/              # AI 决策层 (LangGraph & Tools)
├── core/               # 基础数据清洗与采集逻辑
├── frontend/           # Vue 3 前端工程
├── data/               # SQLite 数据库存放地
├── logs/               # 系统运行日志 (自动生成)
├── config.server.yaml  # 服务器端专用配置文件 (生产环境)
├── main.py             # 命令行运维工具 (健康检查/手动同步)
└── requirements.txt    # 后端依赖清单
```

---

## ⏳ 时间感知 RAG (Temporal RAG)

为解决金融宏观投研中“历史噪音干扰最新行情判定”的痛点，系统实现了一套端到端的时间感知 RAG 方案：

1. **时效性指数衰减算法 (Temporal Exponential Decay)**：
   在 DolphinDB 中执行向量检索时，并非只按 Cosine 距离打分，而是将语义相似度与文档距今的时间天数进行指数融合：
   $$Score_{final} = Score_{semantic} \times e^{-\lambda \Delta t}$$
   *   其中 $\lambda = 0.05$（半衰期约 14 天）。几天前的文章打分小幅降低，数月前的文章打分快速归零，拒绝旧闻干扰。

2. **多分区剪裁硬过滤 (Metadata Hard-Filtering)**：
   *   `wemp_chunks` 向量表以 `pub_month` 作为分区键。
   *   当检索传入 `start_time` / `end_time` 过滤条件时，自动触发 DolphinDB 的**分区剪裁 (Partition Pruning)**，跳过无用分区，将语义匹配计算开销降低数倍。

3. **大模型时间锚点感知 (Time-Aware Agent Prompts)**：
   *   CIO 和专家 Agent 在生成检索关键词时，系统会动态注入当前系统时间（Now）。
   *   Agent 能够理解“近两周”、“上个月”等相对时间概念，并将其转换为绝对时间格式（YYYY-MM-DD）传入检索工具进行精确过滤。

---

## 🚀 生产环境部署 (Ubuntu Server)

项目在服务器上的根目录为 `/opt/wemp_data`，使用内置虚拟环境。

### 1. 环境准备
```bash
cd /opt/wemp_data
# 激活虚拟环境
source .venv/bin/activate
# 安装依赖
pip install -r requirements.txt
```

### 2. 服务启动与管理 (PM2)
推荐使用 PM2 管理后端 API 和前端服务，确保宕机自动重启。

*   **后端 API (`wemp-api`)**:
    ```bash
    # 启动/重启
    pm2 restart wemp-api || pm2 start "uvicorn api.main:app --host 0.0.0.0 --port 8000" --name wemp-api
    ```
*   **前端 UI (`wemp-frontend`)**:
    ```bash
    cd /opt/wemp_data/frontend
    npm run build
    pm2 restart wemp-frontend || pm2 start "serve -s dist -l 8501" --name wemp-frontend
    ```

### 3. 常用运维指令
*   **查看服务状态**: `pm2 list`
*   **查看后端日志**: `pm2 logs wemp-api`
*   **清理日志**: `pm2 flush`
*   **停止所有服务**: `pm2 stop all`

---

## 🛠️ GitHub 私有库同步指南

为了确保安全，请按照以下步骤操作，**务必确认已配置 .gitignore**。

1.  **初始化与关联远程库** (仅需执行一次):
    ```bash
    git init
    # 替换为您的私有库地址
    git remote add origin https://github.com/您的用户名/wemp_data.git
    git branch -M main
    ```

2.  **日常上传指令**:
    ```bash
    git add .
    git commit -m "feat: 研报详情页三栏优化及对话管理功能完善"
    git push -u origin main
    ```

> [!IMPORTANT]
> **安全警告**: 禁止将 `config/config.yaml` (含数据库密钥) 或 `.env` 文件上传至 GitHub。请确保 `.gitignore` 包含这些敏感文件。

---

*Powered by Antigravity AI Engineering - 2026*
