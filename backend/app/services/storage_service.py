"""
文件存储服务模块

本模块提供统一的文件存储抽象层，支持本地文件系统和 MinIO 对象存储两种后端。
上层代码通过统一接口操作文件，无需关心底层存储实现。

主要职责：
- 文件上传（支持文件路径和字节数据两种方式）
- 文件下载
- 文件内容读取
- 生成访问 URL（MinIO 模式为预签名 URL，本地模式为文件路径）
- 文件删除
- 存储桶初始化

设计决策：
- 使用策略模式，根据 storage_backend 配置自动选择本地或 MinIO 实现
- 本地模式将 bucket 映射为目录，object_name 映射为文件路径，模拟对象存储语义
- _resolve_path 中包含路径遍历安全检查，防止恶意输入访问存储目录之外的文件
- MinIO 客户端延迟导入（在 _get_minio_client 中），本地模式无需安装 minio 包
- 本地模式下 get_presigned_url 返回文件路径字符串，仅供内部使用
"""

import os
import shutil
from pathlib import Path
from typing import Optional

from ..config import get_settings

settings = get_settings()


def _get_storage_root() -> Path:
    """
    获取存储根目录（Vault 根）

    Vault 根目录即本地模式下的存储根，object_name 已包含完整的
    "用户/项目/source|output|history|cache" 结构，直接映射到该根之下。

    Returns:
        Path: Vault 根目录路径
    """
    return settings.get_vault_dir()


def _resolve_path(bucket: str, object_name: str) -> Path:
    """
    将 object_name 映射为本地文件系统的绝对路径

    Vault 结构下 object_name 已含完整路径（bucket 仅对 MinIO 有意义，
    本地模式忽略 bucket 直接映射到 Vault 根，保持单一可浏览目录树）。

    兼容历史数据：早期笔记的 object_name 可能是无 bucket 前缀的相对路径
    （如 {user_id}/{folder_id}/original.md），对应文件实际位于
    "{bucket}/{object_name}" 下；已带 bucket 前缀或新 vault 结构
    （含 source/output/history/cache 段）的路径则无需处理。

    同时执行路径遍历安全检查，确保解析后的路径仍在 Vault 根目录下，
    防止恶意 object_name（如 "../../etc/passwd"）访问存储目录之外的文件。

    Args:
        bucket: 存储桶名称（本地模式仅在兼容旧路径时用于补前缀）
        object_name: 对象名称（映射为相对文件路径）

    Returns:
        Path: 本地文件系统的绝对路径

    Raises:
        ValueError: 路径遍历攻击检测，object_name 试图访问 Vault 根目录之外的文件
    """
    root = _get_storage_root()
    obj = object_name.replace("\\", "/")
    # 兼容旧数据：无 bucket 前缀且非新 vault 结构时补上 bucket 前缀
    vault_segments = ("source", "output", "history", "cache")
    is_vault_path = any(seg in obj.split("/") for seg in vault_segments)
    if not is_vault_path and not obj.startswith(bucket + "/"):
        obj = f"{bucket}/{obj}"
    path = root / obj
    # 安全检查：防止路径遍历攻击
    # resolve() 会解析所有 ".." 和符号链接，得到真实路径
    path = path.resolve()
    root = root.resolve()
    # 精确的路径包含判断（startswith 存在前缀绕过：/data/storage_evil 也能通过 /data/storage 检查）
    if path != root and root not in path.parents:
        raise ValueError(f"非法路径: {object_name}")
    return path


def ensure_buckets_exist():
    """
    确保存储目录/存储桶存在

    本地模式：创建 Vault 根目录（各项目子目录在上传时懒创建）。
    MinIO 模式：确保原始文件桶和 Markdown 桶存在。
    在文件上传前调用，确保存储结构就绪。
    """
    root = _get_storage_root()
    root.mkdir(parents=True, exist_ok=True)
    if settings.storage_backend == "minio":
        client = _get_minio_client()
        for bucket_name in [settings.minio_bucket_original, settings.minio_bucket_markdown]:
            if not client.bucket_exists(bucket_name):
                client.make_bucket(bucket_name)


