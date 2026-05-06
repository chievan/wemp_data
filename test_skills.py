import os
import sys
from pathlib import Path
import json
import re
import subprocess

# 模拟 api.core.config 中的路径计算逻辑
# 假设脚本在项目根目录运行
PROJECT_ROOT = Path(__file__).parent.resolve()
BRAIN_DIR = PROJECT_ROOT / "brain"
SKILLS_DIR = BRAIN_DIR / "skills"

print("="*50)
print("🔍 WEMP 技能系统诊断工具")
print("="*50)

print(f"\n[1] 路径检测:")
print(f"    项目根目录: {PROJECT_ROOT}")
print(f"    Brain 目录: {BRAIN_DIR}  [{'✅ 存在' if BRAIN_DIR.exists() else '❌ 不存在'}]")
print(f"    Skills 目录: {SKILLS_DIR} [{'✅ 存在' if SKILLS_DIR.exists() else '❌ 不存在'}]")

print(f"\n[2] 技能扫描:")
if SKILLS_DIR.exists():
    items = os.listdir(SKILLS_DIR)
    skills = [d for d in items if os.path.isdir(SKILLS_DIR / d)]
    print(f"    找到技能文件夹数量: {len(skills)}")
    if skills:
        print(f"    前 5 个技能预览:")
        for s in skills[:5]:
            print(f"      - {s}")
else:
    print("    无法扫描，目录不存在。")

print(f"\n[3] 环境与命令检测:")
try:
    # 1. 检查 node/npm
    node_v = subprocess.run("node -v", shell=True, capture_output=True, text=True).stdout.strip()
    npm_v = subprocess.run("npm -v", shell=True, capture_output=True, text=True).stdout.strip()
    print(f"    Node 版本: {node_v if node_v else '❌ 未找到'}")
    print(f"    NPM 版本:  {npm_v if npm_v else '❌ 未找到'}")
    
    # 2. 尝试执行 npx clawhub
    print(f"    正在测试 npx clawhub search finance...")
    cmd = "npx clawhub search finance"
    res = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=BRAIN_DIR, timeout=15)
    
    if res.returncode == 0:
        print("    ✅ ClawHub 搜索命令执行成功！")
        print("    搜索结果片段:")
        print("-" * 30)
        print("\n".join(res.stdout.split('\n')[:5]))
        print("-" * 30)
    else:
        print(f"    ❌ ClawHub 执行失败 (Return Code: {res.returncode})")
        print(f"    错误输出: {res.stderr}")
except subprocess.TimeoutExpired:
    print("    ❌ 搜索测试超时（可能是网络问题或 ClawHub 响应慢）")
except Exception as e:
    print(f"    ❌ 诊断异常: {e}")

print("\n" + "="*50)
print("💡 提示: 如果路径正确但技能列表为 0，请检查 brain/skills 是否有子文件夹。")
print("💡 提示: 如果 npx 报错，请确保服务器已安装 Node.js。")
print("="*50)
