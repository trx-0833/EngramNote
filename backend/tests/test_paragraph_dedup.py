# -*- coding: utf-8 -*-
"""
段落级去重（B1 第 ④ 项）
=========================

## 这一组盯的是什么

检索分块是 `chunk_size=500`，于是一段 100 字的重复文字会和它前后文**落在同一个
500 字块里** —— 块与块的相似度因此远低于阈值，**检测不到**。走查报告 §5 实测：
样本里刻意重复的段落仍出现 2 次，而界面上显示「本次未检测到重复块」。

**那是"静默说谎"**：用户看到的是"没有重复"，事实是"我们按 500 字找的"。

两个修法各做一半，这一组各钉一半：
- 检测补到**段落级**（`segment_paragraphs` / `find_duplicate_paragraphs`）；
- 文案**如实说清粒度**（前端那句，由 `CleaningPanel.test.tsx` 钉）。

## 实测踩过的两个坑（各有一条用例守着）

1. `segment_with_offsets(text, 1)` **拿不到段落** —— `limit` 小于块长度时分块器
   会把每个块**按字符拆开**，实测 5 行文本切出 **65 个"段落"**，
   于是 `#`、`。` 这种单字符块互相相似度 1.0，去重结果里塞满垃圾（34 条）。
2. 段落级与块级的 `block_index` **不是同一个坐标系** —— 混进同一次
   `generate_clean_copy` 会把标记打在**错误的行**上（且不报错）。
"""

from __future__ import annotations

import pytest

from app.services.cleaning_service import (
    PARAGRAPH_MAX_CHARS,
    apply_line_range_marks,
    find_duplicate_paragraphs,
    generate_clean_copy,
    segment_paragraphs,
)


def _doc(*paragraphs: str) -> str:
    """把若干段落拼成"空行分隔"的 Markdown"""
    return "\n\n".join(paragraphs)


class TestSegmentParagraphs:
    def test_blank_lines_are_the_paragraph_boundary(self):
        """段落 = 空行分隔的文本块（用户口语里的段落）"""
        text = _doc("# 标题", "第一段。", "第二段。", "第三段。")
        paras = segment_paragraphs(text)
        assert [p["content"] for p in paras] == ["# 标题", "第一段。", "第二段。", "第三段。"]
        assert [p["index"] for p in paras] == [0, 1, 2, 3]

    def test_line_ranges_point_at_the_right_lines(self):
        """行范围必须指对（下游按它标注释，指错就会标错行）"""
        text = _doc("# 标题", "第一段。", "第二段。")
        paras = segment_paragraphs(text)
        # "# 标题" 在第 0 行；"第一段。" 在第 2 行（第 1 行是空行）；"第二段。" 在第 4 行
        assert [(p["start_line"], p["end_line"]) for p in paras] == [(0, 0), (2, 2), (4, 4)]

    def test_slice_invariant_holds(self):
        """★ `text[char_start:char_end] == content`（与检索分块同一条不变量）

        它在开发中被违反过两次、各 1318 个分段（`docs/overhaul-plan.md` 附录 L.2），
        所以这条必须自证。
        """
        text = _doc("# 标题", "第一段，含 `code` 与 $x^2$。", "- 列表项一\n- 列表项二", "末段。")
        for p in segment_paragraphs(text):
            assert text[p["char_start"] : p["char_end"]] == p["content"], (
                f"切片与 content 不一致: index={p['index']}"
            )

    def test_multiline_paragraph_stays_one_paragraph(self):
        """★ 段内的单个换行**不是**段落边界（列表项必须留在同一段里）"""
        text = _doc("# 标题", "- 列表项一\n- 列表项二\n- 列表项三")
        paras = segment_paragraphs(text)
        assert len(paras) == 2, f"列表被拆成了 {len(paras)} 段"
        assert paras[1]["content"] == "- 列表项一\n- 列表项二\n- 列表项三"

    def test_long_paragraph_is_split_by_line_boundary(self):
        """超长段按**行边界**再拆（不跨行切，保住不变量）"""
        long_para = "\n".join(f"第 {i} 行" + "字" * 60 for i in range(30))
        paras = segment_paragraphs(long_para)
        assert len(paras) > 1, "超长段没被拆开 —— 两处重复会藏在同一段里"
        for p in paras:
            assert len(p["content"]) <= PARAGRAPH_MAX_CHARS + 80, "拆完还是超长"
            assert long_para[p["char_start"] : p["char_end"]] == p["content"]

    def test_empty_text_gives_empty_list(self):
        assert segment_paragraphs("") == []
        assert segment_paragraphs("   \n\n  ") == []


