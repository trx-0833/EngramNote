"""
笔记资产（图片）的落盘、搬家与读取
==================================

## 这个模块解决什么

MinerU 解析带插图的 PDF 时，产物里既有 `full.md`，也有 `images/{sha256}.jpg`，
markdown 里对图写的是**相对引用**：`![](images/xxx.jpg)`（位置就在原段落处）。

但在 2026-11-09 之前，图片根本没留下来：

- 转换任务把整个过程跑在 `tempfile.TemporaryDirectory` 里（`tasks/convert_tasks.py`），
  只把 markdown 文本送进 vault，临时目录一退，`images/` 跟着没了；
- 本地解析分支（`services/mineru/converter.py` 的 zip 解压）只挑
  `markdown.md` + `middle_json.json`，连落盘都没有；
- 即使留下来也没有任何**分发入口**（`/api/assets`、`/static` 全 404），
  而前端 DOMPurify 只放行 `http(s)` 的 `img[src]`。

本模块补第一、二段（留下 + 搬家 + 删除），分发由 `api/notes/assets.py` 负责，
相对引用重写由前端 `renderMarkdown(text, assetsBaseUrl)` 负责。

## 目录约定（与 `vault_path.py` 一致）

    {user_id}/inbox/output/assets/{base}/images/{name}

`base` 是笔记文件主干，与 `source/{base}{ext}`、`output/markdown/{base}.md`
共用同一条命名关联法则 —— 脱离数据库也能从文件名反推归属。

## 为什么只认图片后缀

资产端点会把文件原样吐给浏览器。`.svg` 可以内嵌 `<script>`，与「同源加载」
组合就是存储型 XSS；而这里的产物全部来自 MinerU（只会给 png/jpg/webp/gif），
白名单比消毒便宜也更安全。白名单之外的产物**直接跳过不落盘**。
"""

from __future__ import annotations

import logging
import mimetypes
import re
from pathlib import Path
from typing import Iterable, List, Optional

from . import vault_path

logger = logging.getLogger(__name__)

#: 允许落盘与分发的图片后缀（**不含 .svg**，理由见文件头）
IMAGE_ASSET_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})

#: 后缀 → Content-Type（不依赖 `mimetypes` 的系统数据库，便于测试稳定）
CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def content_type_for(name: str) -> Optional[str]:
    """按后缀给出 Content-Type；白名单外返回 None"""
    suffix = Path(name).suffix.lower()
    if suffix not in IMAGE_ASSET_SUFFIXES:
        return None
    return CONTENT_TYPES[suffix] or mimetypes.guess_type(name)[0] or "application/octet-stream"


