"""
7z 压缩包解压模块
使用 py7zr 库进行选择性解压
"""
import os
import py7zr
import tempfile
import shutil
import gc
from typing import List, Dict, Tuple


class ArchiveExtractor:
    """ASUP 7z 压缩包解压器"""

    def __init__(self):
        self._temp_dirs = []

    def cleanup(self):
        for d in self._temp_dirs:
            if os.path.exists(d):
                shutil.rmtree(d, ignore_errors=True)
        self._temp_dirs.clear()

    def extract_files(self, archive_path: str, target_files: List[str]) -> Dict[str, str]:
        """
        从7z中选择性提取指定文件到临时目录

        Args:
            archive_path: 7z文件路径
            target_files: 需要提取的文件名列表

        Returns:
            dict: {文件名: 临时文件完整路径}，不存在的文件值为空字符串
        """
        temp_dir = tempfile.mkdtemp(prefix='asup_')
        self._temp_dirs.append(temp_dir)
        result = {f: '' for f in target_files}

        try:
            with py7zr.SevenZipFile(archive_path, mode='r') as archive:
                file_list = archive.getnames()
                to_extract = []
                for target in target_files:
                    for f in file_list:
                        if f.lower().endswith(target.lower()) or f == target:
                            to_extract.append(f)
                            break
                if not to_extract:
                    return result
                archive.extract(targets=to_extract, path=temp_dir)

            # 查找提取后的文件路径
            for root, _, files in os.walk(temp_dir):
                for fname in files:
                    for target in target_files:
                        if fname.lower() == target.lower():
                            result[target] = os.path.join(root, fname)
        except Exception as e:
            print(f"解压出错 {archive_path}: {e}")

        return result

    def extract_and_cleanup(self, archive_path: str, target_files: List[str]) -> Dict[str, str]:
        """提取文件后返回路径，调用者负责使用后删除文件"""
        return self.extract_files(archive_path, target_files)
