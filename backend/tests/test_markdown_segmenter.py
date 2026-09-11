"""
统一分块器与引用定位测试（overhaul-plan 阶段 2.1 / 2.7）

## 这个文件守的是什么

`segment_with_offsets` 产出的 `char_start/char_end` 会被前端拿去**在原文里
切片并高亮**。偏移错一位，高亮的就不是答案 —— 用户会以为系统在胡说，
而这正好摧毁产品承诺里"可追溯"的那一半。

因此本文件的核心是**不变量测试**：

    text[char_start:char_end] == content

它必须是**属性测试**而不是几个手写样例。理由来自实测：第一版实现
（事后用 `text.find()` 定位块）在 61 个真实 markdown、10926 个分段中
有 **1318 个（12%）违反**不变量，而当时只抽查了 1 个文件，恰好没触发。
第二版（拼接多块时用 `"\\n\\n"` 连接）**仍然有 1318 个违反**，
根因是块间在原文里可能只隔 1 个换行。两次都是靠"跑遍全部真实文件"
才发现的 —— 手写样例永远覆盖不到这种系统性偏差。

所以这里对**全部真实 markdown × 多种 limit × 有无 overlap** 逐一断言。
CI 上没有真实资料库（`backend/data/` 被 .gitignore），此时自动跳过，
但本机（以及任何有资料库的环境）会真正执行。
"""

import glob
import os
import random

import pytest

from app.services.markdown_segmenter import (
    Segment,
    make_segments,
    segment_with_offsets,
    split_markdown_blocks,
)


def _real_markdown_files() -> list[str]:
    """真实资料库里的 markdown（CI 上通常为空）"""
    backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return sorted(glob.glob(os.path.join(backend, "data", "storage", "**", "*.md"),
                            recursive=True))


# ---------------------------------------------------------------------------
# 不变量
# ---------------------------------------------------------------------------

class TestOffsetInvariant:
    """`text[char_start:char_end] == content`"""

    DOC = """# 第一章 总则

本章说明适用范围。

## 1.1 适用范围

本标准适用于拉哇水电站。

- 第一项
- 第二项

## 1.2 术语

| 术语 | 含义 |
|---|---|
| 浮充 | 蓄电池的一种运行方式 |

# 第二章 运行

```python
# 这不是标题
print("hello")
```

正文结束。
"""

    def test_offsets_slice_exactly(self):
        for seg in segment_with_offsets(self.DOC, 200):
            assert self.DOC[seg.char_start:seg.char_end] == seg.content

    def test_offsets_slice_exactly_at_many_limits(self):
        """多种 limit 下都必须成立（limit 会改变分段边界）"""
        for limit in (10, 30, 60, 120, 300, 1000):
            for seg in segment_with_offsets(self.DOC, limit):
                assert self.DOC[seg.char_start:seg.char_end] == seg.content, (
                    f"limit={limit} 时不变量被破坏"
                )

    def test_offsets_slice_exactly_with_overlap(self):
        """overlap 只能让区间变长，不能让内容与区间错位"""
        for limit in (40, 100, 300):
            for ov in (1, 2, 3):
                for seg in segment_with_offsets(self.DOC, limit, overlap_blocks=ov):
                    assert self.DOC[seg.char_start:seg.char_end] == seg.content, (
                        f"limit={limit} overlap={ov} 时不变量被破坏"
                    )

    def test_offsets_do_not_accumulate_drift(self):
        """偏移不得逐段累积漂移

        第一版失效模式的典型表现就是漂移：前面的块定位偏了，
        后面全部跟着偏。因此除了逐段断言，还要断言"相邻段区间单调不减"。
        """
        segs = segment_with_offsets(self.DOC, 80)
        assert segs, "未产出任何分段"
        for prev, cur in zip(segs, segs[1:], strict=False):
            assert cur.char_start >= prev.char_start, (
                f"分段区间出现回退: {cur.char_start} < {prev.char_start}"
            )
            assert cur.char_end > cur.char_start, "空区间"

    def test_segments_cover_the_document(self):
        """不加 overlap 时分段应覆盖全文所有非空块内容

        只测不变量会漏掉"整段被丢掉"这类缺陷（丢掉的部分不违反不变量）。
        """
        segs = segment_with_offsets(self.DOC, 120)
        covered = "".join(self.DOC[s.char_start:s.char_end] for s in segs)
        for block in split_markdown_blocks(self.DOC):
            assert block[:20] in covered, f"块未被任何分段覆盖: {block[:20]!r}"


