"""打包元数据守卫（阶段 2.2）：`pyproject.toml` 必须与 `requirements.txt` 一致

## 为什么需要

`pyproject.toml` 让后端**可安装**（`pip install -e backend`），但它同时引入了
第二份依赖清单。同一个事实写两遍，必然漂移 —— 本仓库对这件事的代价吃过很多次亏
（CI 里手抄的依赖清单漏掉 numpy，导致 64 failed）。

因此把它做成断言：
1. 两份清单的**包名集合**必须相同（包不能少也不能多）；
2. 两份清单的**版本约束**必须相同（见下）；
3. `requires-python` 必须与 `check_env.py` / README 声明一致（3.10+）；
4. 可选依赖必须覆盖 `requirements-asr.txt` / `requirements-mineru.txt` 的包。

## 2026-10-21：补上"版本约束"这一维

本文件此前只比**包名**，并显式写着"版本约束可以表述不同"。那条豁免过于宽松：
实测 `pyproject.toml` 已漂移 **7 个包**，其中

    python-jose   pyproject `~=3.3.0`   requirements `~=3.5.0`

而 `requirements.txt` 里有一段专门注释说明 3.3.x 命中
CVE-2024-33663 / CVE-2024-33664、已升到 3.5 —— 也就是说**清单的另一个副本
还在声明那个带 CVE 的区间**，而没有任何东西会因此变红。
另有 `pypdfium2` / `openai` / `bcrypt` 三个大版本差、`httpx` / `sqlalchemy` /
`python-multipart` 三个小版本差。

"版本约束可以表述不同"在**同一个仓库的两份清单**之间没有正当理由 ——
它们服务的是同一批用户、同一个解释器。所以现在逐条比。
真需要差异时，往 `VERSION_EXEMPTIONS` 里加一条并写明理由。
"""

import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND.parent

#: 允许 `pyproject.toml` 与 `requirements.txt` 版本约束不同的包 → 理由。
#: 每条都要写清"为什么可以不一样"，否则豁免会变成"让测试变绿"的后门。
#: 当前为空：两份清单服务同一批用户、同一个解释器，没有正当差异。
VERSION_EXEMPTIONS: dict[str, str] = {}


def _normalise(name: str) -> str:
    """把 `uvicorn[standard]~=0.30.0` 归一成 `uvicorn`"""
    name = name.split("#", 1)[0].strip()
    name = re.split(r"[<>=!~\[]", name)[0]
    return name.strip().lower().replace("_", "-")


def _split_spec(entry: str) -> tuple[str, str]:
    """
    `uvicorn[standard]~=0.30.0` → `("uvicorn", "~=0.30.0")`

    版本串里**去掉所有空格**再比：`>=4.0,<5` 与 `>=4.0, <5` 是同一个约束，
    不该因为排版差异变红。
    """
    entry = entry.split("#", 1)[0].strip()
    match = re.match(r"^([A-Za-z0-9_.\-]+)\s*(\[[^\]]*\])?\s*(.*)$", entry)
    if not match:
        return "", ""
    name = match.group(1).strip().lower().replace("_", "-")
    spec = (match.group(3) or "").strip().replace(" ", "")
    return name, spec


def requirements_packages(filename: str) -> set[str]:
    """读 requirements*.txt 的**生效行**（忽略注释与空行），返回包名集合"""
    return set(requirements_specs(filename))


def requirements_specs(filename: str) -> dict[str, str]:
    """读 requirements*.txt 的**生效行**，返回 `{包名: 版本约束}`"""
    specs: dict[str, str] = {}
    path = BACKEND / filename
    if not path.exists():
        return specs
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        name, spec = _split_spec(line)
        if name:
            specs[name] = spec
    return specs


