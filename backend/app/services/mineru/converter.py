"""
MinerU 文档转换核心模块

提供单文件转换 (convert) 和批量文件夹转换 (convert_folder) 功能。
支持本地 pipeline 和远程 VLM API 两种解析后端，
大文件自动分块处理，转换后自动清洗 Markdown 内容。
"""

from __future__ import annotations

import gc
import json
import os
import shutil
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeoutError
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config_loader import DEFAULT_CONFIG, load_config, _load_env_config
from .intake import (
    intake_docx,
    intake_image,
    intake_pdf,
    intake_pptx,
    intake_xlsx,
)
from .model_loader import configure_modelscope_models
from .models import (
    DOCX_EXTS,
    IMAGE_EXTS,
    PDF_EXTS,
    PPTX_EXTS,
    SUPPORTED_EXTS,
    XLSX_EXTS,
    ConversionResult,
    SourceType,
)
from .postprocess import clean_markdown
from .title_extractor import extract_title

import logging

logger = logging.getLogger(__name__)

# 文件扩展名到源类型的映射
_EXT_TO_SOURCE_TYPE = {
    ".pdf": SourceType.PDF,
    ".png": SourceType.IMAGE,
    ".jpg": SourceType.IMAGE,
    ".jpeg": SourceType.IMAGE,
    ".docx": SourceType.DOCX,
    ".pptx": SourceType.PPTX,
    ".xlsx": SourceType.XLSX,
}