@pytest.mark.skipif(not _real_markdown_files(), reason="本机无真实资料库（CI 上正常）")
class TestOffsetInvariantOnRealCorpus:
    """在**全部真实 markdown** 上跑不变量

    CI 上没有真实资料库会自动跳过；本机与自托管环境会真正执行。
    这是本文件最有价值的部分 —— 两次真实的系统性偏移 bug 都是
    靠"跑遍全部文件"才暴露的，手写样例全部漏过。
    """

    def test_invariant_holds_across_corpus(self):
        files = _real_markdown_files()
        total = 0
        violations = []
        for path in files:
            text = open(path, encoding="utf-8").read()
            for limit in (300, 700, 1200, 2500, 6000):
                for overlap in (0, 1):
                    for seg in segment_with_offsets(text, limit, overlap_blocks=overlap):
                        total += 1
                        if text[seg.char_start:seg.char_end] != seg.content:
                            violations.append(
                                f"{os.path.basename(path)} limit={limit} "
                                f"overlap={overlap} start={seg.char_start}"
                            )
        assert total > 0, "未扫描到任何分段（资料库为空？）"
        assert not violations, (
            f"{len(violations)}/{total} 个分段违反不变量 "
            f"（前 3 例: {violations[:3]}）"
        )

    def test_no_content_is_silently_dropped(self):
        """**内容覆盖率**：分段不得静默丢弃原文

        只测不变量是不够的 —— 第一版对超限块"定位失败就跳过"，
        丢掉的内容**不违反不变量**，因此不变量测试全绿而实际丢了
        10/61 个文件 8%~16% 的内容（最严重的一个丢 3410 字）。
        覆盖率是唯一能抓住这类缺陷的断言。
        """
        losses = []
        for path in _real_markdown_files():
            text = open(path, encoding="utf-8").read()
            if not text.strip():
                continue
            segs = segment_with_offsets(text, 1200)
            covered = sum(s.char_end - s.char_start for s in segs)
            if covered < len(text) * 0.98:
                losses.append(
                    f"{os.path.basename(path)}: {covered}/{len(text)} "
                    f"({covered / len(text):.0%})"
                )
        assert not losses, (
            f"{len(losses)} 个文件的分段覆盖率低于 98%（内容被静默丢弃）: {losses[:3]}"
        )


# ---------------------------------------------------------------------------
# 结构原子性
# ---------------------------------------------------------------------------

class TestBlockAtomicity:
    """结构块不得被从中间切断（这是 `markdown_segmenter` 的立身之本）"""

    def test_table_is_not_split_across_segments(self):
        table = "| 参数 | 数值 |\n|---|---|\n| 电压 | 500kV |\n| 容量 | 2000MW |"
        doc = f"前言文字。\n\n{table}\n\n后记文字。"
        for seg in segment_with_offsets(doc, 400):
            # 每段的表格行数必须是 0 或完整 4 行，不能是 1~3 行
            table_rows = [ln for ln in seg.content.split("\n") if ln.strip().startswith("|")]
            assert len(table_rows) in (0, 4), (
                f"表格被切断成 {len(table_rows)} 行: {table_rows}"
            )

    def test_code_fence_is_not_split(self):
        code = "```python\nx = 1\ny = 2\nprint(x + y)\n```"
        doc = f"说明。\n\n{code}\n\n结束。"
        for seg in segment_with_offsets(doc, 300):
            fences = seg.content.count("```")
            assert fences in (0, 2), f"代码围栏被切断（``` 出现 {fences} 次）"

    def test_oversized_block_split_is_still_exact(self):
        """超限块被拆分后，每个子段仍必须满足不变量，且**内容不丢**

        这条测试的来历：第一版对超限块先 `split_oversized_block()` 拆成字符串、
        再 `text.find()` 反查位置，反查失败就 `continue` 跳过。
        实测 61 个真实文件中 **10 个内容丢失 8%~16%**，最严重的
        41962 字文件丢了 3410 字，且完全静默 —— 因为**丢掉的内容不违反不变量**。

        所以这里同时断言"不变量"与"内容不丢"，两者缺一不可。
        """
        long_para = "。".join(f"第{i}句说明内容" for i in range(200)) + "。"
        doc = f"# 标题\n\n{long_para}"
        segs = segment_with_offsets(doc, 200)

        assert len(segs) > 1, "超长段落未被拆分"
        for seg in segs:
            assert doc[seg.char_start:seg.char_end] == seg.content

        # 内容不丢：所有分段的长度之和必须覆盖整篇（分段之间不重叠）
        covered = sum(s.char_end - s.char_start for s in segs)
        assert covered >= len(doc) * 0.99, (
            f"分段只覆盖了 {covered}/{len(doc)} 字符 —— 有内容被静默丢弃"
        )

    def test_oversized_paragraph_shorter_than_its_sentences(self):
        """整篇就是一句超长无标点文本时仍不得丢内容（退化为硬切）"""
        doc = "甲" * 1000
        segs = segment_with_offsets(doc, 100)
        assert segs, "未产出分段"
        for seg in segs:
            assert doc[seg.char_start:seg.char_end] == seg.content
        assert sum(s.char_end - s.char_start for s in segs) == len(doc)