def ensure_project_dirs(prefix: str, subdirs=None):
    """
    创建项目的 Vault 目录树（source/output/history/cache 等）

    创建项目时预建目录树，让用户在磁盘上即可看到并放入文件，
    无需等待首个文件上传（懒创建）才生成目录。

    Args:
        prefix: 项目前缀 {user_id}/{project_slug}
        subdirs: 需要创建的子目录列表（默认 PROJECT_SUBDIRS）
    """
    from .vault_path import PROJECT_SUBDIRS as _DEFAULT_SUBDIRS
    for subdir in (subdirs if subdirs is not None else _DEFAULT_SUBDIRS):
        object_name = f"{prefix}/{subdir}/"
        if settings.storage_backend == "minio":
            # MinIO 无真实目录概念，写入一个占位对象使目录树在浏览器可见
            upload_bytes(
                settings.minio_bucket_original,
                object_name + ".gitkeep",
                b"",
                "application/octet-stream",
            )
        else:
            path = _resolve_path(settings.minio_bucket_original, object_name.rstrip("/"))
            path.mkdir(parents=True, exist_ok=True)


def remove_project_dir(prefix: str):
    """
    删除项目的整个 Vault 目录树

    删除项目时调用，物理清理磁盘/对象存储中的项目目录。

    Args:
        prefix: 项目前缀 {user_id}/{project_slug}
    """
    if settings.storage_backend == "minio":
        from minio import DeleteObject
        client = _get_minio_client()
        objects = [
            DeleteObject(obj.object_name)
            for obj in client.list_objects(
                settings.minio_bucket_original, prefix=f"{prefix}/", recursive=True
            )
        ]
        if objects:
            client.remove_objects(settings.minio_bucket_original, objects)
    else:
        root = _get_storage_root()
        path = root / prefix
        path = path.resolve()
        root_resolved = root.resolve()
        # 精确的路径包含判断，防止误删 Vault 根之外的目录
        if path != root_resolved and root_resolved in path.parents and path.exists():
            import shutil
            shutil.rmtree(path)


def list_source_files(prefix: str):
    """
    列出项目 source/ 目录下的文件

    用于「扫描导入」：用户手动把文件放入磁盘 source/ 目录后，
    程序据此识别新文件并创建笔记。

    Args:
        prefix: 项目前缀 {user_id}/{project_slug}

    Returns:
        list[tuple[str, int]]: source/ 下相对文件路径与文件大小（字节）的列表
    """
    if settings.storage_backend == "minio":
        client = _get_minio_client()
        source_prefix = f"{prefix}/source/"
        return [
            (obj.object_name[len(source_prefix):], obj.size)
            for obj in client.list_objects(
                settings.minio_bucket_original, prefix=source_prefix, recursive=True
            )
            if not obj.object_name.endswith("/") and not obj.object_name.endswith(".gitkeep")
        ]
    else:
        root = _get_storage_root()
        source_dir = root / prefix / "source"
        if not source_dir.exists():
            return []
        result = []
        for dirpath, _dirnames, filenames in os.walk(source_dir):
            for fname in filenames:
                full = os.path.join(dirpath, fname)
                rel = os.path.relpath(full, source_dir).replace("\\", "/")
                result.append((rel, os.path.getsize(full)))
        return result


def upload_file(bucket: str, object_name: str, file_path: str, content_type: str = "application/octet-stream"):
    """
    上传文件到存储

    根据配置自动选择本地文件系统或 MinIO 作为存储后端。

    Args:
        bucket: 存储桶名称
        object_name: 对象名称（存储路径）
        file_path: 本地文件路径
        content_type: 文件 MIME 类型，MinIO 模式使用
    """
    if settings.storage_backend == "minio":
        _upload_file_minio(bucket, object_name, file_path, content_type)
    else:
        _upload_file_local(bucket, object_name, file_path)


