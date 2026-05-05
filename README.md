# Wemp 投研智能数据门户 (Wemp Data Intelligence Portal)

Wemp 是一款面向专业金融投研领域的**全栈 AI 数据中台**。它实现了从研报采集、自动化清洗、向量化入库到 AI 辅助决策的完整链路。

---

## 🌟 核心功能

* **研报知识库 (RAG)**: 支持 DeepSeek V4 (Flash/Pro) 与 通义千问 (Qwen-Plus/Max) 多模型切换，集成私有库与全网实时检索。
* **智能投委会**: 基于 LangGraph 的多 Agent 协作系统，模拟专家辩论决策。
* **数据资产中心**: 全量数据同步、向量化任务控制与研报管理。
* **系统日志监控**: 独立页面实时监控后端（API、Ingest、Vectorize）日志。

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

## 🚀 服务器部署与启动指南

### 1. 准备工作 (仅第一次)

```bash
# 激活环境
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 释放旧端口 (如有)
kill -9 $(lsof -t -i:8501)
```

### 2. 后端启动 (API + Worker)

后端不仅提供接口，还负责在后台运行同步和向量化任务。

* **开发者模式** (用于调试):

  ```bash
  export WEMP_CONFIG=config.server.yaml
  uvicorn api.main:app --host 0.0.0.0 --port 8000
  ```

* **生产模式** (使用 PM2 后台持久运行):

  ```bash
  pm2 start "uvicorn api.main:app --host 0.0.0.0 --port 8000 --workers 4" --name wemp-api
  ```

### 3. 前端部署 (UI)

前端必须先编译再部署，推荐使用 **8501** 端口。

1. **修改 IP**: 编辑 `frontend/.env.production`，将 `VITE_API_BASE` 改为服务器公网 IP。
2. **打包**: `cd frontend && npm install && npm run build`。
3. **使用 PM2 快速启动**:

   ```bash
   npm install -g serve
   pm2 start "serve -s dist -l 8501" --name wemp-frontend
   ```

   *(或者使用 Nginx 托管 `dist` 目录)*

---

## 🛠️ 运维与上传建议

### 上传服务器 (本地打包)

```bash
# 排除无关文件打包
tar -czvf wemp_portal.tar.gz \
    --exclude='.venv' --exclude='node_modules' \
    --exclude='logs/*' --exclude='data/*.db' \
    --exclude='.git' --exclude='frontend/dist' .
```

### 管理命令

* **查看运行状态**: `pm2 list`
* **查看实时日志**: `pm2 logs wemp-api`
* **手动任务**: `python3 main.py ingest --config config.server.yaml`
* **健康检查**: `python3 main.py health --config config.server.yaml`

---

*Powered by Antigravity AI Engineering*
