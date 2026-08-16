"""
tempfile 兼容模块（tempfile_compat）
====================================

背景：
    在受控运行环境（如 DSH 文件沙箱）中，tempfile.mkdtemp() 创建的临时目录
    会被设置严格 ACL，导致目录内后续写文件 / rename / 子目录创建全部
    PermissionError [WinError 5]（实测：icacls 都无法读取该目录 ACL）。
    而 os.makedirs() 创建的目录不受影响。

方案：
    提供 mkdtemp 的等价实现：用 os.makedirs + 随机名创建普通目录，
    行为与标准库一致（原子性：UUID 名 + O_EXCL 语义的 os.mkdir 保证不冲突；
    权限：创建后 chmod 0o700）。
    该实现与环境无关——在正常环境同样可用，因此可安全内置。

接线：
    在 FastAPI 入口（app/main.py）与 Celery Worker 入口
    （app/tasks/celery_app.py）模块加载时调用 apply_tempfile_compat()，
    一次性替换 tempfile.mkdtemp；tempfile.TemporaryDirectory 内部调用
    mkdtemp，因此自动受惠；其清理逻辑（shutil.rmtree）对普通目录有效。
"""

import os
import tempfile
import uuid

_ORIG_MKDTEMP = tempfile.mkdtemp
_APPLIED = False


def _safe_mkdtemp(suffix=None, prefix=None, dir=None):  # noqa: A002 - 与标准库签名一致
    """
    mkdtemp 的安全等价实现

    Args:
        suffix: 文件名后缀
        prefix: 文件名前缀
        dir: 父目录（None 使用系统临时目录）

    Returns:
        str: 新建的临时目录路径
    """
    base_dir = dir or tempfile.gettempdir()
    os.makedirs(base_dir, exist_ok=True)
    last_exc = None
    for _ in range(200):
        name = (prefix or "tmp") + uuid.uuid4().hex + (suffix or "")
        path = os.path.join(base_dir, name)
        try:
            os.mkdir(path)
        except FileExistsError as e:
            last_exc = e
            continue
        except FileNotFoundError as e:
            # 父目录被并发清理等极端情况
            last_exc = e
            continue
        try:
            # 与标准库 mkdtemp(0o700) 对齐；Windows 上失败不影响功能
            os.chmod(path, 0o700)
        except Exception:
            pass
        return path
    raise OSError(f"无法创建临时目录（{base_dir}）: {last_exc}")


def apply_tempfile_compat() -> bool:
    """
    将 tempfile.mkdtemp 替换为安全实现（幂等）

    Returns:
        bool: 本次是否执行了替换（重复调用返回 False）
    """
    global _APPLIED
    if _APPLIED:
        return False
    tempfile.mkdtemp = _safe_mkdtemp  # type: ignore[assignment]
    _APPLIED = True
    return True


def restore_tempfile_compat() -> None:
    """恢复原始 mkdtemp（测试用）"""
    global _APPLIED
    tempfile.mkdtemp = _ORIG_MKDTEMP  # type: ignore[assignment]
    _APPLIED = False