# ---------------------------------------------------------------------------
# 标题路径
# ---------------------------------------------------------------------------

class TestHeadingPath:
    """`heading_path` 用于告诉用户"这段话出自哪一节"，并在前端显示面包屑"""

    def test_nested_path(self):
        doc = "# 第一章\n\n正文一。\n\n## 1.2 保护配置\n\n正文二。\n\n### 1.2.1 双重化\n\n正文三。"
        segs = segment_with_offsets(doc, 20)
        paths = [s.heading_path for s in segs]
        assert any(p == "第一章" for p in paths), f"缺少一级路径: {paths}"
        assert any(p == "第一章 > 1.2 保护配置" for p in paths), f"缺少二级路径: {paths}"
        assert any(p == "第一章 > 1.2 保护配置 > 1.2.1 双重化" for p in paths), (
            f"缺少三级路径: {paths}"
        )

    def test_sibling_heading_pops_the_stack(self):
        """遇到同级标题必须弹出上一节，不能串成 "第一章 > 1.2 > 1.3\""""
        doc = "# 第一章\n\n## 1.2 甲\n\n内容甲。\n\n## 1.3 乙\n\n内容乙。"
        segs = segment_with_offsets(doc, 15)
        paths = [s.heading_path for s in segs]
        assert "第一章 > 1.3 乙" in paths, (
            f"同级标题未弹出上一节，路径错误: {paths}"
        )
        assert not any("1.2 甲 > 1.3 乙" in p for p in paths), (
            f"同级标题被串成了层级: {paths}"
        )

    def test_heading_in_code_fence_is_not_a_heading(self):
        """围栏代码块里的 `#` 行不是标题

        代码块里的注释（如 Python 的 `# 说明`）若被当成标题，
        就会污染后续所有分段的标题路径。
        """
        doc = "```python\n# 这不是标题\nx = 1\n```\n\n正文。"
        segs = segment_with_offsets(doc, 20)
        for seg in segs:
            assert "这不是标题" not in seg.heading_path, (
                f"代码块内的 # 行被当成了标题: {seg.heading_path!r}"
            )

    def test_heading_path_present_as_dict(self):
        """`as_dict()` 必须带上定位字段（API 层要序列化它们）"""
        seg = segment_with_offsets("# 标题\n\n正文。", 100)[0]
        d = seg.as_dict()
        assert set(d) == {"content", "char_start", "char_end", "heading_path", "block_index"}
        assert d["char_start"] == 0


# ---------------------------------------------------------------------------
# overlap
# ---------------------------------------------------------------------------

