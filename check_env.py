#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EngramNote 环境自动检测与初始化脚本

功能：
1. 检测 Python 版本与 conda 环境
2. 检测 Node.js 与 npm
3. 检测后端 Python 依赖（含 qwen_asr 可选，自动安装缺失依赖，使用清华源）
4. 检测前端 Node 依赖（自动 npm install，使用淘宝源）
5. 检测 .env 配置文件（引导用户填写 API 密钥）
6. 检测并下载 BGE-M3 嵌入模型（必需，ModelScope 国内源）
7. 检测并下载 Silero VAD 模型（可选，ASR 语音活动检测用）
8. 检测并下载 MinerU 模型（可选，PDF 解析用，本地 pipeline 模式）
9. 检测并下载 Qwen3-ASR 模型（可选，音视频转写用）
10. 创建必要的运行时数据目录

使用方法：
    python check_env.py                # 仅检测不修复
    python check_env.py --fix          # 检测并自动修复必需项（环境+依赖+BGE-M3+VAD）
    python check_env.py --fix --all    # 包含可选大模型（MinerU + ASR）
    python check_env.py --download-mineru   # 仅下载 MinerU 模型
    python check_env.py --download-asr      # 仅下载 ASR 模型
"""

import os
import sys
import subprocess
import importlib
import shutil
import json
import urllib.request
import urllib.error
from pathlib import Path

# ============================================================
# 配置
# ============================================================
PROJECT_ROOT = Path(__file__).resolve().parent
BACKEND_DIR = PROJECT_ROOT / "backend"
FRONTEND_DIR = PROJECT_ROOT / "frontend"
DATA_DIR = BACKEND_DIR / "data"
MODELS_DIR = DATA_DIR / "models"

# ModelScope 缓存目录
MODELSCOPE_CACHE = Path.home() / ".cache" / "modelscope" / "hub"

# 模型清单
MODELS = {
    "bge_m3": {
        "name": "BGE-M3 嵌入模型",
        "modelscope_id": "Xorbits/bge-m3",
        "cache_paths": [
            MODELSCOPE_CACHE / "Xorbits" / "bge-m3",
            Path.home() / ".cache" / "huggingface" / "hub" / "models--BAAI--bge-m3",
        ],
        "required": True,
        "size": "约 2.2GB",
        "purpose": "文本向量化，用于清洗去重和知识图谱（必需）",
    },
    "silero_vad": {
        "name": "Silero VAD 模型",
        "local_path": MODELS_DIR / "silero-vad" / "silero_vad.jit",
        "required": False,
        "size": "约 2MB",
        "purpose": "语音活动检测，ASR 音视频转写时切分语音段（可选）",
    },
    "mineru_pipeline": {
        "name": "MinerU Pipeline 模型 (PDF-Extract-Kit-1.0)",
        "modelscope_id": "OpenDataLab/PDF-Extract-Kit-1.0",
        "cache_paths": [
            MODELSCOPE_CACHE / "models" / "OpenDataLab" / "PDF-Extract-Kit-1___0",
            MODELSCOPE_CACHE / "models" / "OpenDataLab" / "PDF-Extract-Kit-1.0",
        ],
        "required": False,
        "size": "约 5GB+",
        "purpose": "PDF 本地解析（布局检测、公式识别、表格识别）。使用云端 API 时不需要",
    },
    "mineru_vlm": {
        "name": "MinerU VLM 模型 (MinerU2.5-Pro-2604-1.2B)",
        "modelscope_id": "OpenDataLab/MinerU2.5-Pro-2604-1.2B",
        "cache_paths": [
            MODELSCOPE_CACHE / "models" / "OpenDataLab" / "MinerU2___5-Pro-2604-1___2B",
            MODELSCOPE_CACHE / "models" / "OpenDataLab" / "MinerU2.5-Pro-2604-1.2B",
        ],
        "required": False,
        "size": "约 2.5GB",
        "purpose": "PDF 本地解析（视觉语言模型）。使用云端 API 时不需要",
    },
    "qwen_asr": {
        "name": "Qwen3-ASR-0.6B 语音识别模型",
        "modelscope_id": "Qwen/Qwen3-ASR-0.6B",
        "cache_paths": [
            MODELSCOPE_CACHE / "models" / "Qwen" / "Qwen3-ASR-0___6B",
            MODELSCOPE_CACHE / "models" / "Qwen" / "Qwen3-ASR-0.6B",
        ],
        "required": False,
        "size": "约 1.2GB",
        "purpose": "音视频转写（语音转文字）。不需要音视频功能时可跳过",
    },
}

# 颜色输出
class Color:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    END = "\033[0m"

def ok(msg):
    print(f"  {Color.GREEN}[✓]{Color.END} {msg}")

def fail(msg):
    print(f"  {Color.RED}[✗]{Color.END} {msg}")

def warn(msg):
    print(f"  {Color.YELLOW}[!]{Color.END} {msg}")

def info(msg):
    print(f"  {Color.BLUE}[i]{Color.END} {msg}")

def header(title):
    print(f"\n{Color.BOLD}{Color.CYAN}{'=' * 50}{Color.END}")
    print(f"{Color.BOLD}{Color.CYAN}  {title}{Color.END}")
    print(f"{Color.BOLD}{Color.CYAN}{'=' * 50}{Color.END}")

# 检测结果统计
results = {"pass": 0, "fail": 0, "warn": 0, "fixed": 0}

def record_pass():
    results["pass"] += 1

def record_fail():
    results["fail"] += 1

def record_warn():
    results["warn"] += 1

def record_fixed():
    results["fixed"] += 1

# ============================================================
# 辅助：ModelScope 下载
# ============================================================
def download_from_modelscope(model_id, cache_subdir=None):
    """通过 ModelScope 下载模型，返回模型路径或 None"""
    try:
        from modelscope import snapshot_download
    except ImportError:
        warn("modelscope 库未安装，正在安装（清华源）...")
        subprocess.run([
            sys.executable, "-m", "pip", "install", "modelscope",
            "-i", "https://pypi.tuna.tsinghua.edu.cn/simple",
            "--trusted-host", "pypi.tuna.tsinghua.edu.cn"
        ], check=True)
        from modelscope import snapshot_download

    info(f"正在从 ModelScope 下载：{model_id}")
    model_dir = snapshot_download(model_id, cache_dir=str(MODELSCOPE_CACHE))
    return model_dir

def find_model_cache(model_key):
    """检查模型是否已缓存，返回路径或 None"""
    model = MODELS[model_key]
    # 检查本地路径
    if "local_path" in model:
        if model["local_path"].exists():
            return model["local_path"]
    # 检查缓存路径
    if "cache_paths" in model:
        for p in model["cache_paths"]:
            if p.exists():
                return p
        # 模糊匹配（ModelScope 会把 '.' 编码为 '___'）
        if "modelscope_id" in model:
            encoded = model["modelscope_id"].replace(".", "___")
            for p in [MODELSCOPE_CACHE, MODELSCOPE_CACHE / "models"]:
                if p.exists():
                    for item in p.rglob("*"):
                        if item.is_dir() and encoded.split("/")[-1] in item.name:
                            return item
    return None

# ============================================================
# 1. 检测 Python 版本
# ============================================================
def check_python():
    header("步骤 1/10：检测 Python 环境")
    version = sys.version_info
    if version >= (3, 10):
        ok(f"Python {version.major}.{version.minor}.{version.micro}")
        record_pass()
    else:
        fail(f"Python 版本过低：{version.major}.{version.minor}.{version.micro}，需要 3.10+")
        info("请安装 Python 3.10+：https://www.python.org/downloads/")
        info("或使用 conda：conda create -n mineru_env python=3.10")
        record_fail()
        return False

    conda_env = os.environ.get("CONDA_DEFAULT_ENV", "")
    if conda_env:
        ok(f"当前 Conda 环境：{conda_env}")
    else:
        warn("未检测到 Conda 环境，建议使用 conda 管理依赖")
        info("创建环境：conda create -n mineru_env python=3.10 && conda activate mineru_env")
        record_warn()
    return True

# ============================================================
# 2. 检测 Node.js
# ============================================================
def check_node():
    header("步骤 2/10：检测 Node.js 环境")
    try:
        result = subprocess.run(["node", "--version"], capture_output=True, text=True, timeout=10)
        version = result.stdout.strip().lstrip("v")
        major = int(version.split(".")[0])
        if major >= 18:
            ok(f"Node.js v{version}")
            record_pass()
        else:
            fail(f"Node.js 版本过低：v{version}，需要 18+")
            info("请安装 Node.js 18+：https://nodejs.org/")
            record_fail()
            return False
    except FileNotFoundError:
        fail("未检测到 Node.js")
        info("请安装 Node.js 18+：https://nodejs.org/")
        record_fail()
        return False
    except Exception as e:
        fail(f"Node.js 检测失败：{e}")
        record_fail()
        return False

    try:
        result = subprocess.run(["npm", "--version"], capture_output=True, text=True, timeout=10)
        ok(f"npm v{result.stdout.strip()}")
        record_pass()
    except FileNotFoundError:
        fail("未检测到 npm")
        record_fail()
        return False
    return True

# ============================================================
# 3. 检测后端 Python 依赖
# ============================================================
REQUIRED_PACKAGES = {
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "sqlalchemy": "sqlalchemy",
    "aiosqlite": "aiosqlite",
    "pydantic": "pydantic",
    "pydantic_settings": "pydantic-settings",
    "jose": "python-jose",
    "bcrypt": "bcrypt",
    "celery": "celery",
    "numpy": "numpy",
    "sentence_transformers": "sentence-transformers",
    "chromadb": "chromadb",
    "multipart": "python-multipart",
    "openai": "openai",
    "soundfile": "soundfile",
    "torch": "torch",
    "yaml": "pyyaml",
}

# 可选包：qwen_asr（ASR 音视频转写用）
OPTIONAL_PACKAGES = {
    "qwen_asr": ("qwen-asr", "ASR 音视频转写"),
}

def check_backend_deps(auto_fix=False):
    header("步骤 3/10：检测后端 Python 依赖")
    missing = []
    for module, package in REQUIRED_PACKAGES.items():
        try:
            importlib.import_module(module)
            ok(f"{package}")
        except ImportError:
            fail(f"{package} 未安装")
            missing.append(package)

    if missing:
        if auto_fix:
            info(f"正在安装缺失依赖（{len(missing)} 个，使用清华 PyPI 源）...")
            cmd = [
                sys.executable, "-m", "pip", "install",
                *missing,
                "-i", "https://pypi.tuna.tsinghua.edu.cn/simple",
                "--trusted-host", "pypi.tuna.tsinghua.edu.cn"
            ]
            try:
                subprocess.run(cmd, check=True)
                ok(f"已安装 {len(missing)} 个依赖")
                record_fixed()
            except subprocess.CalledProcessError as e:
                fail(f"依赖安装失败：{e}")
                info("请手动安装：pip install -r backend/requirements.txt")
                record_fail()
                return False
        else:
            warn(f"有 {len(missing)} 个依赖未安装，运行 --fix 自动安装")
            info("手动安装：pip install -r backend/requirements.txt")
            record_warn()
            return False
    else:
        record_pass()

    # 检测可选包 qwen_asr
    print()
    info("可选依赖（音视频转写）：")
    for module, (package, desc) in OPTIONAL_PACKAGES.items():
        try:
            importlib.import_module(module)
            ok(f"{package}（{desc}）")
        except ImportError:
            if auto_fix:
                info(f"正在安装可选依赖 {package}（{desc}）...")
                try:
                    subprocess.run([
                        sys.executable, "-m", "pip", "install", package,
                        "-i", "https://pypi.tuna.tsinghua.edu.cn/simple",
                        "--trusted-host", "pypi.tuna.tsinghua.edu.cn"
                    ], check=True)
                    ok(f"{package} 已安装")
                    record_fixed()
                except subprocess.CalledProcessError:
                    warn(f"{package} 安装失败（不影响 PDF/图片功能，仅音视频转写需要）")
                    info(f"手动安装：pip install {package}")
                    record_warn()
            else:
                warn(f"{package} 未安装（{desc}，可选）")
                info(f"如需音视频转写：pip install {package}")
                record_warn()
    return True

# ============================================================
# 4. 检测前端 Node 依赖
# ============================================================
def check_frontend_deps(auto_fix=False):
    header("步骤 4/10：检测前端 Node 依赖")
    node_modules = FRONTEND_DIR / "node_modules"
    package_json = FRONTEND_DIR / "package.json"

    if not package_json.exists():
        fail(f"未找到 {package_json}")
        record_fail()
        return False

    if node_modules.exists():
        ok("node_modules 已存在")
        record_pass()
        return True
    else:
        fail("node_modules 不存在")

    if auto_fix:
        info("正在安装前端依赖（使用淘宝 npm 源）...")
        cmd = ["npm", "install", "--registry=https://registry.npmmirror.com"]
        try:
            subprocess.run(cmd, cwd=str(FRONTEND_DIR), check=True, shell=True)
            ok("前端依赖安装完成")
            record_fixed()
            return True
        except subprocess.CalledProcessError as e:
            fail(f"前端依赖安装失败：{e}")
            info(f"请手动安装：cd frontend && npm install")
            record_fail()
            return False
    else:
        warn("前端依赖未安装，运行 --fix 自动安装")
        info("手动安装：cd frontend && npm install")
        record_warn()
        return False

# ============================================================
# 5. 检测 .env 配置文件
# ============================================================
def check_env_file(auto_fix=False):
    header("步骤 5/10：检测 .env 配置文件")
    env_file = BACKEND_DIR / ".env"
    env_example = BACKEND_DIR / ".env.example"

    if env_file.exists():
        ok(".env 文件已存在")
        content = env_file.read_text(encoding="utf-8")
        has_deepseek = "DEEPSEEK_API_KEY=" in content and len(content.split("DEEPSEEK_API_KEY=")[1].split("\n")[0].strip()) > 0
        has_glm = "GLM_API_KEY=" in content and len(content.split("GLM_API_KEY=")[1].split("\n")[0].strip()) > 0

        if has_deepseek or has_glm:
            ok("已配置 AI API 密钥（DeepSeek 或 GLM）")
            record_pass()
        else:
            warn(".env 文件存在但未配置 AI API 密钥")
            info("至少配置一个 LLM API 密钥（详见 README.md API 密钥获取指南）")
            record_warn()

        # 检测 Mineru 配置模式
        has_mineru_token = "MINERU_API_TOKEN=" in content and len(content.split("MINERU_API_TOKEN=")[1].split("\n")[0].strip()) > 0
        mineru_backend = ""
        for line in content.split("\n"):
            if line.strip().startswith("MINERU_BACKEND="):
                mineru_backend = line.split("=", 1)[1].strip()
                break

        if has_mineru_token:
            ok("已配置 Mineru API Token（云端解析模式，无需下载本地模型）")
        elif mineru_backend == "pipeline":
            warn("MINERU_BACKEND=pipeline 但未配置 MINERU_API_TOKEN")
            info("本地 pipeline 模式需要下载 MinerU 模型（步骤 8）")
        return True
    else:
        fail(".env 文件不存在")

    if auto_fix and env_example.exists():
        info("从 .env.example 创建 .env 文件...")
        shutil.copy(env_example, env_file)
        ok(".env 文件已创建")
        record_fixed()
        warn("请编辑 backend/.env 填入你的 API 密钥（详见 README.md）")
        info("  DeepSeek：https://platform.deepseek.com/")
        info("  GLM：https://open.bigmodel.cn/")
        info("  Mineru：https://mineru.net/（PDF 解析可选）")
        record_warn()
        return True
    else:
        if env_example.exists():
            info("请手动创建：cp backend/.env.example backend/.env")
        else:
            info("请参考 README.md 手动创建 .env 文件")
        record_fail()
        return False

# ============================================================
# 6. 检测并下载 BGE-M3 嵌入模型（必需）
# ============================================================
def check_bge_m3(auto_fix=False):
    header("步骤 6/10：检测 BGE-M3 嵌入模型（必需）")
    model = MODELS["bge_m3"]
    info(f"用途：{model['purpose']}")
    info(f"大小：{model['size']}")

    cached = find_model_cache("bge_m3")
    if cached:
        ok(f"BGE-M3 模型已缓存：{cached}")
        record_pass()
        return True

    fail("BGE-M3 模型未下载")

    if auto_fix:
        info("将通过 ModelScope（国内源）下载 BGE-M3 模型...")
        info("模型大小约 2.2GB，首次下载需要较长时间，请耐心等待")
        try:
            model_dir = download_from_modelscope(model["modelscope_id"])
            ok(f"BGE-M3 模型已下载：{model_dir}")
            record_fixed()
            return True
        except Exception as e:
            fail(f"ModelScope 下载失败：{e}")
            info("备选方案 1：使用 HuggingFace 镜像下载")
            info("  set HF_ENDPOINT=https://hf-mirror.com")
            info("  python -c \"from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-m3')\"")
            info("备选方案 2：手动下载到 data/models/bge-m3/")
            record_fail()
            return False
    else:
        warn("BGE-M3 模型未下载，运行 --fix 自动下载（通过 ModelScope 国内源）")
        info("模型大小约 2.2GB，首次使用清洗功能时会自动下载")
        info("手动下载：pip install modelscope && python -c \"from modelscope import snapshot_download; snapshot_download('Xorbits/bge-m3')\"")
        record_warn()
        return False

# ============================================================
# 7. 检测并下载 Silero VAD 模型（可选，ASR 用）
# ============================================================
def check_silero_vad(auto_fix=False):
    header("步骤 7/10：检测 Silero VAD 模型（可选，ASR 用）")
    model = MODELS["silero_vad"]
    info(f"用途：{model['purpose']}")
    info(f"大小：{model['size']}")

    vad_file = model["local_path"]
    if vad_file.exists():
        ok(f"Silero VAD 模型已存在：{vad_file}")
        record_pass()
        return True

    # 检查 torch.hub 缓存
    try:
        import torch
        hub_jit = Path(torch.hub.get_dir()) / "snakers4_silero-vad_master" / "src" / "silero_vad" / "data" / "silero_vad.jit"
        if hub_jit.exists():
            ok(f"Silero VAD 已在 torch.hub 缓存：{hub_jit}")
            record_pass()
            return True
    except Exception:
        pass

    fail("Silero VAD 模型未下载")

    if auto_fix:
        info("正在下载 Silero VAD 模型（约 2MB）...")
        vad_file.parent.mkdir(parents=True, exist_ok=True)

        # 方法 1：通过 torch.hub 下载（会自动缓存）
        try:
            import torch
            info("尝试通过 torch.hub 下载...")
            model_hub, utils = torch.hub.load(
                repo_or_dir="snakers4/silero-vad",
                model="silero_vad",
                trust_repo=True,
            )
            # 保存到本地
            torch.jit.save(model_hub, str(vad_file))
            ok(f"Silero VAD 模型已下载并保存：{vad_file}")
            record_fixed()
            return True
        except Exception as e:
            warn(f"torch.hub 下载失败：{e}")

        # 方法 2：通过 ModelScope 下载
        try:
            info("尝试通过 ModelScope 下载...")
            ms_path = download_from_modelscope("silero-vad/silero-vad")
            # 查找下载的 jit 文件
            for jit_file in Path(ms_path).rglob("silero_vad*.jit"):
                shutil.copy(jit_file, vad_file)
                ok(f"Silero VAD 模型已下载：{vad_file}")
                record_fixed()
                return True
            for onnx_file in Path(ms_path).rglob("silero_vad*.onnx"):
                shutil.copy(onnx_file, vad_file.parent / "silero_vad.onnx")
                ok(f"Silero VAD 模型已下载（onnx 格式）")
                record_fixed()
                return True
            warn("ModelScope 下载完成但未找到模型文件")
        except Exception as e:
            warn(f"ModelScope 下载失败：{e}")

        warn("ASR 语音活动检测功能可选，不下载不影响 PDF/图片/Office 功能")
        info("首次使用 ASR 时系统会自动尝试下载")
        record_warn()
        return False
    else:
        warn("Silero VAD 模型未下载（仅 ASR 功能需要，可跳过）")
        info("运行 --fix 自动下载，或首次使用 ASR 时自动下载")
        record_warn()
        return False

# ============================================================
# 8. 检测并下载 MinerU 模型（可选，PDF 本地解析用）
# ============================================================
def check_mineru_models(auto_fix=False, force_download=False):
    header("步骤 8/10：检测 MinerU 模型（可选，PDF 本地解析用）")

    # 判断是否使用云端 API 模式
    env_file = BACKEND_DIR / ".env"
    use_cloud_api = False
    if env_file.exists():
        content = env_file.read_text(encoding="utf-8")
        has_token = "MINERU_API_TOKEN=" in content and len(content.split("MINERU_API_TOKEN=")[1].split("\n")[0].strip()) > 0
        backend_mode = ""
        for line in content.split("\n"):
            if line.strip().startswith("MINERU_BACKEND="):
                backend_mode = line.split("=", 1)[1].strip()
                break
        if has_token and backend_mode in ("vlm-http-client", "hybrid-http-client"):
            use_cloud_api = True

    if use_cloud_api:
        ok("已配置 Mineru 云端 API（vlm-http-client 模式）")
        info("使用云端 API 解析 PDF，无需下载本地 MinerU 模型（节省约 7GB 磁盘空间）")
        record_pass()
        return True

    info("MinerU 本地 pipeline 模式需要 2 个模型：")
    info(f"  - {MODELS['mineru_pipeline']['name']}（{MODELS['mineru_pipeline']['size']}）")
    info(f"  - {MODELS['mineru_vlm']['name']}（{MODELS['mineru_vlm']['size']}）")
    info(f"  总计约 7GB+，下载时间较长")
    info(f"  如需使用云端 API 替代，请在 .env 中配置 MINERU_API_TOKEN 和 MINERU_BACKEND=vlm-http-client")
    print()

    # 检测已有模型
    pipeline_cached = find_model_cache("mineru_pipeline")
    vlm_cached = find_model_cache("mineru_vlm")

    if pipeline_cached:
        ok(f"Pipeline 模型已缓存：{pipeline_cached}")
    else:
        fail("Pipeline 模型 (PDF-Extract-Kit-1.0) 未下载")

    if vlm_cached:
        ok(f"VLM 模型已缓存：{vlm_cached}")
    else:
        fail("VLM 模型 (MinerU2.5-Pro-2604-1.2B) 未下载")

    if pipeline_cached and vlm_cached:
        record_pass()
        return True

    should_download = force_download
    if auto_fix and not force_download:
        # --fix 模式下不自动下载大模型，询问用户
        warn("MinerU 模型较大（约 7GB），--fix 模式下不自动下载")
        info("如需下载，请运行：python check_env.py --fix --all")
        info("或单独下载：python check_env.py --download-mineru")
        info("或使用云端 API：在 .env 中配置 MINERU_API_TOKEN 并设置 MINERU_BACKEND=vlm-http-client")
        record_warn()
        return False

    if should_download:
        # 下载 Pipeline 模型
        if not pipeline_cached:
            info("正在下载 Pipeline 模型（PDF-Extract-Kit-1.0，约 5GB）...")
            info("这可能需要较长时间，请耐心等待")
            try:
                model_dir = download_from_modelscope(MODELS["mineru_pipeline"]["modelscope_id"])
                ok(f"Pipeline 模型已下载：{model_dir}")
                record_fixed()
            except Exception as e:
                fail(f"Pipeline 模型下载失败：{e}")
                info("请检查网络连接，或手动下载：")
                info("  pip install modelscope")
                info("  python -c \"from modelscope import snapshot_download; snapshot_download('OpenDataLab/PDF-Extract-Kit-1.0')\"")
                record_fail()
                return False

        # 下载 VLM 模型
        if not vlm_cached:
            info("正在下载 VLM 模型（MinerU2.5-Pro-2604-1.2B，约 2.5GB）...")
            try:
                model_dir = download_from_modelscope(MODELS["mineru_vlm"]["modelscope_id"])
                ok(f"VLM 模型已下载：{model_dir}")
                record_fixed()
            except Exception as e:
                fail(f"VLM 模型下载失败：{e}")
                info("请手动下载：")
                info("  python -c \"from modelscope import snapshot_download; snapshot_download('OpenDataLab/MinerU2.5-Pro-2604-1.2B')\"")
                record_fail()
                return False
        return True
    else:
        warn("MinerU 模型未下载（PDF 本地解析功能不可用）")
        info("下载命令：python check_env.py --download-mineru")
        info("或使用云端 API（推荐）：在 .env 中配置 MINERU_API_TOKEN")
        record_warn()
        return False

# ============================================================
# 9. 检测并下载 Qwen3-ASR 模型（可选，音视频转写用）
# ============================================================
def check_qwen_asr(auto_fix=False, force_download=False):
    header("步骤 9/10：检测 Qwen3-ASR 模型（可选，音视频转写用）")
    model = MODELS["qwen_asr"]
    info(f"用途：{model['purpose']}")
    info(f"大小：{model['size']}")

    cached = find_model_cache("qwen_asr")
    if cached:
        ok(f"Qwen3-ASR 模型已缓存：{cached}")
        record_pass()
        return True

    fail("Qwen3-ASR 模型未下载")

    # 检查 qwen_asr 包是否安装
    try:
        importlib.import_module("qwen_asr")
    except ImportError:
        warn("qwen_asr 包未安装")
        if auto_fix:
            info("正在安装 qwen_asr（清华源）...")
            try:
                subprocess.run([
                    sys.executable, "-m", "pip", "install", "qwen-asr",
                    "-i", "https://pypi.tuna.tsinghua.edu.cn/simple",
                    "--trusted-host", "pypi.tuna.tsinghua.edu.cn"
                ], check=True)
                ok("qwen_asr 已安装")
            except subprocess.CalledProcessError:
                fail("qwen_asr 安装失败")
                info("手动安装：pip install qwen-asr")
                record_fail()
                return False
        else:
            info("运行 --fix 自动安装 qwen_asr")
            record_warn()
            return False

    should_download = force_download
    if auto_fix and not force_download:
        warn("Qwen3-ASR 模型较大（约 1.2GB），--fix 模式下不自动下载")
        info("如需下载，请运行：python check_env.py --fix --all")
        info("或单独下载：python check_env.py --download-asr")
        record_warn()
        return False

    if should_download:
        info("正在下载 Qwen3-ASR 模型（约 1.2GB）...")
        try:
            model_dir = download_from_modelscope(model["modelscope_id"])
            ok(f"Qwen3-ASR 模型已下载：{model_dir}")
            record_fixed()
            return True
        except Exception as e:
            fail(f"Qwen3-ASR 模型下载失败：{e}")
            info("请手动下载：")
            info("  python -c \"from modelscope import snapshot_download; snapshot_download('Qwen/Qwen3-ASR-0.6B')\"")
            record_fail()
            return False
    else:
        warn("Qwen3-ASR 模型未下载（音视频转写功能不可用）")
        info("下载命令：python check_env.py --download-asr")
        info("如不需要音视频转写功能，可跳过")
        record_warn()
        return False

# ============================================================
# 10. 创建运行时数据目录
# ============================================================
def create_data_dirs():
    header("步骤 10/10：创建运行时数据目录")
    dirs = [
        DATA_DIR / "db",
        DATA_DIR / "storage",
        DATA_DIR / "celery" / "broker",
        DATA_DIR / "celery" / "results",
        DATA_DIR / "chroma",
        DATA_DIR / "logs",
        MODELS_DIR / "silero-vad",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
        ok(f"目录就绪：{d.relative_to(PROJECT_ROOT)}")
    record_pass()

# ============================================================
# 主函数
# ============================================================
def print_summary():
    header("检测总结")
    total = results["pass"] + results["fail"] + results["warn"]
    print(f"  {Color.GREEN}通过：{results['pass']}{Color.END}")
    print(f"  {Color.RED}失败：{results['fail']}{Color.END}")
    print(f"  {Color.YELLOW}警告：{results['warn']}{Color.END}")
    if results["fixed"] > 0:
        print(f"  {Color.CYAN}已自动修复：{results['fixed']}{Color.END}")
    print()

    if results["fail"] == 0 and results["warn"] == 0:
        print(f"  {Color.GREEN}{Color.BOLD}✓ 所有检测通过！可以启动项目了。{Color.END}")
        print(f"  启动命令：start.bat（Windows）或 ./start.sh（Linux/Mac）")
    elif results["fail"] == 0:
        print(f"  {Color.YELLOW}{Color.BOLD}! 基本就绪，但有警告项需要关注。{Color.END}")
        print(f"  运行 python check_env.py --fix 自动修复必需项")
        print(f"  运行 python check_env.py --fix --all 下载所有可选模型")
    else:
        print(f"  {Color.RED}{Color.BOLD}✗ 有 {results['fail']} 项失败，请根据上述提示修复。{Color.END}")
        print(f"  运行 python check_env.py --fix 尝试自动修复")

    print()
    print(f"  {Color.CYAN}模型下载命令：{Color.END}")
    print(f"    python check_env.py --fix              # 必需项（BGE-M3 + VAD）")
    print(f"    python check_env.py --fix --all        # 全部（含 MinerU + ASR）")
    print(f"    python check_env.py --download-mineru  # 仅 MinerU 模型")
    print(f"    python check_env.py --download-asr     # 仅 ASR 模型")

def main():
    print(f"\n{Color.BOLD}{Color.CYAN}")
    print("  ╔═══════════════════════════════════════════╗")
    print("  ║   EngramNote 环境自动检测与初始化工具     ║")
    print("  ╚═══════════════════════════════════════════╝")
    print(f"{Color.END}")

    auto_fix = "--fix" in sys.argv
    download_all = "--all" in sys.argv
    download_mineru = "--download-mineru" in sys.argv
    download_asr = "--download-asr" in sys.argv
    check_only = "--check" in sys.argv

    # 单独下载模式
    if download_mineru:
        info("单独下载 MinerU 模型模式")
        check_mineru_models(auto_fix=True, force_download=True)
        print_summary()
        return

    if download_asr:
        info("单独下载 Qwen3-ASR 模型模式")
        # 先确保 qwen_asr 包安装
        try:
            importlib.import_module("qwen_asr")
        except ImportError:
            info("正在安装 qwen_asr（清华源）...")
            subprocess.run([
                sys.executable, "-m", "pip", "install", "qwen-asr",
                "-i", "https://pypi.tuna.tsinghua.edu.cn/simple",
                "--trusted-host", "pypi.tuna.tsinghua.edu.cn"
            ], check=True)
        check_qwen_asr(auto_fix=True, force_download=True)
        print_summary()
        return

    if auto_fix:
        if download_all:
            info("已启用 --fix --all 模式：将下载所有模型（含 MinerU + ASR 大模型）")
        else:
            info("已启用 --fix 模式：将自动安装依赖 + 下载必需模型（BGE-M3 + VAD）")
            info("如需下载 MinerU 和 ASR 大模型，请加 --all 参数")
    elif check_only:
        info("已启用 --check 模式：仅检测不修复")

    fix = auto_fix or (not check_only and not download_mineru and not download_asr)
    # 默认（无参数）= 仅检测
    if not auto_fix and not download_mineru and not download_asr:
        fix = False

    # 执行检测
    check_python()
    check_node()
    check_backend_deps(fix)
    check_frontend_deps(fix)
    check_env_file(fix)
    check_bge_m3(fix)
    check_silero_vad(fix)
    check_mineru_models(fix, force_download=download_all)
    check_qwen_asr(fix, force_download=download_all)
    create_data_dirs()

    print_summary()

if __name__ == "__main__":
    main()
