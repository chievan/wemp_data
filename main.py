#!/usr/bin/env python3
import argparse
import os
import sys
from pathlib import Path

# 强制切换工作目录到项目根目录，确保路径一致性
base_dir = Path(__file__).parent.resolve()
os.chdir(base_dir)

# 将 core 目录加入搜索路径
sys.path.insert(0, str(base_dir / "core"))

def main():
    parser = argparse.ArgumentParser(description="Wemp 投研系统统一入口")
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # 1. ingest 命令
    ingest_parser = subparsers.add_parser("ingest", help="执行全量同步 (API -> COS -> SQLite -> DDB)")
    ingest_parser.add_argument("--config", type=Path, default=Path("config.yaml"), help="配置文件路径")
    ingest_parser.add_argument("--dest-db", type=Path, default=Path("./data/wemp_data.db"), help="目标数据库路径")
    ingest_parser.add_argument("--api-url", default="http://localhost:8001")
    ingest_parser.add_argument("--limit", type=int, help="限制处理文章数量")
    ingest_parser.add_argument("--force", action="store_true", help="强制重新处理")
    ingest_parser.add_argument("--skip-ddb", action="store_true", help="跳过写入 DolphinDB")

    # 2. briefing 命令
    briefing_parser = subparsers.add_parser("briefing", help="生成每日固收晨报并发送 Webhook")
    briefing_parser.add_argument("--config", type=Path, default=Path("config.yaml"), help="配置文件路径")
    briefing_parser.add_argument("--hours", type=int, default=24, help="抓取过去几小时的文章")

    # 3. health 命令
    health_parser = subparsers.add_parser("health", help="检查 SQLite / DolphinDB 数据健康状态")
    health_parser.add_argument("--config", type=Path, default=Path("config.yaml"), help="配置文件路径")
    health_parser.add_argument("--db", type=Path, default=Path("./data/wemp_data.db"), help="SQLite 数据库路径")
    health_parser.add_argument("--with-ddb", action="store_true", help="同时检查 DolphinDB 表计数")
    health_parser.add_argument("--json", action="store_true", help="输出 JSON")

    args = parser.parse_args()

    if args.command == "ingest":
        import ingest_service
        # 确保 --config 被传递给子服务
        new_argv = [sys.argv[0]] + sys.argv[2:]
        if "--config" not in new_argv:
            new_argv.extend(["--config", str(args.config)])
        sys.argv = new_argv
        ingest_service.main()
    elif args.command == "briefing":
        import briefing_service
        new_argv = [sys.argv[0]] + sys.argv[2:]
        if "--config" not in new_argv:
            new_argv.extend(["--config", str(args.config)])
        sys.argv = new_argv
        briefing_service.main()
    elif args.command == "health":
        import health_service
        new_argv = [sys.argv[0]] + sys.argv[2:]
        if "--config" not in new_argv:
            new_argv.extend(["--config", str(args.config)])
        sys.argv = new_argv
        health_service.main()
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