class TestOverlap:

    def test_overlap_region_is_shared_between_neighbours(self):
        """开启 overlap 后相邻段必须真的重叠（否则参数没生效）"""
        doc = "\n\n".join(f"第{i}段内容文字。" for i in range(20))
        plain = segment_with_offsets(doc, 60, overlap_blocks=0)
        with_ov = segment_with_offsets(doc, 60, overlap_blocks=1)

        assert len(with_ov) >= len(plain) - 1
        overlapped = sum(
            1 for a, b in zip(with_ov, with_ov[1:], strict=False)
            if b.char_start < a.char_end
        )
        assert overlapped > 0, "overlap_blocks=1 时相邻段没有任何重叠"

    def test_overlap_does_not_exceed_limit_wildly(self):
        """overlap 不应把分段撑到远超 limit（否则等于没有上限）"""
        doc = "\n\n".join(f"第{i}段内容文字。" for i in range(30))
        limit = 80
        for seg in segment_with_offsets(doc, limit, overlap_blocks=1):
            assert len(seg.content) <= limit * 3, (
                f"分段被 overlap 撑到 {len(seg.content)}，limit={limit}"
            )


# ---------------------------------------------------------------------------
# 边界与随机
# ---------------------------------------------------------------------------

class TestEdgeCases:

    def test_empty_and_whitespace(self):
        assert segment_with_offsets("", 100) == []
        assert segment_with_offsets("   \n\n  ", 100) == []

    def test_single_line(self):
        segs = segment_with_offsets("只有一行。", 100)
        assert len(segs) == 1
        assert segs[0].char_start == 0
        assert segs[0].char_end == len("只有一行。")

    def test_no_duplicate_identical_offsets(self):
        """不得产出区间完全相同或空的分段（会导致前端重复高亮）"""
        doc = "\n\n".join(f"段落{i}。" for i in range(15))
        segs = segment_with_offsets(doc, 30)
        spans = [(s.char_start, s.char_end) for s in segs]
        assert len(spans) == len(set(spans)), f"出现重复区间: {spans}"
        for s, e in spans:
            assert e > s

    def test_random_documents_hold_invariant(self):
        """随机文档上的不变量（形状不可预测，最能暴露边界问题）"""
        rng = random.Random(20260911)
        # 每项都是"接受一个后缀、返回一段 markdown"的可调用对象
        pieces = [
            lambda s: f"# 标题{s}",
            lambda s: f"## 子标题{s}",
            lambda s: f"普通段落内容{s}。",
            lambda s: f"- 列表项{s}",
            lambda s: f"| 表头 | 值 |\n|---|---|\n| 甲 | {s} |",
            lambda s: f"```\ncode {s}\n```",
            lambda s: f"> 引用{s}",
            lambda s: "---",
            lambda s: "",
            lambda s: "   ",
        ]
        for trial in range(40):
            blocks = [rng.choice(pieces)(trial * 7 + i) for i in range(rng.randint(1, 25))]
            doc = "\n\n".join(blocks)
            for limit in (15, 40, 100):
                for seg in segment_with_offsets(doc, limit, overlap_blocks=rng.randint(0, 2)):
                    assert doc[seg.char_start:seg.char_end] == seg.content, (
                        f"trial={trial} limit={limit} 不变量被破坏"
                    )


# ---------------------------------------------------------------------------
# 与既有 API 的一致性
# ---------------------------------------------------------------------------

class TestCompatibilityWithExistingApi:
    """既有两个 API 的行为不得因本次新增而改变（它们在 LLM 抽取路径上）"""

    def test_split_markdown_blocks_still_returns_strings(self):
        blocks = split_markdown_blocks("# 标题\n\n正文。\n\n- 项")
        assert all(isinstance(b, str) for b in blocks)
        assert blocks == ["# 标题", "正文。", "- 项"]

    def test_make_segments_still_packs_by_limit(self):
        segs = make_segments(["a" * 10, "b" * 10, "c" * 10], 25)
        assert len(segs) == 2
        assert all(isinstance(s, str) for s in segs)

    def test_segment_is_frozen(self):
        """Segment 不可变：定位信息一旦产出就不该被就地改写

        `@dataclass(frozen=True)` 的赋值会抛 `FrozenInstanceError`
        （`AttributeError` 的子类）。这里断言"确实抛异常"而不是具体类型，
        是因为不同 Python 版本的消息不同，但"不可写"这一性质不能变。
        """
        import dataclasses

        seg = segment_with_offsets("正文。", 100)[0]
        assert isinstance(seg, Segment)
        with pytest.raises(dataclasses.FrozenInstanceError):
            seg.char_start = 999  # type: ignore[misc]
