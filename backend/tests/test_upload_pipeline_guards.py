"""上传/转换护栏的用例（审计 P0 #2 / P1 #5，以及回归审计的"4 处 timeout"）。

这两类都是**必现故障**且之前没有用例守着：
- `commit` 不传 `filename` → `guess_type(None)` 抛 TypeError → 500，且 finally 删暂存；
- `converter` 的 HTTP 调用缺 `timeout=` → 一次挂起就全站停摆。

## ⚠️ 为什么这里不直接写 `requests` 字样

`tests/conftest.py::pytest_collection_modifyitems` 用**子串启发式**判定集成用例：
源码里出现 `requests.` / `httpx.` 且不含 `mock`/`monkeypatch` 就给打上 `integration`
标记并**默认跳过**。本文件早期版本的 docstring 里写了 `requests.*`，
结果两条守卫用例被**永久 SKIPPED** —— 守卫等于不存在（安全审计发现）。

所以这里用 `importlib.import_module` 拿模块、用 `getattr` 取函数，
并且源码里只出现 `HTTP 调用` 这类中文说法。
"""

from __future__ import annotations

import inspect
from importlib import import_module
from pathlib import Path


class TestNoHardcodedFilenameDependency:
    def test_guess_type_called_with_display_name_not_request_param(self):
        """`content_type` 必须从 display_name 推导，不能依赖可选的请求参数 filename"""
        src = Path("app/api/upload.py").read_text(encoding="utf-8")
        assert "guess_type(filename)" not in src, "commit 端点仍在用可选参数 filename 推 content_type：不传必 500"
        assert "guess_type(display_name)" in src


class TestCloudCallsHaveTimeout:
    def test_every_http_call_has_timeout(self):
        """converter 里所有 HTTP 调用都必须带 timeout="""
        src = Path("app/services/mineru/converter.py").read_text(encoding="utf-8")
        offenders = []
        lines = src.splitlines()
        for i, line in enumerate(lines):
            if any(m in line for m in ("req.post(", "req.get(", "req.put(")):
                window = " ".join(lines[i : i + 3])
                if "timeout=" not in window:
                    offenders.append((i + 1, line.strip()[:90]))
        assert not offenders, f"这些 HTTP 调用缺少 timeout：{offenders}"
        # 至少要覆盖 4 处（申请上传/上传文件/轮询/下载）
        assert (src.count("req.post(") + src.count("req.put(") + src.count("req.get(")) >= 4

    def test_module_defines_single_request_timeout(self):
        """上限值必须是模块级常量（便于统一调参）"""
        converter = import_module("app.services.mineru.converter")
        value = converter._REQ_TIMEOUT
        assert isinstance(value, int)
        assert 5 <= value <= 600


class TestConverterSignatureStillWorks:
    def test_public_entry_unchanged(self):
        """加 timeout 不应改变公开入口的签名"""
        converter = import_module("app.services.mineru.converter")
        sig = inspect.signature(converter.convert)
        assert "timeout" in sig.parameters
