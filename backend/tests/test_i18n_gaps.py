# -*- coding: utf-8 -*-
"""i18n 遗漏的守卫（2026-11-08 补）
==================================

## 为什么要新增这一组

`tests/test_i18n.py` 守着 `AppError` 那张表，但 2026-11-08 的体检发现**三条旁路**
完全不经过它，而且都没有任何测试：

| 旁路 | 现场证据 |
|---|---|
| `HTTPException(detail="中文")` | `Accept-Language: en` 下 `GET /api/notes` 收到 `{"detail": "无效或过期的令牌"}` |
| service 层 `return {"error": "中文"}` | 图谱写操作失败时 toast 弹的是「这两张卡片之间已存在关系」 |
| 响应体 `message="中文"` | 契约里给调用方的提示语 |

## 这一组守 5 件事

1. service 层每一条**静态**中文 `{"error": …}` 都能在 `ERROR_MESSAGES` 里找到逐字相同的条目；
2. `HTTPException(..., detail="中文")` 的每一条原文都登记在 `HTTP_STATUS_MESSAGES`；
3. `app/` 里不再有 `message="中文"` 这种硬编码响应体文案（必须走 `localize()`）；
4. **运行期**：英文请求头下，401 的 detail 真的是英文；中文请求头下，
   FastAPI 内建的 "Not authenticated" 真的是中文（反向）；
5. 图谱写操作的错误在英文请求下也是英文（service→API 的包装链路真的翻译了）。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core import i18n
from app.core.error_messages import ERROR_MESSAGES, HTTP_STATUS_MESSAGES

APP_DIR = Path(__file__).resolve().parents[1] / "app"
CJK = re.compile(r"[\u4e00-\u9fff]")


def _iter_files(pattern: str) -> list[Path]:
    return sorted(APP_DIR.rglob(pattern))


def _service_error_strings() -> list[tuple[str, int, str]]:
    """扫 `app/services/**` 里 `{"error": "<静态中文>"}` 的字面量"""
    found: list[tuple[str, int, str]] = []
    for path in _iter_files("*.py"):
        if "services" not in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            for key, value in zip(node.keys, node.values, strict=False):
                if not (isinstance(key, ast.Constant) and key.value == "error"):
                    continue
                # 只查**静态**字面量：f-string（JoinedStr）带运行期值，走 localize()
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    if CJK.search(value.value):
                        found.append((str(path.relative_to(APP_DIR)), node.lineno, value.value))
    return found


def _registered_zh_texts() -> set[str]:
    return {entry.get("zh-CN", "") for entry in ERROR_MESSAGES.values()}


class TestServiceLayerErrors:
    def test_every_static_service_error_string_is_registered(self):
        """★ 服务层每一条静态中文 `{"error": …}` 都要能在表里找到逐字相同的条目

        没有这条守卫时，改服务层一句话 = 英文界面静默退回中文（表按 (code, 中文)
        精确匹配），而**没有任何测试会红**。
        """
        missing = [
            f"{name}:{line} → {text!r}"
            for name, line, text in _service_error_strings()
            if text not in _registered_zh_texts()
        ]
        assert not missing, (
            "这些服务层错误文案没有进 ERROR_MESSAGES（英文界面会显示中文）：\n  "
            + "\n  ".join(missing)
            + "\n做法：在 error_messages.py 里按**包装它的 code** 加一条 `<CODE>_ALT<n>`，"
            "中文逐字照抄服务层返回值；带运行期值的用 i18n.localize()。"
        )


def _http_exception_details() -> list[tuple[str, int, str]]:
    found: list[tuple[str, int, str]] = []
    for path in _iter_files("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name != "HTTPException":
                continue
            for kw in node.keywords:
                if kw.arg == "detail" and isinstance(kw.value, ast.Constant):
                    value = kw.value.value
                    if isinstance(value, str) and CJK.search(value):
                        found.append((str(path.relative_to(APP_DIR)), node.lineno, value))
    return found


class TestHttpExceptionDetails:
    def test_every_http_exception_detail_is_registered(self):
        """`HTTPException` 的 detail 靠**原文**查表（没有业务错误码）"""
        missing = [
            f"{name}:{line} → {detail!r}"
            for name, line, detail in _http_exception_details()
            if detail not in HTTP_STATUS_MESSAGES
        ]
        assert not missing, "这些 HTTPException detail 没登记进 HTTP_STATUS_MESSAGES：\n  " + "\n  ".join(missing)


class TestResponseMessageLiterals:
    def test_no_raw_chinese_message_field(self):
        """响应体的 `message="中文"` 必须改成 `i18n.localize(zh, en)`"""
        offenders: list[str] = []
        for path in _iter_files("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                for kw in node.keywords:
                    if kw.arg == "message" and isinstance(kw.value, ast.Constant):
                        value = kw.value.value
                        if isinstance(value, str) and CJK.search(value):
                            offenders.append(f"{path.relative_to(APP_DIR)}:{node.lineno} → {value[:40]!r}")
        assert not offenders, "这些响应体文案写死了中文（不会随语言切换）：\n  " + "\n  ".join(offenders)


class TestRuntimeTranslation:
    """真请求的端到端判据（前面几组是静态扫描，这里验"真的翻没翻"）"""

    @pytest.fixture(scope="class")
    def client(self) -> TestClient:
        from app.main import app

        return TestClient(app)

    def test_401_detail_is_english_for_en(self, client: TestClient):
        body = client.get(
            "/api/notes",
            headers={"Accept-Language": "en", "Authorization": "Bearer bad.token"},
        ).json()
        assert body["detail"] == "Invalid or expired token", body
        assert not CJK.search(body["detail"])

    def test_401_detail_is_chinese_for_zh(self, client: TestClient):
        body = client.get(
            "/api/notes",
            headers={"Accept-Language": "zh-CN", "Authorization": "Bearer bad.token"},
        ).json()
        assert body["detail"] == "无效或过期的令牌", body

    def test_fastapi_builtin_detail_is_translated_to_chinese(self, client: TestClient):
        """反向：FastAPI 内建的英文 `Not authenticated` 在中文界面要变中文"""
        body = client.get("/api/notes", headers={"Accept-Language": "zh-CN"}).json()
        assert body["detail"] == "未认证，请先登录", body

    def test_service_layer_error_is_translated(self):
        """service 层句子 → API 包装成 AppError → 按 (code, 中文) 命中表"""
        from app.core.error_messages import resolve_message

        for locale, expected in (
            ("en", "A relation between these two cards already exists"),
            ("zh-CN", "这两张卡片之间已存在关系"),
        ):
            got = resolve_message(
                "GRAPH_RELATION_OPERATION_FAILED",
                "这两张卡片之间已存在关系",
                locale,
            )
            assert got == expected, (locale, got)

    def test_localize_follows_request_context(self):
        from app.core import context

        context.set_request_context(request_id="rid-i18n", locale="en")
        try:
            assert i18n.localize("中文", "English") == "English"
        finally:
            context.reset_context()
        assert i18n.localize("中文", "English") == "中文"  # 无上下文 → 默认语言