def convert(
    file_path: str | Path,
    backend: str = "pipeline",
    timeout: Optional[int] = None,
    lang: Optional[str] = None,
    formula_enable: bool = True,
    table_enable: bool = True,
    make_mode: str = "mm_md",
    output_dir: Optional[str | Path] = None,
    start_page_id: int = 0,
    end_page_id: Optional[int] = None,
    config_path: Optional[str | Path] = None,
    api_token: Optional[str] = None,
    server_url: Optional[str] = None,
) -> ConversionResult:
    """
    单文件转换

    Args:
        file_path: 文件路径
        backend: 后端引擎，默认 pipeline（本地 MinerU）
        timeout: 超时时间（秒），None 使用配置默认值
        lang: 识别语言，None 使用配置默认语言
        formula_enable: 是否启用公式识别
        table_enable: 是否启用表格识别
        make_mode: Markdown 生成模式
        output_dir: 输出目录，None 自动创建临时目录
        start_page_id: 起始页码（从 0 开始）
        end_page_id: 结束页码（不包含），None 表示到末尾
        config_path: 配置文件路径，None 使用默认配置
        api_token: MinerU API 令牌，优先级高于配置文件和环境变量
        server_url: MinerU 服务器 URL，优先级高于配置文件和环境变量

    Returns:
        ConversionResult 转换结果对象
    """
    config = load_config(config_path)

    # 调用方传入的参数优先级最高，覆盖配置文件和环境变量
    if api_token:
        config["api_token"] = api_token
    if server_url:
        config["server_url"] = server_url
    file_path = Path(file_path)

    if backend == "pipeline":
        configure_modelscope_models(config)

    if timeout is None:
        timeout = config.get("timeout", 300)

    if lang is None:
        lang = config.get("lang", "ch")

    if not file_path.exists():
        return ConversionResult(
            title="",
            source_path=str(file_path),
            error=f"文件不存在: {file_path}",
            success=False,
        )

    ext = file_path.suffix.lower()
    if ext not in SUPPORTED_EXTS:
        return ConversionResult(
            title="",
            source_path=str(file_path),
            error=f"不支持的文件格式: {ext}",
            success=False,
        )

    source_type = _EXT_TO_SOURCE_TYPE.get(ext)
    if source_type is None:
        return ConversionResult(
            title="",
            source_path=str(file_path),
            error=f"内部错误: 扩展名 {ext} 已通过验证但未映射到源类型",
            success=False,
        )

    intake_temp_dirs: List[Path] = []
    try:
        intake_paths, intake_temp_dirs = _route_to_intake(file_path, ext, config)
    except Exception as e:
        return ConversionResult(
            title="",
            source_type=source_type,
            source_path=str(file_path),
            error=f"文件摄入失败: {str(e)}",
            success=False,
        )

    is_chunked = len(intake_paths) > 1
    chunk_results: List[str] = []
    chunk_metadata: List[Dict[str, Any]] = []
    total_pages = 0

    _timeout_occurred = False
    _early_result: Optional[ConversionResult] = None

    try:
        for idx, intake_path in enumerate(intake_paths):
            chunk_label = f" (分块 {idx+1}/{len(intake_paths)})" if is_chunked else ""
            logger.info(
                f"转换开始: {file_path.name}{chunk_label} | 后端={backend} | 语言={lang}"
            )

            chunk_timeout = _compute_timeout(timeout, intake_path, ext)

            _result_holder = [None]
            _error_holder = [None]

            def _target():
                try:
                    _result_holder[0] = _process_with_mineru(
                        intake_path,
                        output_dir,
                        backend,
                        lang,
                        formula_enable,
                        table_enable,
                        make_mode,
                        start_page_id if not is_chunked else 0,
                        end_page_id if not is_chunked else None,
                        config,
                    )
                except Exception as exc:
                    _error_holder[0] = exc

            worker = threading.Thread(target=_target, daemon=True)
            worker.start()
            worker.join(timeout=chunk_timeout)

            try:
                if worker.is_alive():
                    logger.error(
                        f"转换超时: {file_path.name}{chunk_label} | 超时时间={chunk_timeout}秒"
                    )
                    _timeout_occurred = True
                    _early_result = ConversionResult(
                        title="",
                        source_type=source_type,
                        source_path=str(file_path),
                        error=f"转换超时: 超过 {chunk_timeout} 秒仍未完成",
                        success=False,
                    )
                    break
                if _error_holder[0] is not None:
                    logger.error(
                        f"转换失败: {file_path.name}{chunk_label} | 错误={str(_error_holder[0])}"
                    )
                    _early_result = ConversionResult(
                        title="",
                        source_type=source_type,
                        source_path=str(file_path),
                        error=f"转换失败: {str(_error_holder[0])}",
                        success=False,
                    )
                    break
                chunk_result = _result_holder[0]
            finally:
                gc.collect()

            if not chunk_result.success:
                _early_result = chunk_result
                break

            chunk_results.append(chunk_result.markdown_content)
            chunk_metadata.append(chunk_result.metadata)
            total_pages += chunk_result.metadata.get("page_count", 0)

            logger.info(
                f"转换完成: {file_path.name}{chunk_label} | 状态=成功 | 页数={chunk_result.metadata.get('page_count', 0)}"
            )

        if _early_result is None:
            if is_chunked:
                merged_content = _merge_chunks(chunk_results, config)
                full_metadata = _merge_metadata(chunk_metadata, total_pages, intake_paths)
                markdown_content = merged_content
            else:
                markdown_content = chunk_results[0] if chunk_results else ""
                full_metadata = chunk_metadata[0] if chunk_metadata else {}

            title = extract_title(file_path, markdown_content, source_type.value)

            full_metadata["source_type"] = source_type.value
            full_metadata["backend"] = backend
            full_metadata["chunked"] = is_chunked

            _early_result = ConversionResult(
                title=title,
                markdown_content=markdown_content,
                source_type=source_type,
                source_path=str(file_path),
                metadata=full_metadata,
            )
    finally:
        _cleanup_temp_dirs(intake_temp_dirs)

    if _timeout_occurred:
        logger.error(f"转换超时，强制返回: {file_path}")
    return _early_result


