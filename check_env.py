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
    python check_env.py --check --deep      # 依赖全部真实导入（最严格，也最慢）

关于依赖检测的速度（2026-09-26 改造）：
    第 3 步原来把 22 个模块**全部真实导入**，本机实测 29.7s，其中 29.5s 花在
    三个模块上（sentence_transformers / qwen_asr / openai）—— 而**后端真实启动**
    （`import app.main`）只要 4.2s，且根本不导入它们。
    现在改成"并行 + 分层"：便宜的模块仍真实导入（并行跑），
    昂贵且不在启动闭包内的模块默认只确认"装没装"，真实导入交给 --deep。
    详见 HEAVY_MODULES 的说明。
"""

import os
import sys
import time
import subprocess
import importlib
import importlib.util
import shutil
import json
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor
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

# MinerU 4.x 的配置与模型根（2026-10-21 迁移）
# 4.x 把模型统一放在 `$MINERU_HOME/models`，**不再是 ModelScope 缓存目录**。
# 项目把 MINERU_HOME 指向 D 盘（见 backend/.env），故这里也读环境变量 / .env。
def _resolve_mineru_home() -> Path:
    """MINERU_HOME：系统环境变量 > backend/.env > 4.x 默认值 ~/.mineru"""
    env_home = (os.environ.get("MINERU_HOME") or "").strip()
    if env_home:
        return Path(env_home)
    env_file = BACKEND_DIR / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("MINERU_HOME="):
                value = line.split("=", 1)[1].strip()
                if value:
                    return Path(value)
    return Path.home() / ".mineru"


MINERU_HOME = _resolve_mineru_home()

# 模型清单
MODELS = {
    "bge_m3": {
        "name": "BGE-M3 嵌入模型",
        "modelscope_id": "Xorbits/bge-m3",
        "cache_paths": [
            # D 盘项目目录（embedding_service.py 实际下载位置，优先检查）
            MODELS_DIR / "Xorbits" / "bge-m3",
            # C 盘 ModelScope 默认缓存
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
    "mineru_onnx": {
        "name": "MinerU 4.x 小模型 (MinerU-4_models_onnx)",
        "modelscope_id": "OpenDataLab/MinerU-4_models_onnx",
        "cache_paths": [
            # 4.x 统一放 $MINERU_HOME/models（不再是 ModelScope 缓存目录）
            MINERU_HOME / "models" / "MinerU-4_models_onnx",
        ],
        "required": False,
        "size": "约 0.9GB",
        "purpose": "PDF 本地解析（布局/OCR/公式，ONNX CPU）。basic 档位只需它",
    },
    "mineru_vlm_gguf": {
        "name": "MinerU 4.x VLM 模型 (MinerU2.5-Pro-2605-1.2B-GGUF)",
        "modelscope_id": "OpenDataLab/MinerU2.5-Pro-2605-1.2B-GGUF",
        "cache_paths": [
            MINERU_HOME / "models" / "MinerU2.5-Pro-2605-1.2B-GGUF",
        ],
        "required": False,
        "size": "约 0.5GB",
        "purpose": "PDF 本地解析（VLM，standard 档位叠在小模型之上）。云端 API 不需要",
    },
    "qwen_asr": {
        "name": "Qwen3-ASR-0.6B 语音识别模型",
        "modelscope_id": "Qwen/Qwen3-ASR-0.6B",
        "cache_paths": [
            # D 盘项目目录（优先检查）
            MODELS_DIR / "Qwen" / "Qwen3-ASR-0.6B",
            MODELS_DIR / "Qwen" / "Qwen3-ASR-0___6B",
            # C 盘 ModelScope 默认缓存
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

# 本次检测的起点（`main()` 会重置）—— 用于在总结里如实给出"启动到底等了多久"
_STARTED_AT = time.perf_counter()

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
    """通过 ModelScope 下载模型，返回模型路径或 None

    下载到项目 data/models/ 目录（D 盘），避免占用 C 盘空间。
    与 embedding_service.py 的下载行为保持一致。
    """
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
    # 下载到项目 data/models/ 目录，避免占用 C 盘空间
    # 与 embedding_service.py 的 cache_dir = str(DATA_DIR / "models") 保持一致
    model_dir = snapshot_download(model_id, cache_dir=str(MODELS_DIR))
    return model_dir

def _read_env_config():
    """读取 backend/.env 中的模型路径配置（ASR_MODEL_PATH 等）"""
    env_file = BACKEND_DIR / ".env"
    config = {}
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip()
                if v:  # 只记录非空值
                    config[k] = v
    return config

def find_model_cache(model_key):
    """检查模型是否已缓存，返回路径或 None

    检查顺序：
    1. .env 中配置的自定义路径（ASR_MODEL_PATH 等）
    2. local_path（项目内固定路径，如 silero-vad）
    3. cache_paths（含 D 盘项目目录 + C 盘 modelscope/huggingface 缓存）
    4. modelscope_id 编码后的路径（C 盘和 D 盘两种可能）
    """
    model = MODELS[model_key]

    # 1. 检查 .env 中配置的自定义路径（仅对 ASR 模型）
    if model_key == "qwen_asr":
        env_config = _read_env_config()
        asr_path = env_config.get("ASR_MODEL_PATH", "")
        if asr_path:
            p = Path(asr_path)
            if p.exists():
                return p

    # 2. 检查本地路径
    if "local_path" in model:
        if model["local_path"].exists():
            return model["local_path"]

    # 3. 检查 cache_paths（已包含 D 盘项目目录 + C 盘缓存路径）
    if "cache_paths" in model:
        for p in model["cache_paths"]:
            if p.exists():
                return p

        # 4. 检查 modelscope_id 编码后的路径（C 盘和 D 盘两种可能）
        if "modelscope_id" in model:
            encoded = model["modelscope_id"].replace(".", "___")
            org, name = encoded.split("/")
            # C 盘 ModelScope 缓存的两种路径
            for base in [MODELSCOPE_CACHE / "models" / org / name,
                         MODELSCOPE_CACHE / org / name]:
                if base.exists():
                    return base
            # D 盘项目目录下的编码路径
            for base in [MODELS_DIR / org / name,
                         MODELS_DIR / org / model["modelscope_id"].split("/")[1]]:
                if base.exists():
                    return base
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
        info("或使用 conda：conda create -n mineru_env python=3.11")
        record_fail()
        return False

    conda_env = os.environ.get("CONDA_DEFAULT_ENV", "")
    if conda_env:
        ok(f"当前 Conda 环境：{conda_env}")
    else:
        warn("未检测到 Conda 环境，建议使用 conda 管理依赖")
        info("创建环境：conda create -n mineru_env python=3.11 && conda activate mineru_env")
        record_warn()
    return True

# ============================================================
# 2. 检测 Node.js
# ============================================================
def _node_engines_requirement():
    """读 `frontend/package.json` 的 `engines.node` —— 单一来源，不再写死常量

    为什么不再写死"18+"（2026-09-15）：常量会过期，而过期的表现是**假绿**。
    本脚本曾在 Node 20 上打印 `✅ Node.js v20`，同一台机器上 `npm test` 报的
    却是一句与原因无关的 `Test Files  no tests` —— vitest 的 jsdom 环境
    （`jsdom@30`）要求 `^22.22.2 || ^24.15.0 || >=26.0.0`，它依赖的
    `undici@8` 要求 `>=22.19`。`npm ci` 对 engines 不匹配**只警告**，
    所以"版本够不够"必须有人明判；读包自己的声明（= npm 读的同一个字段）
    才不会出现"检查脚本说没问题、测试却跑不起来"。
    """
    try:
        pkg = json.loads((FRONTEND_DIR / "package.json").read_text(encoding="utf-8"))
        return (pkg.get("engines") or {}).get("node") or ""
    except Exception:
        return ""


def _satisfies_node(version, requirement):
    """版本是否满足 `engines.node` 声明；返回 True / False / None

    只实现本仓库实际用到的两种算子：
      - `^x.y.z`  = 主版本相同且 >= x.y.z
      - `>=x.y.z` = 不小于 x.y.z
    多个子句用 `||` 连接（npm 的语义是"任一满足即可"）。

    遇到不认识的算子返回 **None**（"判不了"），由调用方**说出来** ——
    不静默放过。本仓库反复抓到的病就是"检查器自己空转却报绿"。
    """
    def parts(text):
        nums = text.split(".")
        if len(nums) < 3 or not all(n.isdigit() for n in nums[:3]):
            return None
        return tuple(int(n) for n in nums[:3])

    current = parts(version)
    if current is None or not requirement.strip():
        return None
    known = False
    for clause in requirement.split("||"):
        clause = clause.strip()
        if not clause:
            continue
        if clause.startswith("^"):
            want = parts(clause[1:])
            if want is None:
                return None
            known = True
            if current[0] == want[0] and current >= want:
                return True
        elif clause.startswith(">="):
            want = parts(clause[2:])
            if want is None:
                return None
            known = True
            if current >= want:
                return True
        else:
            return None
    return False if known else None


def check_node():
    header("步骤 2/10：检测 Node.js 环境")
    requirement = _node_engines_requirement()
    try:
        result = subprocess.run(["node", "--version"], capture_output=True, text=True, timeout=10)
        version = result.stdout.strip().lstrip("v")
        satisfied = _satisfies_node(version, requirement)
        if satisfied is True:
            ok(f"Node.js v{version}（满足前端要求 {requirement}）")
            record_pass()
        elif satisfied is None:
            warn(
                f"Node.js v{version}：无法判定是否满足前端要求"
                f"（engines.node = {requirement or '未声明'}），本项跳过"
            )
            record_warn()
        else:
            fail(f"Node.js 版本过低：v{version}，前端要求 {requirement}")
            info(f"请安装满足 {requirement} 的 Node.js：https://nodejs.org/")
            record_fail()
            return False
    except FileNotFoundError:
        fail("未检测到 Node.js")
        info(f"请安装满足 {requirement or '22+'} 的 Node.js：https://nodejs.org/")
        record_fail()
        return False
    except Exception as e:
        fail(f"Node.js 检测失败：{e}")
        record_fail()
        return False

    try:
        # ## 为什么不再用 shell=True（2026-09-23 修正）
        #
        # 旧写法是 `subprocess.run(["npm", "--version"], shell=True)`，注释说
        # "Windows 上 npm 是 .cmd，需要 shell=True 才能找到"。它在 Windows 上
        # 大致能用，但在 POSIX 上是**静默假通过**：
        # `/bin/sh -c 'npm --version'` 只把 argv[0] 当命令串，`--version` 变成
        # `$0` —— npm 于是打印用法说明，退出码为 0，而这里不看退出码，
        # 直接把那段用法当版本号 `ok()` 掉（还会记一个 pass）。
        #
        # 现在：Windows 用 `npm.cmd`，其它平台用 `npm`；一律 shell=False，
        # 并显式检查 returncode 与输出形状。
        npm_cmd = "npm.cmd" if os.name == "nt" else "npm"
        result = subprocess.run(
            [npm_cmd, "--version"],
            capture_output=True, text=True, timeout=10,
            shell=False,
        )
        version = (result.stdout or "").strip()
        if result.returncode != 0 or not version or not version[0].isdigit():
            fail(f"npm 无法执行（退出码 {result.returncode}，输出：{version[:60] or '空'}）")
            record_fail()
            return False
        ok(f"npm v{version}")
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
    "multipart": "python-multipart",
    "openai": "openai",
    "soundfile": "soundfile",
    "yaml": "pyyaml",
    # PDF 路径的**模块级硬依赖**（app/services/mineru/intake.py:22-23 无条件 import）。
    # 2026-09-23 补：它们此前只登记在 requirements-test.txt 里，
    # 于是"CI 能过"掩盖了"用户装完跑不了 PDF" —— 而 PDF 是本产品的主入口。
    "pypdfium2": "pypdfium2",
    "PIL": "Pillow",
    # 嵌入可用性探测（app/services/embedding_service.py:54）
    "psutil": "psutil",
    "httpx": "httpx",
    "alembic": "alembic",
    # ⚠️ chromadb 已于阶段 2.4 移除（向量改存 chunks 表、词法检索改 FTS5）。
    #    此前它仍留在本清单里，`--fix` 甚至会把已废弃的依赖装回来 ——
    #    这是"清单描述了一个不存在的架构"的典型形态，2026-09-23 删除。
    # torch 移入 OPTIONAL_PACKAGES（需从 pytorch.org 专用源安装）
}

# 可选包：torch 和 qwen_asr（需专用源安装，不列入必需检测）
OPTIONAL_PACKAGES = {
    "qwen_asr": ("qwen-asr", "ASR 音视频转写"),
    "torch": ("torch", "PyTorch（嵌入模型和 ASR 依赖，需从 pytorch.org 安装）"),
}

# ============================================================
# 「启动路径不会导入」的重型模块 —— 默认只确认"装没装"
# ============================================================
#
# ## 为什么要有这一层（2026-09-26 实测，Windows / Python 3.14.7）
#
# `--check` 每个启动周期都要跑一次（start.bat:96 / start.sh:84），而它原来把
# 22 个模块**全部真实导入**：实测 29.7s，其中 29.5s 只花在四个模块上 ——
#     sentence_transformers  20.6s（牵连 torch 2.8s + transformers 9s）
#     qwen_asr                6.2s（独立冷启 26.6s；与前者共享依赖时降到 6.2s）
#     openai                  2.7s
#     torch                   2.8s
# 其余 18 个模块合计不到 1.4s。
#
# 而**后端真实启动**（`import app.main`）只要 4.2s，并且 `sys.modules` 里
# 没有上面任何一个。也就是说：这 29s 验的不是"项目能不能启动"，
# 而是"某个可选功能将来能不能用" —— 付在了每次启动上。
#
# ## 入层判据（两条都要满足，可复核，不是拍脑袋）
#
#   1. **导入昂贵**：该模块真实导入 > 1s；
#   2. **不在启动闭包内**：`import app.main` 之后它不在 `sys.modules` 里。
#
# 判据 2 由 `backend/tests/test_check_env_probe.py` 用**子进程实测**守着 ——
# 哪天有人把 torch/openai 加进启动路径，那条守卫会红，而不是靠人记得回来改这里。
#
# ## 代价（必须说清楚，否则就成了"为了快而放松检查"）
#
# 这四个模块"装了但一导入就炸"（Windows 上 torch 缺 DLL 是经典故障）默认
# **不会**在启动时暴露 —— 只验到"已安装"这一层。要验到真实导入：
#     python check_env.py --check --deep      # 全量真实导入（约 +26s）
# `--fix` 一律走深度检测：装完不真导一次，就等于把"假绿"写进修复流程。
#
# 输出侧配套：第 3 步结束时会明说"哪些做了真实导入、哪些只确认了安装"，
# 不把"仅确认已安装"显示成和"真实导入通过"一样的一行 ✓。
HEAVY_MODULES = {
    "sentence_transformers": "embedding_service.py:193 —— 函数内惰性导入，API 进程启动不导入",
    "torch": "asr_engine.py:11 / audio_utils.py:12 —— 仅 ASR 功能路径",
    "qwen_asr": "asr/__init__.py:50 —— 仅 ASR 功能路径",
    "openai": "asr_engine.py:12 —— 仅 ASR 功能路径",
}

#: 只确认"已安装"时给用户看的证据文本（被探测层与测试共同引用，避免两处各写一份）
DETAIL_INSTALLED_ONLY = "已安装（未做真实导入）"
#: 真实导入通过时的证据文本
DETAIL_IMPORTED = "真实导入通过"


def _probe_by_import(module):
    """真实导入一个模块 —— 最严格：能抓出"装了但导入时就炸"（缺 DLL / ABI 不匹配）

    返回 `(是否通过, 证据, 耗时秒)`。
    """
    started = time.perf_counter()
    try:
        importlib.import_module(module)
        return True, DETAIL_IMPORTED, time.perf_counter() - started
    except ImportError as e:
        return False, f"未安装（{e}）", time.perf_counter() - started
    except Exception as e:  # noqa: BLE001 - 导入期什么都可能抛（OSError/系统 DLL 缺失等）
        return False, f"导入失败（{type(e).__name__}: {e}）", time.perf_counter() - started


def _probe_by_find_spec(module):
    """只定位不导入 —— 确认"装没装"，实测 22 个模块合计 0.007s

    返回 `(是否通过, 证据, 耗时秒)`。

    ⚠️ 它**不执行模块体**，因此抓不到"装了但导入就炸"。用它的前提是
    调用方会把这个事实说出来（见 DETAIL_INSTALLED_ONLY 与第 3 步的收尾说明）。
    """
    started = time.perf_counter()
    try:
        spec = importlib.util.find_spec(module)
    except (ImportError, ValueError) as e:
        return False, f"未安装（{e}）", time.perf_counter() - started
    cost = time.perf_counter() - started
    if spec is None:
        return False, "未安装", cost
    return True, DETAIL_INSTALLED_ONLY, cost


def _pick_probe(module, deep):
    """选探测方式：只有"昂贵 且 不在启动闭包内"的模块才降级为 find_spec"""
    if deep or module not in HEAVY_MODULES:
        return _probe_by_import
    return _probe_by_find_spec


def _probe_modules(modules, deep=False, max_workers=None):
    """**并行**探测一组模块，返回 `[(模块, 是否通过, 证据, 耗时), ...]`，**保持入参顺序**

    ## 为什么是线程池，而不是进程池
    线程池实测只能把 28.5s 压到 25.4s（-11%），因为 CPython 的 GIL：
    import 的耗时大头是模块体的字节码执行（注册表构建、装饰器、类型构造），
    这部分不释放 GIL，无法并行；只有读文件 / dlopen 能重叠。

    进程池能真并行，但下界是**最慢的那一支**（qwen_asr 独立冷启 26.6s），
    再加 Windows spawn 开销与每进程一份 torch 内存（数百 MB~GB）—— 收益为负，
    因此不用。真正的大头靠分层解决（见 HEAVY_MODULES），线程是第二层优化。

    ## 顺序与线程安全
    `Executor.map` 按入参顺序产出，且结果只在**主线程**打印 ——
    输出与改造前逐行对应，`ok()/fail()` 的计数也不需要在子线程里加锁。

    ## 失败项必须**串行复验**（不是可选项，是正确性要求）
    并行导入会撞上"半初始化模块"：`alembic` 在导入 `sqlalchemy` 的子模块时，
    对方自己的 `import` 可能还没跑完，于是抛
        cannot import name 'Column' from partially initialized module
        'sqlalchemy.sql.schema' (most likely due to a circular import)
    ——**这是并行制造的假失败**。不是理论风险：独立子进程里两线程并发导入
    `sqlalchemy` + `alembic`，实测 **8/8 全中**（复现方式与断言见
    `backend/tests/test_check_env_probe.py`）。
    因此：池子关掉（所有导入落定）之后，**每一个失败项都单独再导一次**；
    只有"串行也失败"才算真失败。判定标准不能因为跑得快就放松。
    """
    modules = list(modules)
    if not modules:
        return []
    if max_workers is None:
        max_workers = min(8, len(modules), os.cpu_count() or 4)

    def _run(module):
        passed, detail, cost = _pick_probe(module, deep)(module)
        return module, passed, detail, cost

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        results = list(executor.map(_run, modules))

    # 串行复验失败项（此时池已关闭，所有并发导入都已落定）
    verified = []
    for module, passed, detail, cost in results:
        if not passed:
            if module not in sys.modules:
                # 模块体执行失败时，Python 会把**它自己**从 sys.modules 摘掉，
                # 但**子模块会留下**。不清掉它们，复验就会继承同一片废墟：
                # 实测（清 vs 不清，独立子进程对照）——
                #   不清：`import sqlalchemy` 报"成功"，随后
                #         `from sqlalchemy.orm import ...` 才炸出
                #         `module 'sqlalchemy.sql' has no attribute 'base'`
                #         —— 这是**晚到的假绿**，比假失败更坏。
                #   清掉：复验成功且 `event.listen(engine, "connect")` 正常。
                for name in [n for n in list(sys.modules) if n.startswith(module + ".")]:
                    sys.modules.pop(name, None)
            # 反之：模块**已经**在 sys.modules 里 ⇒ 别的线程已经把它导完了，
            # 此时绝不能清 —— 清了会让已经持有它的模块类身份分裂
            # （症状：`No such event 'connect' for target 'Engine(...)'`）。
            # 直接重新 import 会拿到那份已完成的缓存副本，判定同样可信。
            passed, detail, retry_cost = _pick_probe(module, deep)(module)
            cost += retry_cost
            if not passed:
                detail = f"{detail}（已串行复验）"
        verified.append((module, passed, detail, cost))
    return verified


def check_backend_deps(auto_fix=False, deep=False):
    header("步骤 3/10：检测后端 Python 依赖")

    # 必需 + 可选一次探测完（去重且保序：输出仍按"必需在前、可选在后"排列）
    modules = list(dict.fromkeys([*REQUIRED_PACKAGES, *OPTIONAL_PACKAGES]))
    started = time.perf_counter()
    probes = _probe_modules(modules, deep=deep)
    elapsed = time.perf_counter() - started
    by_module = {module: (passed, detail) for module, passed, detail, _ in probes}

    missing = []
    for module, package in REQUIRED_PACKAGES.items():
        passed, detail = by_module[module]
        if passed:
            ok(f"{package}（仅确认已安装）" if detail == DETAIL_INSTALLED_ONLY else f"{package}")
        else:
            fail(f"{package} {detail}")
            missing.append(package)

    # 说清"这次到底验证了什么" —— 不把"仅确认已安装"和"真实导入通过"混成一样的一行
    downgraded = [m for m in modules if not deep and m in HEAVY_MODULES]
    if deep:
        info(f"验证方式：{len(modules)} 个模块全部真实导入（并行，{elapsed:.1f}s）")
    elif downgraded:
        info(
            f"验证方式：{len(modules)} 个模块确认已安装"
            f"（其中 {len(modules) - len(downgraded)} 个真实导入完成，并行共 {elapsed:.1f}s）"
        )
        info(f"仅确认已安装（未做真实导入）：{'、'.join(downgraded)}")
        info("这些模块启动路径不会导入；要验证它们能否真正导入：python check_env.py --check --deep")

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
        passed, detail = by_module[module]
        if passed:
            ok(f"{package}（{desc}）" if detail != DETAIL_INSTALLED_ONLY else f"{package}（{desc}，仅确认已安装）")
        else:
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
        # 与版本探测同样的理由：POSIX 下 `shell=True` 会把参数吞成 `$0`，
        # 命令实际执行的是"只带第一个参数"的形态。改为显式解释器/解释器名 + shell=False。
        npm_cmd = "npm.cmd" if os.name == "nt" else "npm"
        cmd = [npm_cmd, "install", "--registry=https://registry.npmmirror.com"]
        try:
            subprocess.run(cmd, cwd=str(FRONTEND_DIR), check=True, shell=False)
            ok("前端依赖安装完成")
            record_fixed()
            return True
        except subprocess.CalledProcessError as e:
            fail(f"前端依赖安装失败：{e}")
            info("请手动安装：cd frontend && npm install")
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
def check_silero_vad(auto_fix=False, deep=False):
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
    #
    # ⚠️ 这段要 `import torch`（实测 2.8s），所以快速模式下跳过 —— 但**必须说出来**：
    # "项目目录里没有这个文件"不等于"模型没下载"，它可能躺在 torch.hub 缓存里。
    # 把没查过的事说成"未下载"就是**假失败**，和假通过一样不能接受。
    if deep:
        try:
            import torch
            hub_jit = Path(torch.hub.get_dir()) / "snakers4_silero-vad_master" / "src" / "silero_vad" / "data" / "silero_vad.jit"
            if hub_jit.exists():
                ok(f"Silero VAD 已在 torch.hub 缓存：{hub_jit}")
                record_pass()
                return True
        except Exception:
            pass
    else:
        info("torch.hub 缓存本次未检查（需 import torch，约 2.8s）；加 --deep 会检查")

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
def _read_env_key(key, default=""):
    """从 backend/.env 读一个键（找不到返回 default）"""
    env_file = BACKEND_DIR / ".env"
    if not env_file.exists():
        return default
    for line in env_file.read_text(encoding="utf-8").split("\n"):
        line = line.strip()
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    return default


def _find_mineru_kit():
    """
    定位 `mineru-kit`（MinerU 4.x 的命令行入口）；找不到返回 None。

    与 `backend/app/services/mineru/model_loader.py:find_mineru_cli` 同口径
    （check_env.py 是独立脚本，不 import app，所以这里保留一份等价实现）。
    优先找**当前解释器同级的 Scripts** —— pip 装完常打警告说该目录不在 PATH 上。
    """
    explicit = (os.environ.get("MINERU_CLI") or _read_env_key("MINERU_CLI")).strip()
    if explicit and Path(explicit).exists():
        return explicit
    exe_dir = Path(sys.executable).resolve().parent
    for candidate in (
        exe_dir / "Scripts" / "mineru-kit.exe",
        exe_dir / "mineru-kit",
        exe_dir.parent / "bin" / "mineru-kit",
    ):
        if candidate.exists():
            return str(candidate)
    for name in ("mineru-kit", "mineru-kit.exe"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _mineru_tier():
    """档位：环境变量 > .env > 默认 standard"""
    return (os.environ.get("MINERU_TIER") or _read_env_key("MINERU_TIER") or "standard").strip()


def _run_mineru_kit(args, timeout=1800):
    """跑一条 mineru-kit 命令，返回 (returncode, stdout+stderr)"""
    cli = _find_mineru_kit()
    if not cli:
        return None, "找不到 mineru-kit"
    env = os.environ.copy()
    home = (os.environ.get("MINERU_HOME") or _read_env_key("MINERU_HOME")).strip()
    if home:
        env["MINERU_HOME"] = home
    try:
        proc = subprocess.run(
            [cli, *args], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, env=env,
            check=False,
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


def check_mineru_models(auto_fix=False, force_download=False):
    header("步骤 8/10：检测 MinerU 4.x 本地模型（可选，PDF 本地解析用）")

    # 判断是否使用云端 API 模式
    has_token = bool(_read_env_key("MINERU_API_TOKEN"))
    backend_mode = _read_env_key("MINERU_BACKEND")
    if has_token and backend_mode in ("vlm-http-client", "hybrid-http-client"):
        ok("已配置 Mineru 云端 API（vlm-http-client 模式）")
        info("使用云端 API 解析 PDF，无需下载本地 MinerU 模型")
        record_pass()
        return True

    tier = _mineru_tier()
    cli = _find_mineru_kit()

    # 2026-10-21：MinerU 3.x → 4.x。判定方式从"嗅探 ModelScope 目录"
    # 改为"问 4.x 自己"（`mineru-kit models verify`）—— 后者是官方权威校验，
    # 且知道 $MINERU_HOME/models 与档位（basic/standard）的组合关系。
    if not cli:
        fail("未安装 MinerU 4.x（找不到 mineru-kit）")
        info('安装：pip install -r requirements-mineru.txt   # 即 pip install "mineru>=4.0,<5"')
        info("若只走云端：在 .env 配置 MINERU_API_TOKEN 与 MINERU_BACKEND=vlm-http-client")
        record_fail()
        return False

    info(f"mineru-kit: {cli}")
    info(f"解析档位  : {tier}")

    rc, output = _run_mineru_kit(["models", "verify", "--tier", tier], timeout=300)
    if rc == 0:
        for line in output.strip().splitlines():
            if line.strip():
                ok(line.strip())
        record_pass()
        return True

    fail(f"档位 {tier} 的本地模型未就绪")
    for line in output.strip().splitlines()[-6:]:
        if line.strip():
            info(f"  {line.strip()}")

    if not force_download and auto_fix:
        warn("MinerU 模型约 2–4GB，--fix 模式下不自动下载")
        info("如需下载：python check_env.py --download-mineru")
        info(f"或：mineru-kit models download --tier {tier} --source modelscope")
        record_warn()
        return False

    if force_download:
        info(f"正在下载档位 {tier} 的模型（约 2–4GB，请耐心等待）...")
        rc, output = _run_mineru_kit(
            ["models", "download", "--tier", tier, "--source", "modelscope"]
        )
        if rc == 0:
            ok("MinerU 模型下载完成")
            rc2, out2 = _run_mineru_kit(["models", "verify", "--tier", tier], timeout=300)
            if rc2 == 0:
                ok("校验通过")
                record_fixed()
                return True
            fail("下载后校验未通过")
            for line in out2.strip().splitlines()[-5:]:
                info(f"  {line.strip()}")
            record_fail()
            return False
        fail("MinerU 模型下载失败")
        for line in output.strip().splitlines()[-8:]:
            info(f"  {line.strip()}")
        record_fail()
        return False

    warn("MinerU 模型未下载（PDF 本地解析功能不可用）")
    info("下载命令：python check_env.py --download-mineru")
    info("或使用云端 API：在 .env 中配置 MINERU_API_TOKEN")
    record_warn()
    return False


# ============================================================
# 9. 检测并下载 Qwen3-ASR 模型（可选，音视频转写用）
# ============================================================
def check_qwen_asr(auto_fix=False, force_download=False, deep=False):
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
    #
    # 这里只需要"装没装"（用来决定要不要 pip install），不需要真实导入 ——
    # 而真实导入实测 6.2s（独立冷启 26.6s），且第 3 步刚刚验过一次。
    _available, _detail, _cost = _pick_probe("qwen_asr", deep)("qwen_asr")
    if not _available:
        warn(f"qwen_asr 包不可用（{_detail}）")
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
    print(f"  {Color.CYAN}本次检测耗时：{time.perf_counter() - _STARTED_AT:.1f}s{Color.END}")
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

    # 根据检测结果设置退出码：有失败项时 exit 1，让启动脚本能感知
    if results["fail"] > 0:
        sys.exit(1)
    # warnings 不阻止启动，exit 0

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
    # 依赖检测深度：--deep 显式要求全量真实导入；--fix 也一律深度检测 ——
    # 装完不真导一次，就等于把"假绿"写进修复流程本身（见 HEAVY_MODULES 的说明）。
    deep = "--deep" in sys.argv or auto_fix
    global _STARTED_AT
    _STARTED_AT = time.perf_counter()

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
        if deep:
            info("已启用 --deep 模式：所有依赖真实导入（最严格，也更慢）")

    # 简化：只有 --fix 时才修复（默认无参数 = 仅检测）
    fix = auto_fix

    # 执行检测
    check_python()
    check_node()
    check_backend_deps(fix, deep=deep)
    check_frontend_deps(fix)
    check_env_file(fix)
    check_bge_m3(fix)
    check_silero_vad(fix, deep=deep)
    check_mineru_models(fix, force_download=download_all)
    check_qwen_asr(fix, force_download=download_all, deep=deep)
    create_data_dirs()

    print_summary()

if __name__ == "__main__":
    main()
