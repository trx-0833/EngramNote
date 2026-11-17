"""转换超时预算必须**夹到任务上限之内**，且常量必须在模块顶层可 import。

## 为什么要这些用例（二次审计抓到的 blocker）

我把 `LONG_TASK_SOFT_TIME_LIMIT` 的 import 写在 `_compute_timeout` **函数体内**，
而且层级写错了一个点（`..tasks` = `app.services.tasks`）。后果：

- 运行时 `ModuleNotFoundError` ⇒ **任何 PDF（本地或云端）都转不过去**；
- 而 2008 条用例、`p_import_all` 全绿 —— 因为
  ① import 在函数体里，import 期不报；
  ② 没有用例用**真实页数**调过它（假 PDF 读不出页数 → 走早退分支）。

所以这里同时守住**位置**（常量必须模块级）和**数值**（夹紧逻辑）。
"""

from __future__ import annotations

from pathlib import Path

from app.services.mineru import converter
from app.tasks.celery_app import LONG_TASK_SOFT_TIME_LIMIT


class TestConstantIsModuleLevel:
    def test_importable_from_module(self):
        """常量必须在模块顶层可取 —— 写在函数体里的话层级写错要到运行时才炸"""
        assert hasattr(converter, "LONG_TASK_SOFT_TIME_LIMIT")
        assert converter.LONG_TASK_SOFT_TIME_LIMIT == LONG_TASK_SOFT_TIME_LIMIT

    def test_compute_timeout_module_import_path_is_valid(self):
        """`_compute_timeout` 内部不许再出现函数内 import 该常量的写法"""
        src = Path("app/services/mineru/converter.py").read_text(encoding="utf-8")
        start = src.index("def _compute_timeout")
        body = src[start : start + 2000]
        assert "import LONG_TASK_SOFT_TIME_LIMIT" not in body, (
            "常量 import 又被写进函数体：层级写错时 import 期不会报错，只有真实 PDF 才会炸"
        )


class TestTimeoutBudgetIsClamped:
    def test_500_pages_clamped_to_task_limit(self):
        """500 页 × 30s = 15060s，必须被夹到任务上限的 90%"""
        got = converter._compute_timeout(300, Path("dummy.pdf"), ".pdf", page_count=500)
        assert got == int(LONG_TASK_SOFT_TIME_LIMIT * 0.9)
        assert got < LONG_TASK_SOFT_TIME_LIMIT, "夹紧值必须小于任务上限，留出收尾时间"

    def test_small_document_not_clamped(self):
        """小文件不能被夹紧逻辑影响"""
        assert converter._compute_timeout(300, Path("dummy.pdf"), ".pdf", page_count=1) == 300

    def test_non_pdf_uses_base_timeout(self):
        assert converter._compute_timeout(300, Path("dummy.md"), ".md") == 300

    def test_none_timeout_falls_back(self):
        assert converter._compute_timeout(None, Path("dummy.pdf"), ".pdf", page_count=1) == 300
