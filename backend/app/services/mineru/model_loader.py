"""
MinerU 4.x 本地后端就绪检查

## 2026-10-21：整体重写（MinerU 3.x → 4.x）

3.x 时代这个模块做的是"在 ModelScope 缓存里嗅探两个模型目录是否存在"，
并据此判断 pipeline 后端能不能跑。4.x 把这一切都变了：

- 模型不再是 `OpenDataLab/PDF-Extract-Kit-1.0` + `MinerU2.5-Pro-2604-1.2B`，
  而是 4.x 自己的仓库（`MinerU-4_models_onnx` / `MinerU-4_models_torch` /
  `MinerU2.5-Pro-2605-1.2B(-GGUF)`），统一放在 `$MINERU_HOME/models` 下；
- 档位（`basic` / `standard`）决定需要哪几个仓库；
- **4.x 自带权威校验命令** `mineru-kit models verify`。

所以这里不再自己嗅探目录 —— 直接问 4.x 自己。
这同时修掉了旧实现的两个毛病：
① "目录存在就返回 True"的**假就绪**；② 强制要求两份模型都在（只用一种也要背两份）。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import logging

logger = logging.getLogger(__name__)

#: `mineru-kit models verify` 的默认档位（与 config_loader.DEFAULT_CONFIG["tier"] 对齐）
DEFAULT_TIER = "standard"


def find_mineru_cli(config: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """
    定位 `mineru-kit` 可执行文件（MinerU 4.x 的无状态转换入口）。

    ## 为什么不能只靠 PATH

    实测：`pip install mineru` 把 `mineru-kit.exe` 装进 `<env>\\Scripts`，
    而该目录**不一定在 PATH 上**（pip 自己会打这条警告）。所以优先找
    **当前解释器同级的 Scripts**，而不是 `shutil.which`。

    优先级：配置 `cli_path` / 环境变量 `MINERU_CLI` → 同级 Scripts/bin → PATH。
    """
    config = config or {}
    explicit = str(config.get("cli_path") or os.environ.get("MINERU_CLI") or "").strip()
    if explicit and Path(explicit).exists():
        return explicit

    exe_dir = Path(sys.executable).resolve().parent
    for candidate in (
        exe_dir / "Scripts" / "mineru-kit.exe",   # Windows（conda/venv）
        exe_dir / "mineru-kit",                    # POSIX venv
        exe_dir.parent / "bin" / "mineru-kit",     # POSIX conda
    ):
        if candidate.exists():
            return str(candidate)

    for name in ("mineru-kit", "mineru-kit.exe"):
        found = shutil.which(name)
        if found:
            return found
    return None


def mineru_env(config: Dict[str, Any]) -> Dict[str, str]:
    """把项目配置翻译成 4.x 认的环境变量（只设非空、非 auto 的项）。"""
    env = os.environ.copy()
    for env_key, cfg_key in (
        ("MINERU_HOME", "mineru_home"),
        ("MINERU_MODEL_SOURCE", "model_source"),
    ):
        value = str(config.get(cfg_key) or "").strip()
        if value:
            env[env_key] = value
    for env_key, cfg_key in (
        ("MINERU_MODEL_SMALL_BACKEND", "small_backend"),
        ("MINERU_MODEL_VLM_ENGINE", "vlm_engine"),
    ):
        value = str(config.get(cfg_key) or "").strip()
        if value and value != "auto":
            env[env_key] = value
    return env


def local_backend_ready(
    config: Dict[str, Any], tier: Optional[str] = None
) -> Tuple[bool, str]:
    """
    检查本地 MinerU 4.x 后端是否真的可用。

    两步：① `mineru-kit` 在不在；② `mineru-kit models verify` 过不过。
    后者是 4.x 的权威校验（按当前生效的档位与后端组合核对本地资源）。

    Args:
        config: 配置字典（含 tier / small_backend / vlm_engine / mineru_home）
        tier: 覆盖配置里的档位；None 则用 `config["tier"]`，再退回 DEFAULT_TIER

    Returns:
        (是否就绪, 不可用原因)；就绪时原因为空串
    """
    cli = find_mineru_cli(config)
    if not cli:
        return False, (
            '找不到 mineru-kit。请安装 MinerU 4.x：pip install "mineru>=4.0,<5"'
            "（见 backend/requirements-mineru.txt），或用 MINERU_CLI 指定路径"
        )

    use_tier = str(tier or config.get("tier") or DEFAULT_TIER)
    argv = [cli, "models", "verify", "--tier", use_tier]
    small = str(config.get("small_backend") or "").strip()
    if small and small != "auto":
        argv += ["--small-backend", small]
    engine = str(config.get("vlm_engine") or "").strip()
    if engine and engine != "auto":
        argv += ["--vlm-engine", engine]

    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            env=mineru_env(config),
        )
    except Exception as exc:  # noqa: BLE001
        return False, f"调用 mineru-kit models verify 失败: {exc}"

    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        detail = " / ".join(line.strip() for line in tail[-3:] if line.strip())
        return False, (
            f"档位 {use_tier} 的本地模型未就绪"
            + (f"（{detail}）" if detail else "")
            + f"。补齐：mineru-kit models download --tier {use_tier} --source modelscope"
        )
    return True, ""


def verify_local_backend(config: Dict[str, Any]) -> Dict[str, str]:
    """
    就绪状态的**明细**（供自检 / 诊断输出用）。

    Returns:
        {"status": "ok" | 原因, "tier": 档位, "cli": 路径}
    """
    cli = find_mineru_cli(config) or ""
    tier = str(config.get("tier") or DEFAULT_TIER)
    ready, reason = local_backend_ready(config, tier=tier)
    return {
        "status": "ok" if ready else reason,
        "tier": tier,
        "cli": cli,
    }
