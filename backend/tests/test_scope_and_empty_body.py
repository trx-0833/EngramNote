"""R5 / N1 的守卫：授权交集只许有一份实现；`/ask` 剥完为空要兜底。

## R5
`/ask` 与 `/ask/stream` 原本各写一份**逐字相同**的授权交集查询。这个语义改过多次
（空集合曾被 `or None` 变成"不限"、fallback 分支曾漏传导致范围逃逸），
每次"只改一处"都会漏。现在只允许存在 `_resolve_scope_note_ids` 一处实现。

## N1
模型只回横幅时，剥完是空串 —— 直接拼会得到「横幅 + 空正文」（审计实测 4/5）。
"""

from __future__ import annotations

from pathlib import Path

import pytest


class TestScopeResolutionSingleSource:
    def _src(self) -> str:
        return Path("app/api/understanding.py").read_text(encoding="utf-8")

    def test_helper_exists(self):
        from app.api.understanding import _resolve_scope_note_ids

        assert callable(_resolve_scope_note_ids)

    def test_inline_intersection_removed(self):
        """两处端点都不许再内联写交集查询（只留 helper 里的一份）"""
        src = self._src()
        occurrences = src.count("_Note.id.in_(")
        assert occurrences == 1, f"`_Note.trashed_at.is_(None)` 出现 {occurrences} 次 —— 授权交集应只有 helper 一处"

    def test_both_endpoints_call_helper(self):
        src = self._src()
        assert src.count("await _resolve_scope_note_ids(") >= 2, "两个端点都该调用 helper；只改一处正是 R5 要防的事故"

    @pytest.mark.parametrize(
        ("scope", "expected_kind"),
        [(None, "unrestricted"), ([], "empty")],
    )
    def test_semantics_none_vs_empty(self, scope, expected_kind):
        """判据必须是 `is not None`：`[]` 是有效输入，不是"没传\""""
        src = self._src()
        assert "if scope_note_ids is None:" in src
        # 反向守护：不许用真值判断（`if not scope_note_ids` 会把 [] 当没传）
        assert "if not scope_note_ids:" not in src


class TestAskFallsBackWhenBodyEmpty:
    def test_rag_service_guards_empty_body(self):
        src = Path("app/services/rag_service.py").read_text(encoding="utf-8")
        at = src.index("strip_general_banner(answer)")
        window = src[at : at + 700]
        assert "not answer.strip()" in window, "剥完为空时没有兜底 ⇒ 用户会看到「横幅 + 空正文」"
