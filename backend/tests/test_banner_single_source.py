"""`/ask`（非流式）的 A6 横幅也必须**恰好一次**。

回归审计 R1 实测：流式那条改对后，`/ask` 仍然 2 次 —— 因为 `rag_service` 那边
只是 `f"{_GENERAL_PREFIX}\n\n{answer}"`，**只加不剥**。模型守规矩写了横幅时就成了两份。

这里同时守住"唯一定义处"这件事：常量与剥离逻辑只允许在 `qa_context` 里定义一次，
否则"两处只改一处"的事故会再来一次（这个 bug 已经发生过两轮）。
"""

from __future__ import annotations

from pathlib import Path

from app.services.qa_context import GENERAL_BANNER, strip_general_banner


class TestSingleDefinition:
    def test_banner_defined_once(self):
        """横幅字符串只允许在 qa_context 里出现字面量，别处必须是引用"""
        roots = [
            Path("app/api/understanding.py"),
            Path("app/services/rag_service.py"),
        ]
        for path in roots:
            src = path.read_text(encoding="utf-8")
            assert GENERAL_BANNER not in src, f"{path} 里又写了一份横幅字面量 —— 应该 import GENERAL_BANNER"

    def test_strip_helper_single_implementation(self):
        """剥离逻辑不允许有第二份实现"""
        for path in (Path("app/api/understanding.py"), Path("app/services/rag_service.py")):
            src = path.read_text(encoding="utf-8")
            assert "def strip_general_banner" not in src, f"{path} 又写了一份剥离实现"


class TestAskStripsBeforePrefixing:
    def test_ask_strips_model_banner(self):
        """/ask 的返回路径必须先剥再拼（源码级断言，避免依赖 LLM 桩）"""
        src = Path("app/services/rag_service.py").read_text(encoding="utf-8")
        at = src.index('"answer": f"{_GENERAL_PREFIX}')
        head = src[max(0, at - 600) : at]
        assert "strip_general_banner(" in head, "/ask 在拼横幅之前没有剥掉模型自己写的那一份 ⇒ 会出现两次"

    def test_strip_removes_only_leading(self):
        assert strip_general_banner(f"{GENERAL_BANNER}\n\n正文") == "正文"
        body_mention = f"资料里提到 {GENERAL_BANNER} 这句话"
        assert strip_general_banner(body_mention) == body_mention
