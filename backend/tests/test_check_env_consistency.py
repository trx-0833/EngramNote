"""`check_env.py` 的依赖清单必须与 `requirements.txt` 一致（阶段 5.7 的可执行判据）

## 这个守卫抓到过什么

`check_env.py` 的 `REQUIRED_PACKAGES` 里曾长期留着 **`chromadb`** ——
而该依赖在阶段 2.4 就已移除（向量改存 `chunks` 表、词法检索改 FTS5）。
后果不是"多报一个错"，而是两件相反的事同时发生：

1. **假失败**：真实用户装完合规依赖后，自检仍报 `chromadb 未安装`，
   而 `--fix` 甚至会**把已废弃的依赖装回来**；
2. **假通过**：同一份清单里**没有** `pypdfium2` / `Pillow` / `psutil`，
   而它们在 `app/services/mineru/intake.py:22-23` 是模块顶层无条件 import ——
   于是"自检全绿"的机器上，PDF 上传路径直接 ModuleNotFoundError。

清单描述了一个不存在的架构，两个方向都错。因此把它做成断言：
**自检说我该装什么，就必须等于 requirements.txt 说我该装什么。**
"""

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECK_ENV = REPO_ROOT / "check_env.py"
REQUIREMENTS = REPO_ROOT / "backend" / "requirements.txt"


#: 运行依赖里**不需要**进入 check_env 自检的包 → 理由。
#: 每条都要写清"为什么不做检查"，否则豁免会变成"让测试变绿"的后门。
CLASSIFICATION_EXEMPTIONS: dict[str, str] = {
    "email-validator": (
        "pydantic 的 EmailStr 校验后端，由 pydantic 在需要时通过 entry point 加载；"
        "它没有 Python 模块级用法，无法（也不需要）用 importlib 探测。"
    ),
}

#: 存在"两个名字"的包：requirements 里叫 A，自检里用模块名 B
NAME_ALIASES: dict[str, str] = {
    "pyyaml": "yaml",     # PyYAML 的模块名是 yaml
}


def _extract_required_packages() -> dict[str, str]:
    """从 check_env.py 里取出 `REQUIRED_PACKAGES = {模块: 包名}`（用 AST，不执行文件）"""
    tree = ast.parse(CHECK_ENV.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if "REQUIRED_PACKAGES" in targets:
                return ast.literal_eval(node.value)
    raise AssertionError("check_env.py 里找不到 REQUIRED_PACKAGES")


def _normalise(name: str) -> str:
    return re.split(r"[<>=!~\[]", name.strip())[0].strip().lower().replace("_", "-")


def _optional_mode_packages() -> set[str]:
    """可选模式清单（本地 PDF 解析 / ASR）里声明的包"""
    names: set[str] = set()
    for filename in ("requirements-pdf-local.txt", "requirements-asr.txt"):
        path = REQUIREMENTS.parent / filename
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and not line.startswith("-"):
                names.add(_normalise(line))
    return names


def _requirements_packages() -> set[str]:
    names = set()
    for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        names.add(_normalise(line))
    return names


class TestCheckEnvMatchesRequirements:
    def test_no_dependency_that_requirements_does_not_declare(self):
        """自检不得要求 requirements.txt 里没有的包（chromadb 就是这样留下的）

        两张清单的**名字空间不同**：自检用 import 名（`yaml`），
        requirements 用发行名（`pyyaml`）。同类差异还有从 optional 清单来的包
        （`pyyaml` 属本地 PDF 模式，见 requirements-pdf-local.txt）——
        因此这里按"发行名归一 + 别名 + 可选清单"三件事一起判。
        """
        required = {
            NAME_ALIASES.get(_normalise(pkg), _normalise(pkg))
            for pkg in _extract_required_packages().values()
        }
        declared = _requirements_packages()
        declared |= _optional_mode_packages()
        declared = {NAME_ALIASES.get(n, n) for n in declared}
        ghosts = sorted(required - declared)
        assert not ghosts, (
            f"check_env.py 要求了 requirements.txt 未声明的包：{ghosts}\n"
            "若该依赖已被移除（例如 chromadb），请从 REQUIRED_PACKAGES 删除 —— "
            "否则真实用户会看到'假失败'，`--fix` 还会把它装回来。"
        )

    def test_every_requirements_package_is_checked(self):
        """反方向：requirements.txt 里的运行依赖必须都被自检覆盖

        否则会出现"自检全绿但服务跑不起来"（pypdfium2 / Pillow / psutil 曾如此）。
        例外只有两类：重量级可选依赖（有独立下载步骤）与仅本地 PDF 模式需要的。
        """
        required = {_normalise(pkg) for pkg in _extract_required_packages().values()}
        declared = _requirements_packages()
        # 这些有专门的检测/下载步骤，因此不进 REQUIRED_PACKAGES
        exemptions = {"torch", "qwen-asr"} | set(CLASSIFICATION_EXEMPTIONS)
        required = {NAME_ALIASES.get(n, n) for n in required}
        uncovered = sorted(declared - required - exemptions)
        assert not uncovered, (
            f"requirements.txt 里这些运行依赖没有进入自检清单：{uncovered}\n"
            "缺一个就会出现'自检全绿，用户却跑不起来'。"
        )

    def test_removed_dependency_stays_removed(self):
        """chromadb 已废弃：两个地方都不得再要求它（防回潮）"""
        assert "chromadb" not in CHECK_ENV.read_text(encoding="utf-8").split(
            "REQUIRED_PACKAGES"
        )[1][:400].split("}")[0], "check_env.py 又在要求 chromadb 了"
        assert "chromadb" not in {
            _normalise(line) for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        }, "requirements.txt 里又出现了 chromadb"

    def test_npm_probe_does_not_use_shell_true(self):
        """`shell=True` 在 POSIX 上会让 npm 检查静默假通过（打印用法并记 pass）"""
        # 用 AST 判定"真实调用"，而不是在源码里搜字符串 ——
        # 否则解释这件事的注释本身就会让断言失败（第一次写这个守卫时就踩了）
        offenders = []
        for node in ast.walk(ast.parse(CHECK_ENV.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if kw.arg == "shell" and getattr(kw.value, "value", None) is True:
                        offenders.append(node.lineno)
        assert not offenders, (
            f"check_env.py 第 {offenders} 行又用回了 shell=True —— POSIX 下 "
            "`/bin/sh -c` 只把 argv[0] 当命令串，`--version` 会变成 $0，"
            "npm 打印用法并退出 0，于是检查'通过'了但什么都没验证。"
        )
        assert "npm.cmd" in CHECK_ENV.read_text(encoding="utf-8"), (
            "Windows 上 npm 是 .cmd，应当显式用 npm.cmd + shell=False"
        )
