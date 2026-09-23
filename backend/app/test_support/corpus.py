"""真实语料定位（测试与手动脚本共用的单一出口）

## 为什么需要它（阶段 1.5，2026-09-23）

改造前，16 个已跟踪文件把**真实个人文件名与真实姓名**写成默认值：

    PDF_PATH = os.environ.get("TEST_PDF_PATH", r"D:\\...\\劳动合同书-<真实姓名>.pdf")

这类默认值会被 GitHub 代码搜索与搜索引擎命中，等于把用户资料公开。
现在默认值只剩中性名，真实语料一律由使用者通过环境变量提供：

    TEST_PDF_PATH=/path/to/your.pdf pytest tests/test_full_flow.py

## 为什么放在 `app/` 里而不是 `tests/`

`tests/` **不是**包（无 `__init__.py`，且 pytest 只把 rootdir `backend/` 放进
`sys.path`），所以：
  - 顶层用例 `from conftest import ...` → `ModuleNotFoundError: conftest`
  - `from _pdf_corpus import ...` → 同样失败

放进 `app.test_support` 后，两条路径都能用同一次导入：
  - pytest 用例：`from app.test_support.corpus import real_pdf_path`（rootdir 在 path 上）
  - `backend/scripts/dev/*.py` 手动脚本：`backend/` 在 path 上（脚本自己插入）

它不含任何测试框架依赖，因此放进 app 包不会把 pytest 带进生产依赖。
"""

from __future__ import annotations

import os

#: 真实语料路径的环境变量名（仓库内不得出现任何具体默认路径）
TEST_PDF_ENV = "TEST_PDF_PATH"

#: 中性占位名：使用者把它放进 `tests/` 或项目根即可被自动发现
DEFAULT_PDF_NAME = "sample.pdf"

#: 自动发现中性占位文件的位置（相对本文件：backend/app/test_support/ → 仓库根）
_SEARCH_DIRS = (
    os.path.dirname(os.path.abspath(__file__)),
    os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tests")),
    os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")),
)


def real_pdf_path() -> str | None:
    """真实 PDF 语料路径；未配置且无中性占位文件时返回 None

    刻意**不**回退到任何"看起来像真的"路径 —— 那正是本次要根除的形态。
    """
    configured = os.environ.get(TEST_PDF_ENV, "").strip()
    if configured:
        return configured
    for directory in _SEARCH_DIRS:
        candidate = os.path.join(directory, DEFAULT_PDF_NAME)
        if os.path.exists(candidate):
            return candidate
    return None


def require_pdf_path() -> str:
    """给"手动运行的脚本"用：拿不到语料就**响亮失败**

    这些脚本不是 pytest 用例，不适合 skip；缺语料时必须立刻报错，
    而不是拿着一个不存在的默认路径一路跑到最后才失败。
    """
    path = real_pdf_path()
    if not path:
        raise RuntimeError(
            f"缺少真实 PDF 语料：请设置 {TEST_PDF_ENV}=<你的文件路径> 后再运行本脚本"
        )
    return path
