# -*- coding: utf-8 -*-
"""`note.error_message` 的读时本地化（2026-11-08）

## 现场

`note.error_message` 是**写进数据库**的中文诊断串，界面直接渲染：

- `NotesList.tsx:517`：`{note.error_message}`（列表里每篇失败的笔记都显示）
- `CleaningPanel.tsx:258`：`{note.error_message ? `：${note.error_message}` : ''}`

于是英文界面里照样是「转换任务提交失败: …」「用户手动停止清洗」—— 而且
**切语言也不会变**（值已经固化在库里）。

## 修法：读的时候翻，不动库

在 `NoteResponse` / `NoteStatusResponse` 的 `error_message` 上加
`field_validator`，按 `NOTE_ERROR_MESSAGES` 翻译。这样：

- 历史行同样能翻（不需要数据迁移）；
- Celery 任务写库时**不需要**知道语言（它们本来也没有请求上下文）；
- 库里的中文保持原样，排查时仍可按原文 grep。

## 这一组守 4 件事

1. 整句固定 → 翻；
2. 前缀 + 冒号尾巴 → 翻前缀、**尾巴原样保留**（尾巴常是异常文本）；
3. 复合句（前缀 + 动态括号 + 固定建议句）→ 整句模板翻；
4. 匹配不上 → **原样返回**（不能把诊断串翻丢）。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from app.core import context
from app.core.error_messages import (
    NOTE_ERROR_MESSAGES,
    translate_note_error,
)
from app.schemas.note import NoteResponse, NoteStatusResponse

APP_DIR = Path(__file__).resolve().parents[1] / "app"
CJK = re.compile(r"[\u4e00-\u9fff]")


@pytest.fixture(autouse=True)
def _locale():
    yield

    context.reset_context()


def _as_en() -> None:
    context.set_request_context(request_id="rid-x", locale="en")


class TestTranslateNoteError:
    def test_exact_sentence(self):
        _as_en()
        assert translate_note_error("用户手动停止清洗") == "Cleaning stopped by the user"

    def test_prefix_with_dynamic_tail_keeps_tail(self):
        _as_en()
        got = translate_note_error("转换任务提交失败: [Errno 13] Permission denied")
        assert got == "Failed to enqueue the conversion task: [Errno 13] Permission denied"

    def test_longest_prefix_wins(self):
        """`转换任务重试失败` 不能被 `转换任务提交失败` 之类抢走"""
        _as_en()
        got = translate_note_error("转换任务重试失败: boom")
        assert got.startswith("Conversion retry failed"), got

    def test_composite_template(self):
        _as_en()
        text = "检索索引未建好（向量缺失）。这篇笔记的问答与原文定位暂时不可用，重新清洗一次可重试。"
        got = translate_note_error(text)
        assert got.startswith("The search index could not be built (向量缺失)."), got
        assert "clean it again to retry" in got

    def test_unknown_text_passes_through(self):
        _as_en()
        assert translate_note_error("某个没登记过的诊断串: 细节") == "某个没登记过的诊断串: 细节"

    def test_empty_and_none(self):
        _as_en()
        assert translate_note_error(None) is None
        assert translate_note_error("") == ""

    def test_chinese_locale_is_identity(self):
        _as_en()  # 故意先设 en，再确认 locale 为 zh 时不变
        context.set_request_context(request_id="rid-y", locale="zh-CN")
        assert translate_note_error("转换任务提交失败: x") == "转换任务提交失败: x"


class TestSchemaSurfacesIt:
    """schema 层是**所有**笔记响应的公共出口，必须真的过一遍翻译"""

    def test_note_response_translates(self):
        _as_en()
        note = NoteResponse.model_validate(
            {
                "id": "n1",
                "user_id": "u1",
                "title": "t",
                "source_type": "pdf",
                "status": "failed",
                "file_size": 1,
                "error_message": "转换任务提交失败: boom",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            }
        )
        assert note.error_message == "Failed to enqueue the conversion task: boom"

    def test_status_response_translates(self):
        _as_en()
        status = NoteStatusResponse(id="n1", status="failed", error_message="用户手动停止清洗")
        assert status.error_message == "Cleaning stopped by the user"

    def test_chinese_request_keeps_original(self):
        context.set_request_context(request_id="rid-z", locale="zh-CN")
        note = NoteStatusResponse(id="n1", status="failed", error_message="用户手动停止清洗")
        assert note.error_message == "用户手动停止清洗"


class TestWriteSitesAreRegistered:
    """写库的每一条中文 `error_message` 都要能被翻译覆盖（AST 扫描）

    没有这条守卫时，新增一句 `note.error_message = "…"` 就会让英文界面
    永久显示中文，而且**没有任何测试会红**（字段是自由文本）。
    """

    def _write_sites(self) -> list[tuple[str, int, str]]:
        found: list[tuple[str, int, str]] = []
        for path in sorted(APP_DIR.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                # note.error_message = "…" / error_message="…"（关键字参数）
                value = None
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Attribute) and target.attr == "error_message":
                            value = node.value
                elif isinstance(node, ast.keyword) and node.arg == "error_message":
                    value = node.value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    if CJK.search(value.value):
                        found.append((str(path.relative_to(APP_DIR)), node.lineno, value.value))
        return found

    def test_each_fixed_write_site_has_an_entry(self):
        missing = [
            f"{name}:{line} → {text!r}" for name, line, text in self._write_sites() if text not in NOTE_ERROR_MESSAGES
        ]
        assert not missing, (
            "这些写入 `note.error_message` 的中文没有本地化条目（英文界面会显示中文）：\n  "
            + "\n  ".join(missing)
            + "\n做法：在 error_messages.py 的 NOTE_ERROR_MESSAGES 里加一条"
            "（带运行期尾巴的用前缀形式，复合句用 _NOTE_ERROR_TEMPLATES）。"
        )