def pyproject_text() -> str:
    return (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")


def _extract_body(text: str, header: str) -> str:
    """取出 `header = [ ... ]` 的方括号内容（跨行，且容忍包名里的方括号）

    注意：不能用 `\\[(.*?)\\]` 这种非贪婪匹配 —— 依赖字符串里本身带方括号
    （`uvicorn[standard]~=0.30.0`），非贪婪会在第一个 `]` 就收尾，把剩下的
    条目全丢掉。这里改成"从 `[` 开始按括号配对找到结尾"。
    """
    match = re.search(rf"(?m)^{re.escape(header)}\s*=\s*\[", text)
    assert match, f"pyproject.toml 里找不到 {header}"
    start = match.end() - 1  # 指向 '['
    depth = 0
    for index in range(start, len(text)):
        char = text[index]
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return text[start + 1:index]
    raise AssertionError(f"pyproject.toml 里 {header} 的方括号不配对")


def _extract_list(text: str, header: str) -> set[str]:
    """取出 `header = [ ... ]` 里的包名"""
    return {_normalise(i) for i in re.findall(r'"([^"]+)"', _extract_body(text, header))}


def _extract_specs(text: str, header: str) -> dict[str, str]:
    """取出 `header = [ ... ]` 里的 `{包名: 版本约束}`"""
    specs: dict[str, str] = {}
    for entry in re.findall(r'"([^"]+)"', _extract_body(text, header)):
        name, spec = _split_spec(entry)
        if name:
            specs[name] = spec
    return specs


class TestPyprojectMatchesRequirements:
    def test_pyproject_exists(self):
        assert (REPO_ROOT / "pyproject.toml").is_file(), (
            "后端仍不可安装（缺仓库根的 pyproject.toml）"
        )

    def test_base_dependencies_match_requirements_txt(self):
        """主依赖集合必须与 requirements.txt 完全一致（缺一个就是"装完跑不了"）"""
        declared = _extract_list(pyproject_text(), "dependencies")
        expected = requirements_packages("requirements.txt")
        missing = sorted(expected - declared)
        extra = sorted(declared - expected)
        assert not missing, (
            f"pyproject.toml 的 dependencies 少了 requirements.txt 里的包：{missing}"
        )
        assert not extra, (
            f"pyproject.toml 的 dependencies 多了 requirements.txt 里没有的包：{extra}\n"
            "（若确实需要，请同时登记 requirements.txt —— 用户读的是那一份）"
        )

    def test_base_dependency_versions_match_requirements_txt(self):
        """主依赖的**版本约束**也必须一致（2026-10-21 新增）

        只比包名会让"两个副本声明不同版本区间"完全隐身 ——
        实测漏掉了 7 个，其中一个还是带 CVE 的 `python-jose~=3.3.0`。
        """
        declared = _extract_specs(pyproject_text(), "dependencies")
        expected = requirements_specs("requirements.txt")

        mismatches = []
        for name in sorted(set(declared) & set(expected)):
            if name in VERSION_EXEMPTIONS:
                continue
            if declared[name] != expected[name]:
                mismatches.append(
                    f"{name}: pyproject {declared[name]!r} vs requirements {expected[name]!r}"
                )
        assert not mismatches, (
            "两份清单的版本约束不一致：\n  " + "\n  ".join(mismatches) + "\n"
            "同一个仓库的两份清单服务同一批用户，没有正当理由不同。\n"
            "确需差异时请往 VERSION_EXEMPTIONS 加一条并写明理由。"
        )

    def test_optional_groups_cover_the_split_files(self):
        """extras 必须覆盖被拆出去的可选清单

        2026-10-21：MinerU 由 3.x 升到 4.x，`pdf-local` extra 随之改名 `mineru`
        （3.x 的 `pyyaml` 等已由 4.x 基础包自带，见 requirements-mineru.txt）。
        """
        text = pyproject_text()
        asr = _extract_list(text, "asr")
        mineru = _extract_list(text, "mineru")
        dev = _extract_list(text, "dev")
        assert {"torch"} <= asr, f"asr extras 缺 torch：{sorted(asr)}"
        assert {"qwen-asr"} <= asr, f"asr extras 缺 qwen-asr：{sorted(asr)}"
        assert {"mineru"} <= mineru, f"mineru extras 缺 mineru：{sorted(mineru)}"
        assert {"pytest"} <= dev, f"dev extras 缺 pytest：{sorted(dev)}"

    def test_optional_extras_match_their_requirement_files(self):
        """extras 与对应的 requirements 清单**版本**也要一致"""
        text = pyproject_text()
        pairs = (
            ("asr", "requirements-asr.txt"),
            ("mineru", "requirements-mineru.txt"),
        )
        problems = []
        for header, filename in pairs:
            declared = _extract_specs(text, header)
            expected = requirements_specs(filename)
            for name in sorted(set(declared) & set(expected)):
                if name in VERSION_EXEMPTIONS:
                    continue
                if declared[name] != expected[name]:
                    problems.append(
                        f"[{header}] {name}: pyproject {declared[name]!r} "
                        f"vs {filename} {expected[name]!r}"
                    )
        assert not problems, "可选清单版本不一致：\n  " + "\n  ".join(problems)

    def test_requires_python_matches_the_documented_floor(self):
        """`requires-python` 必须与 README / check_env 的 3.10+ 一致"""
        match = re.search(r'(?m)^requires-python\s*=\s*"([^"]+)"', pyproject_text())
        assert match, "pyproject.toml 缺 requires-python"
        assert "3.10" in match.group(1), (
            f"requires-python 应为 >=3.10（README 与 check_env.py 都是 3.10+），"
            f"实际：{match.group(1)}"
        )
        check_env = (BACKEND.parent / "check_env.py").read_text(encoding="utf-8")
        assert "需要 3.10+" in check_env, "check_env.py 的 Python 下界声明变了，请同步"

    def test_no_test_only_package_leaked_into_runtime_deps(self):
        """pytest/ruff 不得出现在运行依赖里（用户不该为了跑服务装测试框架）"""
        declared = _extract_list(pyproject_text(), "dependencies")
        leaked = sorted(declared & {"pytest", "pytest-asyncio", "ruff"})
        assert not leaked, f"运行依赖里混入了测试/lint 工具：{leaked}"
