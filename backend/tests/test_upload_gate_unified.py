"""安全审计 F1 的回归用例：**两条上传入口都必须过同一道闸门**。

历史问题（有实测）：`POST /api/upload` 单次直传完全不校验压缩包与 PDF 页数，
3000 页 PDF / 1025:1 的 docx 炸弹都能入队；而 `prepare` 全拦。

这里用「源码级结构断言」守住结构本身：只要 `_do_upload` 里有闸门调用，
两条入口就都绕不过去。功能级验证（真传炸弹）留给人工/集成测试。
"""

from __future__ import annotations

from pathlib import Path

# 注意：本文件路径里不要出现网络库名字 —— conftest 的子串启发式会把整个文件
# 当成集成用例永久跳过（安全审计发现过一次，守卫等于不存在）。


class TestUnifiedUploadGate:
    def test_gate_function_exists(self):
        from app.services.upload_safety import validate_staged_file

        assert callable(validate_staged_file)

    def test_do_upload_calls_the_gate(self):
        """`_do_upload` 是两条入口的汇合点，闸门必须在这里"""
        src = Path("app/api/upload.py").read_text(encoding="utf-8")
        start = src.index("async def _do_upload")
        # 取函数体前 3000 字符（闸门应尽早执行）
        head = src[start : start + 3000]
        assert "validate_staged_file(" in head, "_do_upload 没有调用统一闸门 —— 单次直传入口会再次绕过所有校验"

    def test_gate_covers_archive_and_pdf(self):
        """闸门内部必须覆盖压缩包与 PDF 页数两类检查"""
        src = Path("app/services/upload_safety.py").read_text(encoding="utf-8")
        start = src.index("def validate_staged_file")
        body = src[start : start + 2000]
        assert "check_archive" in body
        assert "check_pdf_pages" in body

    def test_both_entrypoints_reach_do_upload(self):
        """两条入口（直传 / commit）都要经过 `_do_upload`"""
        src = Path("app/api/upload.py").read_text(encoding="utf-8")
        assert src.count("await _do_upload(") >= 2, "预期两条入口都调用 _do_upload；若有入口直接落库则会绕过闸门"