class TestFindDuplicateParagraphs:
    def test_short_paragraph_duplicate_is_found(self):
        """★ 块级检测不到的那种重复（短段逐字重复）必须被找到

        这正是走查报告 §5 实测的那一幕：样本里刻意重复的段落仍出现 2 次。
        """
        text = _doc(
            "# 标题",
            "第一段讲浮充的定义，端电压保持恒定。",
            "第二段讲均充，电压更高一些。",
            "第一段讲浮充的定义，端电压保持恒定。",
            "第三段是别的内容。",
        )
        dupes = find_duplicate_paragraphs(text, [])
        assert len(dupes) == 1, f"段落级重复没找到（或找到多条）: {dupes}"
        d = dupes[0]
        assert d["similarity"] == pytest.approx(1.0)
        assert d["granularity"] == "paragraph"
        # 重复的是第 3 段（行号 6），首次出现是第 1 段（行号 2）
        assert (d["start_line"], d["end_line"]) == (6, 6)
        assert d["duplicate_of"] == 1

    def test_no_duplicates_gives_empty_list(self):
        text = _doc("# 标题", "甲段。", "乙段。", "丙段。")
        assert find_duplicate_paragraphs(text, []) == []

    def test_single_paragraph_cannot_have_duplicates(self):
        assert find_duplicate_paragraphs("只有一段。", []) == []

    def test_ranges_already_covered_by_chunk_level_are_skipped(self):
        """★ 块级已覆盖的行范围不重复报（否则界面上同一条重复出现两次）"""
        text = _doc("# 标题", "甲段。", "甲段。")
        chunk_dupes = [{"block_index": 0, "duplicate_of": 0, "similarity": 0.9,
                        "line_range": (0, 4)}]
        assert find_duplicate_paragraphs(text, chunk_dupes) == []

    def test_duplicates_outside_covered_ranges_are_still_found(self):
        """反向：块级覆盖的是别处时，段落级该报的还是要报"""
        text = _doc("# 标题", "甲段。", "乙段。", "甲段。")
        chunk_dupes = [{"block_index": 0, "duplicate_of": 0, "similarity": 0.9,
                        "line_range": (0, 0)}]
        dupes = find_duplicate_paragraphs(text, chunk_dupes)
        assert len(dupes) == 1, dupes


