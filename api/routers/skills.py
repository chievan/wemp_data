from fastapi import APIRouter, HTTPException, Query
import os
import json
import re
import subprocess
import shutil
from typing import List
from pydantic import BaseModel
from api.core.config import PROJECT_ROOT
from core.logger import api_logger

router = APIRouter(prefix="/skills", tags=["skills"])

BRAIN_DIR = os.path.abspath(os.path.join(str(PROJECT_ROOT), "brain"))
SKILLS_DIR = os.path.join(BRAIN_DIR, "skills")

class SkillInfo(BaseModel):
    id: str
    name: str
    description: str
    version: str

def try_fix_text(s: str) -> str:
    """终极乱码修复：尝试各种组合还原 UTF-8"""
    if not s: return s
    # 如果字符串里已经包含正常的中文，可能不需要修（或者修坏了）
    # 但由于目前大部分是乱码，我们尝试强制转换
    for enc in ['gbk', 'iso-8859-1', 'cp1252']:
        try:
            # 允许忽略非法字符，防止整行失败
            bytes_data = s.encode(enc, errors='ignore')
            if len(bytes_data) < 2: continue
            decoded = bytes_data.decode('utf-8')
            if len(decoded) > 1: return decoded
        except:
            continue
    return s

@router.get("/debug")
def debug_skills():
    if not os.path.exists(SKILLS_DIR): return {"error": "not found"}
    results = []
    for d_b in os.listdir(SKILLS_DIR.encode('utf-8')):
        d_u = d_b.decode('utf-8', errors='ignore')
        results.append({
            "raw": d_u,
            "fixed": try_fix_text(d_u),
            "files": os.listdir(os.path.join(SKILLS_DIR.encode('utf-8'), d_b))
        })
    return results

@router.get("", response_model=List[SkillInfo])
def get_skills():
    if not os.path.exists(SKILLS_DIR):
        return []
    
    installed = []
    try:
        # 使用字节模式扫描，确保路径访问不受乱码字符串影响
        for d_bytes in os.listdir(SKILLS_DIR.encode('utf-8')):
            d_u = d_bytes.decode('utf-8', errors='ignore')
            path_bytes = os.path.join(SKILLS_DIR.encode('utf-8'), d_bytes)
            
            if os.path.isdir(path_bytes):
                # 默认值：尝试修复目录名作为显示名
                display_name = try_fix_text(d_u)
                info = {
                    "id": d_u,
                    "name": display_name,
                    "description": "无描述",
                    "version": "未知"
                }
                
                # 深度扫描文件夹内部
                try:
                    inner_files = os.listdir(path_bytes)
                    # 1. 寻找描述文件
                    target_md = None
                    for f in inner_files:
                        f_lower = f.decode('utf-8', errors='ignore').lower()
                        if f_lower in ['skill.md', 'readme.md', '_meta.json']:
                            target_md = f
                            break
                    
                    if target_md:
                        md_path = os.path.join(path_bytes, target_md)
                        with open(md_path, "r", encoding="utf-8", errors='ignore') as f_obj:
                            content = f_obj.read()
                            # 如果是 JSON
                            if target_md.decode().endswith('.json'):
                                try:
                                    data = json.loads(content)
                                    if 'name' in data: info['name'] = data['name']
                                    if 'description' in data: info['description'] = data['description']
                                    if 'version' in data: info['version'] = str(data['version'])
                                except: pass
                            else:
                                # 如果是 MD，解析 Frontmatter 或标题
                                n_m = re.search(r'name:\s*(.*)', content, re.I)
                                if n_m: info["name"] = n_m.group(1).strip()
                                d_m = re.search(r'description:\s*(.*)', content, re.I)
                                if d_m: info["description"] = d_m.group(1).strip()
                                v_m = re.search(r'version:\s*(.*)', content, re.I)
                                if v_m: info["version"] = v_m.group(1).strip()
                except Exception as e:
                    api_logger.error(f"Deep scan error for {d_u}: {e}")
                
                installed.append(info)
    except Exception as e:
        api_logger.error(f"Scan failed: {e}")
        
    return installed

@router.get("/search")
def search_skills(q: str = Query(...)):
    try:
        res = subprocess.run(f"npx clawhub search {q}", shell=True, capture_output=True, text=True, cwd=BRAIN_DIR, timeout=20)
        output = res.stdout
        results = []
        for line in output.strip().split('\n'):
            if line.strip():
                clean = re.sub(r'\x1b\[[0-9;]*m', '', line)
                parts = re.split(r'\s{2,}', clean.strip())
                if parts:
                    results.append({"slug": parts[0], "description": parts[1] if len(parts)>1 else ""})
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/install/{slug}")
def install_skill(slug: str):
    try:
        subprocess.run(f"npx clawhub install {slug}", shell=True, capture_output=True, text=True, cwd=BRAIN_DIR, timeout=60)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/{skill_id}")
def uninstall_skill(skill_id: str):
    path = os.path.join(SKILLS_DIR, skill_id)
    if os.path.exists(path):
        shutil.rmtree(path)
        return {"status": "success"}
    return {"status": "not_found"}

@router.get("/{skill_id}/content")
def get_skill_content(skill_id: str):
    """获取技能的详细 Markdown 介绍内容"""
    path = os.path.join(SKILLS_DIR, skill_id)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Skill not found")
    
    # 优先寻找 SKILL.md
    for f in ["SKILL.md", "skill.md", "README.md", "readme.md"]:
        f_path = os.path.join(path, f)
        if os.path.exists(f_path):
            try:
                with open(f_path, "r", encoding="utf-8", errors='ignore') as f_obj:
                    return {"content": f_obj.read()}
            except Exception as e:
                api_logger.error(f"Read skill content error: {e}")
    
    return {"content": "暂无详细介绍内容。"}

