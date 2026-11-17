"""全模块导入冒烟：`app/` 下的模块都必须可导入。

## 为什么需要这条（二次审计的教训）

我曾在 `converter.py` 的**函数体内**写 `from ..tasks.celery_app import ...` ——
层级少一个点（`..tasks` = `app.services.tasks`）。后果是 **所有 PDF 转换必然失败**，
而当时 2008 条用例全绿（写在函数里，import 期不报；也没有用例用真实页数调过它）。

**写在函数体内的 import 是隐形错误**：层级写错只在恰好执行到那一行时才暴露。
这条冒烟把"路径写错"提前到收集期，几秒钟覆盖全部模块。

## ⚠️ 与可选依赖的边界（CI 曾因此失败）

`app/services/asr/{asr_engine,audio_utils}.py` 在**顶层** import `torch` / `qwen_asr` /
`soundfile` —— 它们是 `requirements-asr.txt` 里的可选重依赖，CI 装的
`requirements-test.txt` **不含**。所以这里必须区分两种缺失：

| 缺什么 | 处理 | 理由 |
|---|---|---|
| **`app` 内部**模块（如 `app.services.tasks`） | **报错** | 这正是要抓的层级写错 |
| 外部可选包（torch / qwen_asr / …） | **skip** | 环境未装该可选能力，不是代码错 |

判据：`ModuleNotFoundError.name` 的首段。以 `app` 开头 ⇒ 内部错误，必须红。
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


def _is_internal(missing: str | None) -> bool:
    """缺失的是不是 `app` 自己的模块（那就是真 bug，不是环境问题）"""
    return bool(missing) and missing.split(".")[0] == "app"


@pytest.mark.parametrize("module_name", ALL_MODULES)
def test_module_imports(module_name: str):
    try:
        importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if _is_internal(exc.name):
            # `app` 内部路径写错 ⇒ 必须红（这就是 converter.py 那次 blocker 的形态）
            raise
        pytest.skip(f"可选依赖未安装: {exc.name}")
    except ImportError as exc:  # 例如 "cannot import name X from Y"
        if _is_internal(getattr(exc, "name", None)):
            raise
        pytest.skip(f"可选依赖不完整: {exc}")


class TestSkipJudgement:
    """守住上面那个判据本身（判反了就会把真 bug 也 skip 掉）"""

    def test_internal_is_not_skipped(self):
        assert _is_internal("app.services.tasks") is True

    def test_external_is_skipped(self):
        assert _is_internal("torch") is False
        assert _is_internal("qwen_asr") is False
        assert _is_internal(None) is False


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