class TestApplyLineRangeMarks:
    def test_marks_are_inserted_around_the_range(self):
        text = _doc("# 标题", "甲段。", "乙段。")
        dupes = [{"block_index": 2, "duplicate_of": 1, "similarity": 1.0,
                  "start_line": 4, "end_line": 4}]
        out, n = apply_line_range_marks(text, dupes)
        assert n == 1
        lines = out.split("\n")
        assert lines[4].startswith("<!-- duplicate: block_2")
        assert lines[6] == "<!-- /duplicate -->"
        assert lines[5] == "乙段。"

    def test_does_not_nest_inside_existing_marks(self):
        """★ 已经在 `<!-- duplicate -->` 块里的行**不再包一层**

        嵌套注释会让 Markdown 渲染出乱码，而且"恢复"操作会解错一层。
        """
        text = "\n".join([
            "<!-- duplicate: block_0 similarity=0.90 -->",
            "甲段。",
            "<!-- /duplicate -->",
        ])
        dupes = [{"block_index": 1, "duplicate_of": 0, "similarity": 1.0,
                  "start_line": 1, "end_line": 1}]
        out, n = apply_line_range_marks(text, dupes)
        assert n == 0, "在已标记区间里又包了一层"
        assert out == text

    def test_multiple_marks_are_inserted_without_shifting_each_other(self):
        """★ 多处标记：从后往前插入，前面的行号不受影响"""
        text = "\n".join([f"第 {i} 段。" for i in range(6)])
        dupes = [
            {"block_index": 1, "duplicate_of": 0, "similarity": 1.0, "start_line": 1, "end_line": 1},
            {"block_index": 3, "duplicate_of": 2, "similarity": 1.0, "start_line": 4, "end_line": 4},
        ]
        out, n = apply_line_range_marks(text, dupes)
        assert n == 2
        lines = out.split("\n")
        # 第 1 段（原行 1）与第 4 段（原行 4）各自被包住
        assert lines[1].startswith("<!-- duplicate: block_1")
        assert lines[3] == "<!-- /duplicate -->"
        # 后面的插入不该把前面的标记挤走
        assert any(ln.startswith("<!-- duplicate: block_3") for ln in lines)
        assert out.count("<!-- /duplicate -->") == 2

    def test_empty_duplicates_is_a_noop(self):
        text = "甲段。"
        assert apply_line_range_marks(text, []) == (text, 0)

    def test_out_of_range_is_ignored_not_crashed(self):
        """越界的行范围**跳过**而不是抛异常（清洗不该因为一条坏数据挂掉）"""
        text = _doc("甲段。", "乙段。")
        dupes = [{"block_index": 9, "duplicate_of": 0, "similarity": 1.0,
                  "start_line": 99, "end_line": 120}]
        out, n = apply_line_range_marks(text, dupes)
        assert n == 0
        assert out == text


class TestCoordinateSystems:
    """★ 两套 `block_index` 不是同一个坐标系 —— 这条最容易写错且不会报错"""

    def test_chunk_and_paragraph_index_sets_differ(self):
        """同一份文本，段落索引与检索块索引**确实不同**（不是理论担心）

        构造：三个短段 + 若干填充，使 500 字检索块把多段装进同一块 ——
        段落数因此明显多于检索块数。
        """
        from app.services.markdown_segmenter import segment_with_offsets, to_retrieval_chunks

        text = "\n\n".join(f"第 {i} 段：" + "填充" * 40 for i in range(10))
        paras = segment_paragraphs(text)
        chunks = to_retrieval_chunks(segment_with_offsets(text, 500))
        assert len(paras) != len(chunks), (
            "段落数与检索块数相同，这个构造证明不了坐标差异 —— 换一个更长的样本"
        )
        assert len(paras) > len(chunks), "段落应该比 500 字块更细"

    def test_granularity_parameter_selects_the_matching_segmentation(self):
        """`granularity='paragraph'` 时必须用段落分块，否则标记会打在错误的行上"""
        text = _doc("# 标题", "甲段。", "乙段。", "甲段。")
        paras = segment_paragraphs(text)
        # 找出重复段的索引（按段落坐标）
        dupes = find_duplicate_paragraphs(text, [])
        assert dupes, "构造里没有重复，用例失去意义"

        out, stats = generate_clean_copy(text, text, dupes, granularity="paragraph")
        assert stats["granularity"] == "paragraph"
        # 标记必须落在**重复段**那一段，而不是标题那一段
        lines = out.split("\n")
        mark_idx = next(i for i, ln in enumerate(lines) if ln.startswith("<!-- duplicate:"))
        assert lines[mark_idx + 1].strip() == "甲段。", (
            f"标记落在了 {lines[mark_idx + 1]!r} —— 坐标用错了"
        )
        assert paras  # 让 linters 知道 paras 被用过

    def test_granularity_is_reported_even_when_there_are_no_duplicates(self):
        """★ 没有重复时也要如实报粒度

        界面那句"未检测到重复"**只在没有重复时出现** ——
        若 `granularity` 在早退路径上没写，这个字段就在最需要它的时候失效。
        第一版正是这么写的。
        """
        text = _doc("甲段。", "乙段。")
        _, stats = generate_clean_copy(text, text, [], granularity="paragraph")
        assert stats["granularity"] == "paragraph"
        _, stats2 = generate_clean_copy(text, text, [])
        assert stats2["granularity"] == "chunk"
