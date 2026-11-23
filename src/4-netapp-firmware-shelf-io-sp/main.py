#!/usr/bin/env python3
"""
NetApp Firmware Analysis Skill
主入口脚本，用于解析NetApp ASUP日志并生成Excel报告
"""

import subprocess
import sys
import os
from pathlib import Path

_shared_skill = os.environ.get('SAM_SHARED_SKILL_DIR')
if _shared_skill is not None:
    if not _shared_skill.strip() or not Path(_shared_skill).is_absolute():
        raise ValueError('SAM_SHARED_SKILL_DIR must be a non-empty absolute path')
    _shared_skill = Path(_shared_skill)
else:
    _shared_skill = Path(__file__).resolve().parents[1] / 'NetApp-SAM-Report'
    if not (_shared_skill / 'sam_settings.py').is_file():
        _shared_skill = Path(__file__).resolve().parents[2] / 'skills/NetApp-SAM-Report'
if not (_shared_skill / 'sam_settings.py').is_file():
    raise FileNotFoundError('Shared SAM path settings are missing')
sys.path.insert(0, str(_shared_skill))
from sam_settings import path_setting
from sam_package_compat import require_configuration

def main():
    # 获取当前脚本所在目录
    skill_dir = os.path.dirname(os.path.abspath(__file__))
    # 路径结构: .trae-cn/skills/NetApp Firmware Analysis/
    # 需要找到 Trae Solo Coder/ASUP Analysis/
    project_dir = str(path_setting('legacy_root', Path.home() / 'Desktop/demo/Trae Solo Coder') / 'ASUP Analysis')
    main_script = 'generate_all_final.py'
    
    script_path = os.path.join(project_dir, main_script)
    if not os.path.exists(script_path):
        print(f"错误：未找到主脚本文件 {script_path}")
        print(f"当前路径: {os.getcwd()}")
        print(f"skill_dir: {skill_dir}")
        print(f"project_dir: {project_dir}")
        return 1
    
    # 获取输入目录参数
    input_dir = sys.argv[1] if len(sys.argv) > 1 else ''
    dir_name = os.path.basename(input_dir) if input_dir else ''
    
    # 运行主脚本
    print("=== 开始运行NetApp Firmware Analysis ===")
    os.chdir(project_dir)
    environment = os.environ.copy()
    environment['SAM_SHARED_SKILL_DIR'] = str(_shared_skill)
    environment['PYTHONIOENCODING'] = 'utf-8'
    environment['PYTHONUTF8'] = '1'
    
    if input_dir:
        result = subprocess.run([sys.executable, main_script, input_dir], 
                              capture_output=True,
                              text=True, encoding='utf-8', env=environment)
    else:
        result = subprocess.run([sys.executable, main_script], 
                              capture_output=True,
                              text=True, encoding='utf-8', env=environment)
    
    if result.stdout:
        print(result.stdout)
    
    if result.stderr:
        print("错误输出:", result.stderr)
    
    return result.returncode

if __name__ == '__main__':
    sys.exit(main())
