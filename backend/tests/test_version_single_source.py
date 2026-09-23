"""版本号必须只有一个来源（阶段 7.3）

## 这条守卫防什么

版本号此前硬编码在**两处**（`backend/app/main.py` 的 `FastAPI(version=...)`
与 `frontend/package.json`），彼此没有任何约束。后果不是"数字不好看"，而是
**没有任何机制能发现它们漂移了** —— 于是 `/openapi.json` 的 `info.version`、
后端日志、前端包元数据可能各说各话，而"我装的是哪个版本"就没有唯一答案。

现在后端只有 `backend/app/version.py` 一处常量，本文件把它与另外两处
（`pyproject.toml`、`frontend/package.json`）钉在一起。
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VERSION_PY = REPO_ROOT / "backend" / "app" / "version.py"


def _version_from_python() -> str:
    match = re.search(r'(?m)^__version__\s*=\s*"([^"]+)"', VERSION_PY.read_text(encoding="utf-8"))
    assert match, "backend/app/version.py 里找不到 __version__"
    return match.group(1)


class TestSingleVersionSource:
    def test_version_module_exists(self):
        assert VERSION_PY.is_file(), "缺少单一版本来源 app/version.py"

    def test_main_does_not_hardcode_a_version(self):
        """`main.py` 不得再写死版本字符串"""
        source = (REPO_ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")
        assert "version=__version__" in source, "main.py 没有引用 __version__"
        assert not re.search(r'version="\d+\.\d+\.\d+"', source), (
            "main.py 里又出现了写死的版本号 —— 版本必须来自 app/version.py"
        )

    def test_pyproject_matches(self):
        text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
        assert match, "pyproject.toml 缺 version"
        assert match.group(1) == _version_from_python(), (
            f"pyproject.toml ({match.group(1)}) 与 app/version.py "
            f"({_version_from_python()}) 不一致"
        )

    def test_frontend_package_matches(self):
        text = (REPO_ROOT / "frontend" / "package.json").read_text(encoding="utf-8")
        match = re.search(r'"version"\s*:\s*"([^"]+)"', text)
        assert match, "frontend/package.json 缺 version"
        assert match.group(1) == _version_from_python(), (
            f"frontend/package.json ({match.group(1)}) 与 app/version.py "
            f"({_version_from_python()}) 不一致"
        )

    def test_openapi_reports_the_same_version(self):
        """`/openapi.json` 的 info.version 必须同源（前端类型的来源就是它）"""
        from app.main import app

        assert app.version == _version_from_python()
