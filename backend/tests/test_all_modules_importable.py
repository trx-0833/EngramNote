"""全模块导入冒烟：任何 `app/` 下的模块都必须可导入。

## 为什么需要这条（二次审计的教训）

我曾在 `converter.py` 的**函数体内**写 `from ..tasks.celery_app import ...` ——
层级少一个点（`..tasks` = `app.services.tasks`）。后果是 **所有 PDF 转换必然失败**，
而当时：

- 2008 条用例全绿（写在函数里，import 期不报）；
- 也没有用例用真实页数调过那个函数（假 PDF 读不出页数 → 走早退分支）。

**写在函数体内的 import 是隐形错误**：层级写错只在恰好执行到那一行时才暴露。
这条冒烟用例把"路径写错"提前到收集期，几秒钟，覆盖 177 个模块。

## 为什么不是逐条 `import` 语句

用 `pkgutil.walk_packages` 自动发现 —— 新增模块**自动纳入**，不会因为忘了加一行而漏。
"""

from __future__ import annotations

import importlib
import pkgutil

import pytest

import app


def _all_modules() -> list[str]:
    names = ["app"]
    for info in pkgutil.walk_packages(app.__path__, prefix="app."):
        names.append(info.name)
    return sorted(set(names))


ALL_MODULES = _all_modules()


def test_module_discovery_is_not_empty():
    """自检：发现机制本身不能失效（否则这条用例形同虚设）"""
    assert len(ALL_MODULES) > 100, f"只发现 {len(ALL_MODULES)} 个模块，walk_packages 可能失效"


@pytest.mark.parametrize("module_name", ALL_MODULES)
def test_module_imports(module_name: str):
    """每个模块都必须能导入（含模块体内的 import）"""
    importlib.import_module(module_name)


def test_celery_tasks_wire_time_limits():
    """三个长任务必须真的带上 per-task 时间上限（不能只定义常量不用）"""
    from app.tasks.celery_app import LONG_TASK_SOFT_TIME_LIMIT, LONG_TASK_TIME_LIMIT
    from app.tasks.clean_tasks import clean_document_task
    from app.tasks.convert_tasks import convert_document_task
    from app.tasks.understand_tasks import understand_document_task

    assert LONG_TASK_TIME_LIMIT == 7200
    assert LONG_TASK_SOFT_TIME_LIMIT < LONG_TASK_TIME_LIMIT
    for task in (convert_document_task, clean_document_task, understand_document_task):
        assert task.time_limit == LONG_TASK_TIME_LIMIT, f"{task.name} 缺 time_limit"
        assert task.soft_time_limit == LONG_TASK_SOFT_TIME_LIMIT, f"{task.name} 缺 soft_time_limit"