def convert_folder(
    input_dir: str | Path,
    output_dir: str | Path,
    max_workers: int = 1,
    **kwargs,
) -> List[ConversionResult]:
    """
    批量转换文件夹

    Args:
        input_dir: 输入目录路径
        output_dir: 输出目录路径
        max_workers: 最大并发工作线程数，默认 1
        **kwargs: 传递给 convert 函数的额外参数

    Returns:
        所有文件的转换结果列表
    """
    input_dir = Path(input_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not input_dir.exists():
        return [
            ConversionResult(
                title="",
                source_path=str(input_dir),
                error=f"输入目录不存在: {input_dir}",
                success=False,
            )
        ]

    config = load_config(kwargs.get("config_path"))
    device = kwargs.pop("device", None) or config.get("device", "cpu")

    if max_workers > 1 and device != "cpu":
        logger.warning("GPU模式下 max_workers 强制降为 1")
        max_workers = 1

    files = sorted(
        [f for f in input_dir.iterdir() if f.is_file() and f.suffix.lower() in SUPPORTED_EXTS]
    )

    if not files:
        logger.warning(f"输入目录中无支持格式文件: {input_dir}")
        return []

    results: List[ConversionResult] = []
    total = len(files)

    if max_workers <= 1:
        for i, f in enumerate(files):
            logger.info(f"批量转换进度: {i+1}/{total} - {f.name}")
            try:
                call_kwargs = dict(kwargs, output_dir=output_dir)
                result = convert(f, **call_kwargs)
            except Exception as e:
                result = ConversionResult(
                    title="",
                    source_path=str(f),
                    error=f"转换异常: {str(e)}",
                    success=False,
                )
            results.append(result)
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_file = {}
            for f in files:
                call_kwargs = dict(kwargs, output_dir=output_dir)
                future = executor.submit(convert, f, **call_kwargs)
                future_to_file[future] = f

            for i, future in enumerate(as_completed(list(future_to_file.keys()))):
                f = future_to_file[future]
                logger.info(f"批量转换进度: {i+1}/{total} - {f.name}")
                try:
                    result = future.result()
                except Exception as e:
                    result = ConversionResult(
                        title="",
                        source_path=str(f),
                        error=f"转换异常: {str(e)}",
                        success=False,
                    )
                results.append(result)

    success_count = sum(1 for r in results if r.success)
    fail_count = total - success_count
    logger.info(f"批量转换完成: 总计={total}, 成功={success_count}, 失败={fail_count}")

    return results


# ── 内部辅助函数 ──────────────────────────────────────────────


def _route_to_intake(
    file_path: Path, ext: str, config: Dict[str, Any]
) -> Tuple[List[Path], List[Path]]:
    """根据文件扩展名路由到对应的摄入函数"""
    if ext in PDF_EXTS:
        chunk_size = config.get("chunk_size", DEFAULT_CONFIG["chunk_size"])
        chunk_overlap = config.get("chunk_overlap", DEFAULT_CONFIG["chunk_overlap"])
        paths, temp_dir = intake_pdf(file_path, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        return paths, [temp_dir] if temp_dir else []
    elif ext in IMAGE_EXTS:
        path, temp_dir = intake_image(file_path)
        return [path], [temp_dir] if temp_dir else []
    elif ext in DOCX_EXTS:
        return [intake_docx(file_path)], []
    elif ext in PPTX_EXTS:
        return [intake_pptx(file_path)], []
    elif ext in XLSX_EXTS:
        return [intake_xlsx(file_path)], []
    else:
        raise ValueError(f"不支持的文件格式: {ext}")


def _process_with_mineru(
    intake_path: Path,
    output_dir: Optional[str | Path],
    backend: str,
    lang: str,
    formula_enable: bool,
    table_enable: bool,
    make_mode: str,
    start_page_id: int,
    end_page_id: Optional[int],
    config: Dict[str, Any],
) -> ConversionResult:
    """本地 MinerU 处理函数"""
    if backend in ("vlm-http-client", "hybrid-http-client"):
        return _process_with_remote_api(
            intake_path, output_dir, backend, lang,
            formula_enable, table_enable, make_mode,
            start_page_id, end_page_id, config,
        )

    from mineru.cli.common import (
        do_parse,
        image_suffixes,
        read_fn,
    )
    from mineru.utils.enum_class import MakeMode

    if output_dir is None:
        temp_dir = Path(tempfile.mkdtemp(prefix="mineru_output_"))
        output_dir = temp_dir
    else:
        output_dir = Path(output_dir)
        temp_dir = None

    output_dir.mkdir(parents=True, exist_ok=True)

    device = config.get("device", "cpu")
    os.environ.setdefault("MINERU_DEVICE_MODE", device)
    os.environ.setdefault("MINERU_MODEL_SOURCE", "local")

    file_stem = intake_path.stem
    file_suffix = intake_path.suffix.lstrip(".").lower()

    file_bytes = read_fn(intake_path)
    if file_suffix in image_suffixes:
        file_suffix = "pdf"

    parse_method = "auto"
    if backend == "pipeline":
        parse_method = "auto"
    elif backend.startswith("vlm-") or backend.startswith("hybrid-"):
        parse_method = "vlm"

    lang_list = [lang] if lang else ["ch"]
    make_mode_enum = MakeMode.MM_MD if make_mode in ("mm_md", None) else MakeMode.NLP_MD

    try:
        do_parse(
            output_dir=str(output_dir),
            pdf_file_names=[file_stem],
            pdf_bytes_list=[file_bytes],
            p_lang_list=lang_list,
            backend=backend,
            parse_method=parse_method,
            formula_enable=formula_enable,
            table_enable=table_enable,
            f_draw_layout_bbox=False,
            f_draw_span_bbox=False,
            f_dump_md=True,
            f_dump_middle_json=True,
            f_dump_model_output=True,
            f_dump_orig_pdf=True,
            f_dump_content_list=True,
            f_make_md_mode=make_mode_enum,
            start_page_id=start_page_id,
            end_page_id=end_page_id,
        )
    except Exception as e:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
        return ConversionResult(
            title="",
            source_path=str(intake_path),
            error=f"MinerU解析失败: {str(e)}",
            success=False,
        )

    # 生成 layout.json（从 middle.json 复制）
    middle_candidates = sorted(output_dir.rglob(f"{file_stem}_middle.json"))
    if middle_candidates:
        middle_path = middle_candidates[0]
        layout_path = middle_path.with_name("layout.json")
        try:
            shutil.copy2(middle_path, layout_path)
            logger.info(f"已生成 layout.json: {layout_path}")
        except Exception as e:
            logger.warning(f"生成 layout.json 失败: {e}")

    md_content = ""
    page_count = 0
    layout_json_data = None
    md_path: Optional[Path] = None

    md_candidates = sorted(output_dir.rglob(f"{file_stem}.md"))
    if md_candidates:
        md_path = md_candidates[0]

    try:
        if md_path and md_path.exists():
            md_content = md_path.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning(f"读取Markdown文件失败: {md_path} - {e}")

    json_candidates = sorted(output_dir.rglob("layout.json"))
    if json_candidates:
        json_path = json_candidates[0]
        try:
            layout_json_data = json.loads(json_path.read_text(encoding="utf-8"))
            pdf_info = layout_json_data.get("pdf_info", [])
            page_count = len(pdf_info)
        except Exception as e:
            logger.warning(f"读取 layout.json 失败: {json_path} - {e}")

    # 读取 content_list.json，用于插入页码标签
    content_list_candidates = sorted(output_dir.rglob(f"{file_stem}_content_list.json"))
    if not content_list_candidates:
        content_list_candidates = sorted(output_dir.rglob("content_list.json"))
    if not content_list_candidates:
        content_list_candidates = sorted(output_dir.rglob("*_content_list.json"))
    if content_list_candidates:
        try:
            md_content = _insert_page_markers_from_content_list(
                md_content, content_list_candidates[0], start_page_id
            )
        except Exception as e:
            logger.warning(f"插入页码标签失败: {e}")

    md_content = clean_markdown(md_content, config)

    metadata = {
        "page_count": page_count,
        "backend": backend,
        "parse_method": parse_method,
    }

    if temp_dir:
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass

    return ConversionResult(
        title="",
        markdown_content=md_content,
        source_path=str(intake_path),
        metadata=metadata,
    )


def _process_with_remote_api(
    intake_path: Path,
    output_dir: Optional[str | Path],
    backend: str,
    lang: str,
    formula_enable: bool,
    table_enable: bool,
    make_mode: str,
    start_page_id: int,
    end_page_id: Optional[int],
    config: Dict[str, Any],
) -> ConversionResult:
    """远程 API 处理函数（通过 HTTP 调用 MinerU 服务）"""
    api_token = config.get("api_token") or _load_env_config().get("MINERU_API_TOKEN")
    if not api_token:
        return ConversionResult(
            title="",
            source_path=str(intake_path),
            error="API Token 未配置。VLM/HTTP Client 后端需要设置 MINERU_API_TOKEN。",
            success=False,
        )

    base_url = config.get("server_url", "")
    if not base_url:
        return ConversionResult(
            title="",
            source_path=str(intake_path),
            error="server_url 未配置。VLM/HTTP Client 后端需要设置远程 MinerU API 地址，如 https://mineru.net。",
            success=False,
        )
    base_url = base_url.rstrip("/")
    base_url = _normalize_api_base_url(base_url)

    if output_dir is None:
        temp_dir = Path(tempfile.mkdtemp(prefix="mineru_remote_output_"))
        output_dir = temp_dir
    else:
        output_dir = Path(output_dir)
        temp_dir = None

    output_dir.mkdir(parents=True, exist_ok=True)
    file_stem = intake_path.stem
    extract_dir: Optional[Path] = None

    import requests as req

    model_version = config.get("model_version") or _resolve_model_version(backend)

    try:
        submit_url = f"{base_url}/api/v4/file-urls/batch"
        headers = {"Content-Type": "application/json",
                   "Authorization": f"Bearer {api_token}"}
        payload = {
            "files": [{"name": intake_path.name, "data_id": file_stem}],
            "model_version": model_version,
        }
        if lang:
            payload["language"] = lang
        if not formula_enable:
            payload["enable_formula"] = False
        if not table_enable:
            payload["enable_table"] = False
        if not is_ocr_needed(backend):
            payload["is_ocr"] = False
        if end_page_id is not None:
            payload["page_ranges"] = f"{start_page_id+1}-{end_page_id}"

        logger.info(f"申请上传地址: {submit_url} -> {intake_path.name}")
        resp = req.post(submit_url, headers=headers, json=payload)
        if resp.status_code != 200:
            return ConversionResult(
                title="",
                source_path=str(intake_path),
                error=f"申请上传地址失败: HTTP {resp.status_code} {resp.text[:200]}",
                success=False,
            )
        result = resp.json()
        if result.get("code") != 0:
            return ConversionResult(
                title="",
                source_path=str(intake_path),
                error=f"申请上传地址失败: {result.get('msg', '未知错误')}",
                success=False,
            )

        data = result.get("data", {})
        batch_id = data.get("batch_id")
        file_urls = data.get("file_urls", [])
        if not batch_id or not file_urls:
            return ConversionResult(
                title="",
                source_path=str(intake_path),
                error=f"API返回数据不完整: batch_id={batch_id}, file_urls={file_urls}",
                success=False,
            )
        logger.info(f"获取到上传地址: batch_id={batch_id}, urls_count={len(file_urls)}")

        for upload_url in file_urls:
            logger.info(f"上传文件: {intake_path.name}")
            with open(intake_path, "rb") as f:
                upload_resp = req.put(upload_url, data=f)
            if upload_resp.status_code not in (200, 201):
                return ConversionResult(
                    title="",
                    source_path=str(intake_path),
                    error=f"文件上传失败: HTTP {upload_resp.status_code}",
                    success=False,
                )
            logger.info(f"上传成功: {intake_path.name}")

        logger.info(f"文件上传完成，系统自动提交任务: batch_id={batch_id}")

        import time
        task_state = None
        remote_timeout = _compute_timeout(config.get('timeout'), intake_path, intake_path.suffix.lower())
        deadline = time.time() + remote_timeout
        logger.info(f"远程API超时设定: timeout={remote_timeout}秒, file={intake_path.name}")
        poll_interval = config.get("poll_interval", 5)

        query_url = f"{base_url}/api/v4/extract-results/batch/"

        state_desc = {
            "waiting-file": "等待文件上传排队提交解析任务中",
            "pending": "排队中",
            "running": "正在解析",
            "converting": "格式转换中",
            "done": "完成",
            "failed": "解析失败"
        }

        while time.time() < deadline and task_state != 'done':
            try:
                resp = req.get(f"{query_url}{batch_id}", headers=headers)
                if resp.status_code == 200:
                    data = resp.json().get("data", {})
                    tasks = data if isinstance(data, list) else [data]
                    for task in tasks:
                        extract_result = task.get("extract_result", [])
                        if not extract_result:
                            continue
                        task_state = extract_result[0].get("state")
                        if task_state:
                            desc = state_desc.get(task_state, task_state)
                            logger.info(f"任务状态: {desc} (file_name={intake_path.name})")
                            if task_state == 'failed':
                                error_msg = extract_result[0].get('err_msg', '')
                                return ConversionResult(
                                    title="",
                                    source_path=str(intake_path),
                                    error=f"远程提取失败: {error_msg}",
                                    success=False,
                                )
                            if task_state == 'done':
                                zip_url = extract_result[0].get('full_zip_url', '')
                                if not zip_url:
                                    return ConversionResult(
                                        title="",
                                        source_path=str(intake_path),
                                        error=f"任务完成但未返回结果下载地址: file_name={intake_path.name}",
                                        success=False,
                                    )

                                zip_path = output_dir / f"{file_stem}_result.zip"
                                logger.info(f"下载结果: {zip_url}")
                                zip_resp = req.get(zip_url, stream=True)
                                if zip_resp.status_code != 200:
                                    return ConversionResult(
                                        title="",
                                        source_path=str(intake_path),
                                        error=f"下载结果失败: HTTP {zip_resp.status_code}",
                                        success=False,
                                    )
                                with open(zip_path, "wb") as f:
                                    for chunk in zip_resp.iter_content(chunk_size=8192):
                                        f.write(chunk)

                                extract_dir = output_dir / file_stem
                                extract_dir.mkdir(parents=True, exist_ok=True)
                                _safe_extract_zip(zip_path, extract_dir)
                                zip_path.unlink(missing_ok=True)
                                break
                            if task_state not in (None, 'done'):
                                time.sleep(poll_interval)
                                continue
                        if task_state is None:
                            logger.info(f"等待任务创建中... batch_id={batch_id}")
                            time.sleep(poll_interval)
            except Exception as e:
                logger.warning(f"查询任务状态异常: {e}")
                time.sleep(poll_interval)

        if task_state is None:
            return ConversionResult(
                title="",
                source_path=str(intake_path),
                error=f"远程API等待任务创建超时: batch_id={batch_id}, 超时时间={remote_timeout}秒",
                success=False,
            )

        if task_state != 'done':
            return ConversionResult(
                title="",
                source_path=str(intake_path),
                error=f"远程API轮询超时: state={task_state}, 超时时间={remote_timeout}秒",
                success=False,
            )

    except Exception as e:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
        return ConversionResult(
            title="",
            source_path=str(intake_path),
            error=f"远程API调用失败: {str(e)}",
            success=False,
        )

    md_content = ""
    page_count = 0
    layout_json_data = None
    md_path: Optional[Path] = None

    if extract_dir and extract_dir.exists():
        md_candidates = sorted(extract_dir.rglob("*.md"))
        if md_candidates:
            md_path = md_candidates[0]
            try:
                md_content = md_path.read_text(encoding="utf-8")
            except Exception as e:
                logger.warning(f"读取远程结果 Markdown 失败: {md_path} - {e}")

        json_candidates = sorted(extract_dir.rglob("layout.json"))
        if json_candidates:
            json_path = json_candidates[0]
            try:
                layout_json_data = json.loads(json_path.read_text(encoding="utf-8"))
                pdf_info = layout_json_data.get("pdf_info", [])
                page_count = len(pdf_info)
            except Exception as e:
                logger.warning(f"读取远程结果 layout_json 失败: {json_path} - {e}")

        content_list_candidates = sorted(extract_dir.rglob("content_list.json"))
        if not content_list_candidates:
            content_list_candidates = sorted(extract_dir.rglob("*_content_list.json"))
        if content_list_candidates:
            try:
                md_content = _insert_page_markers_from_content_list(
                    md_content, content_list_candidates[0], start_page_id
                )
            except Exception as e:
                logger.warning(f"插入页码标签失败（远程API）: {e}")

    md_content = clean_markdown(md_content, config)

    if md_path and md_path.exists():
        try:
            md_path.write_text(md_content, encoding="utf-8")
        except Exception as e:
            logger.warning(f"写入更新后的 full.md 失败: {e}")

    metadata = {
        "page_count": page_count,
        "backend": backend,
        "model_version": model_version,
        "remote_api": True,
    }

    if temp_dir:
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass

    return ConversionResult(
        title="",
        markdown_content=md_content,
        source_path=str(intake_path),
        metadata=metadata,
    )


def _resolve_model_version(backend: str) -> str:
    """根据后端确定模型版本字符串"""
    if backend.startswith("vlm-"):
        return "vlm"
    if backend.startswith("hybrid-"):
        return "vlm"
    return "pipeline"


def is_ocr_needed(backend: str) -> bool:
    """判断是否需要 OCR（只有 pipeline 后端需要）"""
    return backend == "pipeline"


def _safe_extract_zip(zip_path: Path, output_dir: Path) -> None:
    """安全解压 ZIP 文件（防止路径遍历攻击）"""
    import zipfile
    from pathlib import PurePosixPath

    output_dir.mkdir(parents=True, exist_ok=True)
    output_root = output_dir.resolve()

    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.infolist():
            member_path = PurePosixPath(member.filename)
            if member_path.is_absolute() or ".." in member_path.parts:
                logger.warning(f"跳过不安全的 ZIP 条目: {member.filename}")
                continue
            if member.external_attr >> 28 == 0xA:
                logger.warning(f"跳过符号链接 ZIP 条目: {member.filename}")
                continue
            target_path = (output_root / Path(*member_path.parts)).resolve()
            if target_path != output_root and output_root not in target_path.parents:
                logger.warning(f"跳过不安全的 ZIP 条目: {member.filename}")
                continue
            if member.is_dir():
                target_path.mkdir(parents=True, exist_ok=True)
                continue
            target_path.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member, "r") as source, open(target_path, "wb") as dest:
                while True:
                    chunk = source.read(8192)
                    if not chunk:
                        break
                    dest.write(chunk)


def _normalize_api_base_url(url: str) -> str:
    """规范化 API 基础 URL，去除路径中可能包含的 /api/vX/ 部分"""
    import re
    match = re.search(r"/api/v\d+/", url)
    if match:
        return url[: match.start()]
    return url


def _compute_timeout(timeout: int, intake_path: Path, ext: str, page_count: int = 0) -> int:
    """根据文件类型和页数计算超时时间"""
    base_timeout = timeout if timeout else 300
    if ext not in PDF_EXTS:
        return base_timeout

    if page_count <= 0:
        import pypdfium2 as pdfium
        try:
            doc = pdfium.PdfDocument(str(intake_path))
            page_count = len(doc)
            doc.close()
        except Exception:
            return base_timeout

    per_page = 30
    return max(base_timeout, 60 + page_count * per_page)


# 页面辅助类型，不作为页码定位标记
_AUXILIARY_TYPES = {"header", "footer", "page_number", "aside_text", "page_footnote"}


def _insert_page_markers_from_content_list(
    md_content: str,
    content_list_path: Path,
    start_page_id: int = 0,
) -> str:
    """
    根据 content_list.json 中的 page_idx 在 Markdown 中插入页码标签

    读取 MinerU 输出的 content_list.json，提取每页第一个正文文本内容，
    在 Markdown 中对应位置插入 <!-- page=N --> 标签（N 从 1 开始）。

    Args:
        md_content: 原始 Markdown 内容
        content_list_path: content_list.json 文件路径
        start_page_id: 起始页码偏移（用于分块场景，默认 0）

    Returns:
        插入页码标签后的 Markdown 内容
    """
    try:
        content_list = json.loads(content_list_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"读取 content_list.json 失败: {content_list_path} - {e}")
        return md_content

    if not isinstance(content_list, list) or not content_list:
        return md_content

    page_first_texts: Dict[int, str] = {}
    for item in content_list:
        if not isinstance(item, dict):
            continue
        page_idx = item.get("page_idx")
        if page_idx is None or page_idx in page_first_texts:
            continue
        item_type = item.get("type", "")
        if item_type in _AUXILIARY_TYPES:
            continue
        text = ""
        if item_type in ("text", "equation") and item.get("text"):
            text = item["text"].strip()
        if not text:
            continue
        page_first_texts[page_idx] = text

    if not page_first_texts:
        return md_content

    lines = md_content.split("\n")
    page_line_map: Dict[int, int] = {}
    search_start = 0

    for page_idx in sorted(page_first_texts.keys()):
        first_text = page_first_texts[page_idx]
        search_text = first_text[:30].strip()
        if not search_text:
            continue

        for line_num in range(search_start, len(lines)):
            if search_text in lines[line_num]:
                page_line_map[page_idx] = line_num
                search_start = line_num + 1
                break

    if not page_line_map:
        return md_content

    for page_idx in sorted(page_line_map.keys(), reverse=True):
        line_num = page_line_map[page_idx]
        page_number = page_idx + 1 + start_page_id
        lines.insert(line_num, f"<!-- page={page_number} -->")

    return "\n".join(lines)


def _merge_chunks(chunk_results: List[str], config: Dict[str, Any]) -> str:
    """合并多个分块的 Markdown 内容，处理重叠部分"""
    if not chunk_results:
        return ""
    if len(chunk_results) == 1:
        return chunk_results[0]

    overlap_pages = config.get("chunk_overlap", 10)
    chunk_size = config.get("chunk_size", 200)
    first_chunk_lines = len(chunk_results[0].split("\n"))
    overlap_lines = max(overlap_pages * 30, int(first_chunk_lines * overlap_pages / chunk_size))

    merged = ""

    for chunk_content in chunk_results:
        chunk_lines = chunk_content.split("\n")

        if merged:
            merged_lines = merged.split("\n")
            found = _find_overlap(merged_lines, chunk_lines, overlap_lines)
            if found > 0:
                chunk_lines = chunk_lines[found:]
                chunk_content = "\n".join(chunk_lines)

        merged += "\n\n" + chunk_content if merged else chunk_content

    merged = clean_markdown(merged, config)
    return merged


def _normalize_for_comparison(text: str) -> str:
    """标准化空白符：去除首尾空白，将中间连续空白替换为单个空格"""
    return ' '.join(text.strip().split())


def _collapse_details_lines(lines):
    """折叠 <details> 块为单行标记，用于模糊匹配"""
    collapsed = []
    orig_counts = []
    in_details = False
    detail_lines = 0

    for line in lines:
        if '<details>' in line:
            in_details = True
            collapsed.append('<DETAILS/>')
            detail_lines = 1
            continue
        if '</details>' in line:
            in_details = False
            detail_lines += 1
            orig_counts.append(detail_lines)
            detail_lines = 0
            continue
        if not in_details:
            collapsed.append(line)
            orig_counts.append(1)
        else:
            detail_lines += 1

    return collapsed, orig_counts


def _find_overlap(merged_lines: List[str], chunk_lines: List[str], max_overlap: int) -> int:
    """查找两个分块之间的重叠行数"""
    if len(merged_lines) < 1 or len(chunk_lines) < 1:
        return 0

    max_check = min(max_overlap, len(merged_lines), len(chunk_lines))

    # 精确匹配
    for i in range(max_check, 0, -1):
        if merged_lines[-i:] == chunk_lines[:i]:
            return i

    # 标准化后匹配
    norm_merged = [_normalize_for_comparison(l) for l in merged_lines]
    norm_chunk = [_normalize_for_comparison(l) for l in chunk_lines]

    for i in range(max_check, 0, -1):
        if norm_merged[-i:] == norm_chunk[:i]:
            return i

    # 折叠 details 后匹配
    collapsed_merged, merged_orig_counts = _collapse_details_lines(norm_merged)
    collapsed_chunk, chunk_orig_counts = _collapse_details_lines(norm_chunk)

    max_collapsed = min(max_check, len(collapsed_merged), len(collapsed_chunk))

    for i in range(max_collapsed, 0, -1):
        if collapsed_merged[-i:] == collapsed_chunk[:i]:
            return sum(chunk_orig_counts[:i])

    # 基于签名的模糊匹配
    merged_full = ' '.join(norm_merged)
    chunk_full = ' '.join(norm_chunk)

    merged_nospace = merged_full.replace(' ', '')
    chunk_nospace = chunk_full.replace(' ', '')

    search_start = 0

    for sig_len in (200, 150, 100, 80, 60, 50, 40, 30, 20):
        actual_sig_len = min(sig_len, len(chunk_nospace))
        if actual_sig_len < 20:
            continue
        signature = chunk_nospace[:actual_sig_len]
        pos = merged_nospace.find(signature, search_start)
        if pos >= 0:
            overlap_nospace_len = len(merged_nospace) - pos
            cum_chars = 0
            for line_idx, nl in enumerate(norm_chunk):
                cum_chars += len(nl.replace(' ', ''))
                if cum_chars >= overlap_nospace_len:
                    return max(1, line_idx + 1)
            return len(norm_chunk)

    return 0


def _merge_metadata(
    chunk_metadata: List[Dict[str, Any]],
    total_pages: int,
    intake_paths: List[Path],
) -> Dict[str, Any]:
    """合并多个分块的元数据"""
    merged = {}
    if chunk_metadata:
        merged = dict(chunk_metadata[0])

    merged["page_count"] = total_pages
    merged["chunk_count"] = len(chunk_metadata)
    merged["chunk_paths"] = [str(p) for p in intake_paths]
    return merged


def _cleanup_temp_dirs(dirs: List[Path]) -> None:
    """清理临时目录列表"""
    for d in dirs:
        if d and d.exists():
            import shutil
            shutil.rmtree(d, ignore_errors=True)
