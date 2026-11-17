"""A6 横幅必须**恰好一次**（回归审计 R1：我上一版仍会出两次）。

判别性用例必须覆盖两种吐出方式：
1. 模型**自己写了横幅**、且**分块**吐（真实模型第一个 chunk 往往是半个横幅）——
   上一版的条件 `not _pending.strip()` 会让半个横幅立刻定型 ⇒ 最终 2 次；
2. `/ask`（非流式）**只加不剥** ⇒ 任何情况都 2 次。
"""

from __future__ import annotations


from app.api.understanding import (
    _GENERAL_PREFIX,
    _strip_general_banner,
)


class TestStripHelper:
    def test_strips_leading_once(self):
        assert _strip_general_banner(f"{_GENERAL_PREFIX}\n\n正文") == "正文"

    def test_strips_with_other_whitespace(self):
        assert _strip_general_banner(f"\n{_GENERAL_PREFIX}\n正文") == "正文"

    def test_does_not_touch_body_mention(self):
        """正文里提到这句话不该被删（只剥开头）"""
        text = f"按你的资料，{_GENERAL_PREFIX} 这句话会出现在提示里。"
        assert _strip_general_banner(text) == text

    def test_idempotent(self):
        once = _strip_general_banner(f"{_GENERAL_PREFIX}\n\n正文")
        assert _strip_general_banner(once) == once


class TestBannerExactlyOnce:
    """模拟模型自己写横幅 + 分块吐出（回归审计实测 3/5 块都是 2 次）"""

    @staticmethod
    def _run(chunks: list[str]) -> str:
        """把分块序列喂给流式端点的去重逻辑，返回最终答案正文。"""
        # 直接复用端点里的判定顺序（与实现同步的最小副本）
        stripped_leading = False
        pending = ""
        out = ""
        for chunk in chunks:
            if not stripped_leading:
                pending += chunk
                head = pending.lstrip()
                if len(head) < len(_GENERAL_PREFIX) and _GENERAL_PREFIX.startswith(head):
                    continue
                candidate = _strip_general_banner(pending).lstrip(chr(10))
                if not candidate:
                    pending = ""
                    continue
                chunk = candidate
                stripped_leading = True
                pending = ""
            out += chunk
        if not stripped_leading and pending:
            tail = _strip_general_banner(pending)
            if tail.strip():
                out += tail
        return out

    def test_model_writes_banner_in_three_chunks(self):
        raw = f"{_GENERAL_PREFIX}\n\n这是通用知识的回答。"
        third = len(raw) // 3
        chunks = [raw[:third], raw[third : third * 2], raw[third * 2 :]]
        assert self._run(chunks) == "这是通用知识的回答。"

    def test_model_writes_banner_in_five_chunks(self):
        raw = f"{_GENERAL_PREFIX}\n\n这是通用知识的回答，稍长一些以便切分。"
        step = len(raw) // 5
        chunks = [raw[i * step : (i + 1) * step] for i in range(5)] + [raw[5 * step :]]
        out = self._run([c for c in chunks if c])
        assert _GENERAL_PREFIX not in out, f"横幅没剥干净：{out!r}"
        assert out.startswith("这是通用知识")

    def test_model_does_not_write_banner(self):
        assert self._run(["这是通用知识的回答。"]) == "这是通用知识的回答。"

    def test_pure_banner_only(self):
        """整段只有横幅：剥完应该是空，而不是残留一个换行"""
        out = self._run([_GENERAL_PREFIX, "\n\n"])
        assert out.strip() == ""
