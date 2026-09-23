"""打包元数据守卫（阶段 2.2）：`pyproject.toml` 必须与 `requirements.txt` 一致

## 为什么需要

`pyproject.toml` 让后端**可安装**（`pip install -e backend`），但它同时引入了
第二份依赖清单。同一个事实写两遍，必然漂移 —— 本仓库对这件事的代价吃过很多次亏
（CI 里手抄的依赖清单漏掉 numpy，导致 64 failed）。

因此把它做成断言：
1. 两份清单的**包名集合**必须相同（版本约束可以表述不同，包不能少也不能多）；
2. `requires-python` 必须与 `check_env.py` / README 声明一致（3.10+）；
3. 可选依赖必须覆盖 `requirements-asr.txt` / `requirements-pdf-local.txt` 的包。
"""

import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND.parent


def _normalise(name: str) -> str:
    """把 `uvicorn[standard]~=0.30.0` 归一成 `uvicorn`"""
    name = name.split("#", 1)[0].strip()
    name = re.split(r"[<>=!~\[]", name)[0]
    return name.strip().lower().replace("_", "-")


def requirements_packages(filename: str) -> set[str]:
    """读 requirements*.txt 的**生效行**（忽略注释与空行）"""
    names = set()
    for line in (BACKEND / filename).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        normalised = _normalise(line)
        if normalised:
            names.add(normalised)
    return names


def pyproject_text() -> str:
    return (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")


def _extract_list(text: str, header: str) -> set[str]:
    """取出 `header = [ ... ]` 里的包名（支持跨行，且容忍包名里的方括号）

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
                body = text[start + 1:index]
                break
    else:  # pragma: no cover - 畸形文件
        raise AssertionError(f"pyproject.toml 里 {header} 的方括号不配对")
    return {_normalise(i) for i in re.findall(r'"([^"]+)"', body)}


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

    def test_optional_groups_cover_the_split_files(self):
        """extras 必须覆盖被拆出去的两份可选清单"""
        text = pyproject_text()
        asr = _extract_list(text, "asr")
        pdf_local = _extract_list(text, "pdf-local")
        dev = _extract_list(text, "dev")
        assert {"torch"} <= asr, f"asr extras 缺 torch：{sorted(asr)}"
        assert {"pyyaml"} <= pdf_local, f"pdf-local extras 缺 pyyaml：{sorted(pdf_local)}"
        assert {"pytest"} <= dev, f"dev extras 缺 pytest：{sorted(dev)}"

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