def upload_bytes(bucket: str, object_name: str, data: bytes, content_type: str = "application/octet-stream"):
    """
    上传字节数据到存储

    适用于在内存中生成的内容直接上传，无需先写入临时文件。

    Args:
        bucket: 存储桶名称
        object_name: 对象名称（存储路径）
        data: 字节数据
        content_type: 文件 MIME 类型，MinIO 模式使用
    """
    if settings.storage_backend == "minio":
        _upload_bytes_minio(bucket, object_name, data, content_type)
    else:
        _upload_bytes_local(bucket, object_name, data)


def download_file(bucket: str, object_name: str, file_path: str):
    """
    从存储下载文件到本地路径

    Args:
        bucket: 存储桶名称
        object_name: 对象名称
        file_path: 本地目标文件路径
    """
    if settings.storage_backend == "minio":
        _download_file_minio(bucket, object_name, file_path)
    else:
        _download_file_local(bucket, object_name, file_path)


def get_object_bytes(bucket: str, object_name: str) -> bytes:
    """
    从存储读取对象内容为字节数据

    Args:
        bucket: 存储桶名称
        object_name: 对象名称

    Returns:
        bytes: 对象内容的字节数据
    """
    if settings.storage_backend == "minio":
        return _get_object_bytes_minio(bucket, object_name)
    else:
        return _get_object_bytes_local(bucket, object_name)


def get_presigned_url(bucket: str, object_name: str, expires_hours: int = 1) -> str:
    """
    生成访问 URL

    MinIO 模式：生成带时效的预签名 URL，可直接在浏览器中访问
    本地模式：返回文件路径字符串（仅供内部使用，无法通过 HTTP 直接访问）

    Args:
        bucket: 存储桶名称
        object_name: 对象名称
        expires_hours: 预签名 URL 有效时长（小时），仅 MinIO 模式使用

    Returns:
        str: 访问 URL 或文件路径
    """
    if settings.storage_backend == "minio":
        return _get_presigned_url_minio(bucket, object_name, expires_hours)
    else:
        path = _resolve_path(bucket, object_name)
        return str(path)


def delete_file(bucket: str, object_name: str):
    """
    从存储删除文件

    Args:
        bucket: 存储桶名称
        object_name: 对象名称
    """
    if settings.storage_backend == "minio":
        _delete_file_minio(bucket, object_name)
    else:
        _delete_file_local(bucket, object_name)


def file_exists(bucket: str, object_name: str) -> bool:
    """
    检查对象是否存在（回收站恢复时的同名冲突检测）

    Args:
        bucket: 存储桶名称
        object_name: 对象名称

    Returns:
        bool: 对象存在返回 True，不存在或无法访问返回 False
    """
    if settings.storage_backend == "minio":
        from minio.error import S3Error
        try:
            client = _get_minio_client()
            client.stat_object(bucket, object_name)
            return True
        except S3Error:
            return False
    else:
        return _resolve_path(bucket, object_name).exists()


def move_file(bucket: str, object_name: str, new_object_name: str,
              content_type: str = "application/octet-stream"):
    """
    在存储内移动对象（回收站移入/恢复的物理文件隔离搬家）

    统一实现为 读取字节 → 写入新位置 → 删除旧位置。
    文档类文件体量小，复制删除方式对本地/MinIO 双后端最简单可靠。

    Args:
        bucket: 存储桶名称
        object_name: 原对象名
        new_object_name: 目标对象名
        content_type: MIME 类型（MinIO 模式使用）
    """
    data = get_object_bytes(bucket, object_name)
    upload_bytes(bucket, new_object_name, data, content_type=content_type)
    delete_file(bucket, object_name)


# ===== 本地文件系统实现 =====

