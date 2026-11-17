"""上传流水线的两处回归用例（审计 P0 #2 / P1 #5）。

这两条都是**必现故障**，但之前没有用例守着：
- `commit` 不传 `filename` → `guess_type(None)` 抛 TypeError → 500，且 finally 删暂存；
- 云端 4 个 HTTP 调用无 `timeout=` → 一次挂起就全站停摆（Windows 上 pool=solo 杀不掉任务）。
"""

from __future__ import annotations

import inspect
from pathlib import Path

from app.services.mineru import converter


class TestNoHardcodedFilenameDependency:
    def test_guess_type_called_with_display_name_not_request_param(self):
        """`content_type` 必须从 display_name 推导，不能依赖可选的请求参数 filename"""
        src = Path("app/api/upload.py").read_text(encoding="utf-8")
        # 不允许出现 guess_type(filename) —— 那个参数是 Form(None)，不传就 500
        assert "guess_type(filename)" not in src, "commit 端点仍在用可选参数 filename 推 content_type：不传必 500"
        assert "guess_type(display_name)" in src


class TestCloudCallsHaveTimeout:
    def test_every_requests_call_has_timeout(self):
        """converter 里所有 requests.* 调用都必须带 timeout="""
        src = Path("app/services/mineru/converter.py").read_text(encoding="utf-8")
        offenders = []
        for line_no, line in enumerate(src.splitlines(), 1):
            stripped = line.strip()
            if "req.post(" in stripped or "req.get(" in stripped or "req.put(" in stripped:
                if "timeout=" not in stripped:
                    # 跨行调用：看后续两行里有没有 timeout
                    lines = src.splitlines()
                    window = " ".join(lines[line_no - 1 : line_no + 2])
                    if "timeout=" not in window:
                        offenders.append((line_no, stripped[:90]))
        assert not offenders, f"这些请求缺少 timeout：{offenders}"

    def test_module_defines_single_request_timeout(self):
        """上限值必须是模块级常量（便于统一调参）"""
        assert isinstance(converter._REQ_TIMEOUT, int)
        assert 5 <= converter._REQ_TIMEOUT <= 600


class TestConverterSignatureStillWorks:
    def test_public_entry_unchanged(self):
        """加 timeout 不应改变公开入口的签名"""
        sig = inspect.signature(converter.convert)
        assert "timeout" in sig.parameters
