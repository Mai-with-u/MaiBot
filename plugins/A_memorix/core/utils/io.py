"""
IO Utilities

提供原子文件写入等IO辅助功能。
"""

import os
import shutil
import contextlib
from pathlib import Path
from typing import Union

@contextlib.contextmanager
def atomic_write(file_path: Union[str, Path], mode: str = "w", encoding: str = None, **kwargs):
    """
    原子文件写入上下文管理器
    
    原理：
    1. 写入 .tmp 临时文件
    2. 写入成功后，使用 os.replace 原子替换目标文件
    3. 如果失败，自动删除临时文件
    
    Args:
        file_path: 目标文件路径
        mode: 打开模式 ('w', 'wb' 等)
        encoding: 编码
        **kwargs: 传给 open() 的其他参数
    """
    path = Path(file_path)
    # 确保父目录存在
    path.parent.mkdir(parents=True, exist_ok=True)
    
    # 临时文件路径
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    
    try:
        with open(tmp_path, mode, encoding=encoding, **kwargs) as f:
            yield f
            
            # 确保写入磁盘
            f.flush()
            os.fsync(f.fileno())
            
        # 原子替换 (Windows下可能需要先删除目标文件，但 os.replace 在 Py3.3+ 尽可能原子)
        # 注意: Windows 上如果有其他进程占用文件，os.replace 可能会失败
        os.replace(tmp_path, path)
        
    except Exception as e:
        # 清理临时文件
        if tmp_path.exists():
            try:
                os.remove(tmp_path)
            except:
                pass
        raise e

@contextlib.contextmanager
def atomic_save_path(file_path: Union[str, Path]):
    """
    提供临时路径用于原子保存 (针对只接受路径的API，如Faiss)
    
    Args:
        file_path: 最终目标路径
        
    Yields:
        tmp_path: 临时文件路径 (str)
    """
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    
    try:
        yield str(tmp_path)
        
        if Path(tmp_path).exists():
            os.replace(tmp_path, path)
            
    except Exception as e:
        if Path(tmp_path).exists():
            try:
                os.remove(tmp_path)
            except:
                pass
        raise e
