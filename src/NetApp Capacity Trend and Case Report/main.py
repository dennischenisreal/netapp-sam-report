from pathlib import Path
import os
import sys
_package_shared = os.environ.get('SAM_SHARED_SKILL_DIR') or str(Path(__file__).resolve().parents[1] / 'NetApp-SAM-Report')
if not Path(_package_shared).is_absolute() or not (Path(_package_shared) / 'sam_package_compat.py').is_file():
    raise FileNotFoundError('Explicit candidate shared runtime directory is required')
sys.path.insert(0, _package_shared)
from sam_package_compat import package_path

"""
NetApp Capacity Trend and Case Report
生成容量趋势和Case分析报告
"""

import sys
import os
from pathlib import Path

current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

def main():
    """主函数"""
    print("="*80)
    print("NetApp Capacity Trend and Case Report")
    print("容量趋势和Case分析报告生成工具")
    print("="*80)
    
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'NetApp-SAM-Report'))
    from sam_settings import path_setting
    source_base = str(path_setting('reports_root', Path.home() / 'Documents' / 'SAM report' / 'Report'))
    output_base = str(path_setting('trend_output_root', Path(__file__).resolve().parents[2] / '.local/trends'))
    
    if len(sys.argv) > 1:
        source_base = sys.argv[1]
    if len(sys.argv) > 2:
        output_base = sys.argv[2]
    
    print(f"\n源目录: {source_base}")
    print(f"输出目录: {output_base}")
    
    if not os.path.exists(source_base):
        print(f"\n错误: 源目录不存在 - {source_base}")
        return
    
    os.makedirs(output_base, exist_ok=True)
    
    print("\n" + "="*80)
    print("Step 1: 生成容量趋势报告")
    print("="*80)
    
    try:
        from generate_capacity_report import generate_all_capacity_reports
        generate_all_capacity_reports(source_base, output_base)
        print("容量趋势报告生成完成！")
    except Exception as e:
        print(f"生成容量趋势报告时出错: {e}")
    
    print("\n" + "="*80)
    print("Step 2: 生成Case分析报告")
    print("="*80)
    
    try:
        from generate_case_report import generate_all_case_reports
        generate_all_case_reports(source_base, output_base)
        print("Case分析报告生成完成！")
    except Exception as e:
        print(f"生成Case分析报告时出错: {e}")
    
    print("\n" + "="*80)
    print("Step 3: 生成设备盘点分析报告")
    print("="*80)
    
    try:
        from generate_device_report import generate_all_device_reports
        generate_all_device_reports(source_base, output_base)
        print("设备盘点分析报告生成完成！")
    except Exception as e:
        print(f"生成设备盘点分析报告时出错: {e}")
    
    print("\n" + "="*80)
    print("所有报告生成完成！")
    print("="*80)
    print(f"\n输出目录: {output_base}")

if __name__ == "__main__":
    main()
