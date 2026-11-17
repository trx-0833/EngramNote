"""回归审计发现的两条「改了但没完全改」（2026-11-18）。

1. **A6 横幅仍会写两遍**：round9 只改了提示词（"不要写"），但模型不保证遵守。
   实测 `/ask` 与 `/ask/stream` 都出现 `【…】\n\n【…】\n\n答案`。
   修法：输出侧 strip 一次。
2. **`converter.py:715` 的 timeout 漏了**：4 处 HTTP 调用只补了 3 处，
   而漏掉的正是"申请上传地址"——云端不回包就永久挂起的那一处。
"""

from __future__ import annotations

from pathlib import Path


class TestGeneralBannerDeduplicated:
    def test_stream_has_output_side_strip(self):
        """流式端点必须有输出侧去重，不能只靠提示词"""
        src = Path("app/api/understanding.py").read_text(encoding="utf-8")
        assert "_stripped_leading" in src, "流式端点缺少输出侧横幅去重：只改提示词不构成保证，实测会写两遍"
        # 去重必须在 token 循环里，不是循环之后补一刀
        loop_at = src.index("async for chunk in llm_service.chat_stream")
        assert "_stripped_leading" in src[loop_at : loop_at + 2500]


class TestAllHttpCallsHaveTimeout:
    def test_four_calls_each_have_timeout(self):
        """4 处 HTTP 调用逐个校验（不能只数总数）"""
        src = Path("app/services/mineru/converter.py").read_text(encoding="utf-8")
        calls = []
        for i, line in enumerate(src.splitlines(), 1):
            if "req.post(" in line or "req.put(" in line or "req.get(" in line:
                window = " ".join(src.splitlines()[i - 1 : i + 2])
                calls.append((i, "timeout=" in window))
        assert len(calls) >= 4, f"预期 4 处 HTTP 调用，实际找到 {len(calls)}: {calls}"
        missing = [n for n, ok in calls if not ok]
        assert not missing, f"这些行缺少 timeout：{missing}"