def _upload_file_local(bucket: str, object_name: str, file_path: str):
    """本地模式：移动文件到存储目录（使用 os.replace 避免双份占用）"""
    path = _resolve_path(bucket, object_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    # 优先使用 os.replace（原子操作，无额外磁盘占用）
    # 如果跨文件系统则回退到 shutil.copy2 + 删除源文件
    try:
        os.replace(file_path, path)
    except OSError:
        shutil.copy2(file_path, path)
        os.unlink(file_path)


def _upload_bytes_local(bucket: str, object_name: str, data: bytes):
    """本地模式：将字节数据写入存储目录"""
    path = _resolve_path(bucket, object_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _download_file_local(bucket: str, object_name: str, file_path: str):
    """本地模式：从存储目录复制文件到指定路径"""
    path = _resolve_path(bucket, object_name)
    shutil.copy2(path, file_path)


def _get_object_bytes_local(bucket: str, object_name: str) -> bytes:
    """本地模式：读取存储目录中文件的内容"""
    path = _resolve_path(bucket, object_name)
    return path.read_bytes()


def _get_presigned_url_local(bucket: str, object_name: str) -> str:
    """本地模式：返回文件路径（非 HTTP URL）"""
    return str(_resolve_path(bucket, object_name))


def _delete_file_local(bucket: str, object_name: str):
    """本地模式：删除存储目录中的文件

    删除后向上清理空目录（直到 Vault 根），避免 purge/restore 留下
    空目录壳（如 trash/{note_id}/source 等只剩目录树）。
    """
    path = _resolve_path(bucket, object_name)
    if path.exists():
        path.unlink()
    # 向上清理空目录：仅删除真正的空目录，遇到非空目录或到达根即停止
    root = _get_storage_root()
    parent = path.parent
    try:
        while parent != root and parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent
    except OSError:
        # 并发写入等导致目录非空/无法删除时静默跳过，不影响主流程
        pass


# ===== MinIO 实现（保留，后续可切换） =====

def _get_minio_client():
    """
    创建 MinIO 客户端实例

    延迟导入 minio 包，本地模式无需安装此依赖。
    每次调用创建新客户端，MinIO SDK 内部会管理连接池。

    Returns:
        Minio: MinIO 客户端实例
    """
    from minio import Minio
    return Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
    )


def _upload_file_minio(bucket: str, object_name: str, file_path: str, content_type: str):
    """MinIO 模式：通过文件路径上传对象"""
    client = _get_minio_client()
    client.fput_object(bucket, object_name, file_path, content_type=content_type)


def _upload_bytes_minio(bucket: str, object_name: str, data: bytes, content_type: str):
    """MinIO 模式：通过字节数据上传对象"""
    from io import BytesIO
    client = _get_minio_client()
    client.put_object(bucket, object_name, BytesIO(data), length=len(data), content_type=content_type)


def _download_file_minio(bucket: str, object_name: str, file_path: str):
    """MinIO 模式：下载对象到本地文件"""
    client = _get_minio_client()
    client.fget_object(bucket, object_name, file_path)


def _get_object_bytes_minio(bucket: str, object_name: str) -> bytes:
    """MinIO 模式：读取对象内容为字节数据"""
    client = _get_minio_client()
    response = client.get_object(bucket, object_name)
    try:
        return response.read()
    finally:
        # 确保关闭响应流，释放连接
        response.close()
        response.release_conn()


def _get_presigned_url_minio(bucket: str, object_name: str, expires_hours: int) -> str:
    """
    MinIO 模式：生成预签名访问 URL

    生成的 URL 在指定时间内可直接访问，无需认证。
    """
    from datetime import timedelta
    client = _get_minio_client()
    return client.presigned_get_object(bucket, object_name, expires=timedelta(hours=expires_hours))


def _delete_file_minio(bucket: str, object_name: str):
    """MinIO 模式：删除对象"""
    client = _get_minio_client()
    client.remove_object(bucket, object_name)