def is_safe_asset_path(rel_path: str) -> bool:
    """判断一个相对引用路径是否可以安全地拼进对象名

    拒绝：空串、绝对路径、盘符、任何 `..` 段、白名单外的后缀。

    反斜杠**统一成 `/` 再判定**（不当作穿越）：对象名一律用 POSIX 分隔符，
    把 `images\\x.png` 归一成 `images/x.png` 后仍在资产目录内，比直接拒绝更宽容，
    也不会让 Windows 语义带来歧义 —— 归一化之后不存在"它到底在不在目录里"的问题。
    """
    if not rel_path or not rel_path.strip():
        return False
    rel = rel_path.replace("\\", "/").strip()
    if rel.startswith("/") or ":" in rel.split("/")[0]:
        return False
    parts = [part for part in rel.split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        return False
    return content_type_for(parts[-1]) is not None


def normalize_rel_path(rel_path: str) -> str:
    """把 markdown 里的引用规范化成对象名尾段（去前导 `/`、去 `./`、统一分隔符）"""
    rel = rel_path.replace("\\", "/").strip()
    parts = [part for part in rel.split("/") if part not in ("", ".")]
    return "/".join(parts)


def collect_converted_images(out_dir: Path) -> List[Path]:
    """从一次转换的产物目录里收集所有插图（`images/` 下的白名单图片）

    为什么按**目录名**找而不是按 markdown 引用找：引用可能是 MinerU 改写过的
    相对路径，而产物结构是它自己的约定；两边取交集才是稳妥做法 ——
    调用方拿到结果后按 basename 与引用对齐（见 `persist_converted_images`）。
    """
    if not out_dir.exists():
        return []
    found: List[Path] = []
    for path in out_dir.rglob("*"):
        if not path.is_file():
            continue
        if content_type_for(path.name) is None:
            continue
        if any(part.lower() == "images" for part in path.parts):
            found.append(path)
    return sorted(found)


def persist_converted_images(out_dir: Path, prefix: str, base: str) -> int:
    """把产物目录里的插图写进 vault 的 `output/assets/{base}/images/`

    Returns:
        成功写入的张数（失败逐张 warning，不抛 —— 转换本身已经成功，
        不能因为图片写入失败把整篇笔记标成 failed）

    Raises:
        OSError: 只在**读取源文件**时抛（磁盘问题属于真错误，交给调用方记 warning）
    """
    images = collect_converted_images(out_dir)
    if not images:
        return 0

    from .storage_service import upload_bytes  # 延迟导入：避免测试期循环依赖

    settings_bucket = _markdown_bucket()
    saved = 0
    for image in images:
        rel = f"images/{image.name}"
        object_name = vault_path.assets_object(prefix, base, rel)
        try:
            upload_bytes(
                settings_bucket,
                object_name,
                image.read_bytes(),
                content_type=content_type_for(image.name) or "application/octet-stream",
            )
            saved += 1
        except Exception as exc:  # noqa: BLE001 - 单张失败不该毁掉整次转换
            logger.warning("插图写入失败（忽略继续）: %s -> %s, 错误: %s", image.name, object_name, exc)
    if saved:
        logger.info("插图已写入 vault: note_base=%s, 张数=%d", base, saved)
    return saved


def list_asset_objects(prefix: str, base: str) -> List[str]:
    """列出一篇笔记的全部资产对象名（导出 / 搬家 / 删除用）"""
    from .storage_service import list_object_names

    asset_prefix = vault_path.assets_prefix(prefix, base)
    return list_object_names(_markdown_bucket(), asset_prefix)


def move_assets(prefix_from: str, prefix_to: str, base: str) -> int:
    """把一篇笔记的资产整体搬家（移入回收站 / 恢复）

    Returns:
        实际搬动的个数；单个失败只 warning（与 md 的搬家口径一致：
        搬不动的保留原路径，恢复时再试）
    """
    from .storage_service import file_exists, move_file

    source_prefix = vault_path.assets_prefix(prefix_from, base)
    target_prefix = vault_path.assets_prefix(prefix_to, base)
    moved = 0
    for object_name in list_asset_objects(prefix_from, base):
        tail = object_name[len(source_prefix) :].lstrip("/")
        if not tail:
            continue
        target = f"{target_prefix}/{tail}"
        try:
            if file_exists(_markdown_bucket(), object_name):
                move_file(_markdown_bucket(), object_name, target)
                moved += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("资产搬家失败（忽略继续）: %s -> %s, 错误: %s", object_name, target, exc)
    return moved


def delete_assets(prefix: str, base: str) -> int:
    """删掉一篇笔记的全部资产（彻底删除 / 覆盖重传）

    Returns:
        实际删除的个数；失败只 warning（回收站/彻底删除都不该因此失败）
    """
    from .storage_service import delete_file

    deleted = 0
    for object_name in list_asset_objects(prefix, base):
        try:
            delete_file(_markdown_bucket(), object_name)
            deleted += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("资产删除失败（忽略继续）: %s, 错误: %s", object_name, exc)
    return deleted


def read_asset(prefix: str, base: str, rel_path: str) -> Optional[bytes]:
    """读取一个资产；不存在或路径不安全时返回 None（端点据此回 404）"""
    if not is_safe_asset_path(rel_path):
        return None
    from .storage_service import get_object_bytes

    object_name = vault_path.assets_object(prefix, base, normalize_rel_path(rel_path))
    try:
        return get_object_bytes(_markdown_bucket(), object_name)
    except Exception:  # noqa: BLE001 - 不存在与读取失败对用户是同一件事：没有这张图
        return None


def _markdown_bucket() -> str:
    """资产统一放在 markdown 桶（与 md、meta 同桶，便于整体搬迁）"""
    from ..config import get_settings

    return get_settings().minio_bucket_markdown


def iter_asset_names_for_export(prefix: str, base: str) -> Iterable[str]:
    """导出用：列出相对路径（`images/xxx.jpg`），供 ZIP 里建 `assets/{base}/…`"""
    asset_prefix = vault_path.assets_prefix(prefix, base)
    for object_name in list_asset_objects(prefix, base):
        tail = object_name[len(asset_prefix) :].lstrip("/")
        if tail:
            yield tail


#: 判断图片地址是否自带基准（绝对 URL / 协议相对 / 根路径 / 锚点 / data URI）
_HAS_BASE_RE = re.compile(r"^(?:[a-zA-Z][a-zA-Z0-9+.-]*:|//|/|#)")
#: Markdown 图片语法：`![alt](src)` / `![alt](src "title")`
_MD_IMAGE_RE = re.compile(r"(!\[[^\]]*\]\()\s*([^)\s]+)((?:\s+\"[^\"]*\")?\s*\))")
#: 原始 HTML 的 `<img src="…">`
_HTML_IMAGE_RE = re.compile(r"(<img\b[^>]*?\bsrc\s*=\s*)([\"'])([^\"']*)\2", re.IGNORECASE)


def rewrite_asset_refs(markdown_text: str, new_prefix: str) -> str:
    """把正文里的**相对图片引用**重写到另一个前缀下（导出 ZIP 用）

    ZIP 里每篇笔记是一个平铺文件，而图落在 `assets/{base}/images/…`；
    不重写的话，正文里的 `images/xxx.jpg` 指向包外，解压后打不开。

    与前端 `utils/markdown.ts` 的 `rewriteRelativeImageSources` 同一套判据：
    只改**相对路径**，绝对 URL / 协议相对 / 根路径 / 锚点 / data URI 一律不动。

    Args:
        markdown_text: 正文
        new_prefix: 新前缀（如 `assets/{base}/`，**以 `/` 结尾**）

    Returns:
        重写后的正文
    """
    if not markdown_text or not new_prefix:
        return markdown_text
    base = new_prefix if new_prefix.endswith("/") else f"{new_prefix}/"

    def _join(src: str) -> str:
        return f"{base}{src[2:] if src.startswith('./') else src}"

    text = _MD_IMAGE_RE.sub(
        lambda m: m.group(0) if _HAS_BASE_RE.match(m.group(2)) else f"{m.group(1)}{_join(m.group(2))}{m.group(3)}",
        markdown_text,
    )
    return _HTML_IMAGE_RE.sub(
        lambda m: (
            m.group(0) if _HAS_BASE_RE.match(m.group(3)) else f"{m.group(1)}{m.group(2)}{_join(m.group(3))}{m.group(2)}"
        ),
        text,
    )
