"""A8 用例：`build_retrieval_query` 的上文锚点。

报告要求"追问子集 Recall@5 +5 点，否则回退"。**那是个需要专门构造追问语料的验收**，
本轮评测骨架（A9）只覆盖单轮检索，所以这里守的是可测的部分：拼接顺序、去重、
截断、以及"没有锚点时行为与旧实现逐字相同"（防回归）。
"""

from __future__ import annotations

import pytest

from app.services.qa_context import ANCHOR_MAX_CHARS, build_retrieval_query


class TestAnchorTitles:
    def test_no_anchor_is_identical_to_old_behavior(self):
        """没有锚点时必须与旧实现逐字相同（A8 不能改掉既有行为）"""
        assert build_retrieval_query("上一问", "当前问") == "上一问 当前问"
        assert build_retrieval_query(None, "当前问") == "当前问"
        assert build_retrieval_query("上一问", "") == "上一问"

    def test_anchor_goes_first(self):
        """锚点放最前：查询向量对靠前内容权重更高，而"那它呢"本身没有信息量"""
        got = build_retrieval_query("上一问", "那它呢？", ["拉哇水电站运行规程"])
        assert got == "拉哇水电站运行规程 上一问 那它呢？"

    def test_anchor_without_prev(self):
        assert build_retrieval_query(None, "那它呢？", ["油系统技术标准"]) == "油系统技术标准 那它呢？"

    def test_anchor_dedup_and_empty_dropped(self):
        got = build_retrieval_query("上一问", "当问", ["同一篇", "同一篇", ""])
        assert got == "同一篇 上一问 当问"

    def test_anchor_is_clipped(self):
        long_title = "题" * (ANCHOR_MAX_CHARS + 40)
        got = build_retrieval_query(None, "当问", [long_title])
        assert len(got) < len(long_title) + 10, "锚点没有被截断"
        assert got.endswith("当问")

    def test_prev_inside_current_is_not_repeated(self):
        got = build_retrieval_query("寿命", "那它的寿命呢？", ["标题"])
        assert got == "标题 那它的寿命呢？"

    @pytest.mark.parametrize("anchors", [None, [], [""]])
    def test_empty_anchors_are_noop(self, anchors):
        assert build_retrieval_query("上一问", "当前问", anchors) == "上一问 当前问"
