"""阶段 6.8：错误响应不泄露内部信息 + 启动时打印安全姿态

## 这份测试要证明什么

"不泄露"这件事**没法靠读代码确认**：泄露的形态是某条路由顺手把 `str(exc)`
放进 `detail`，而 `exc` 来自解析库 —— 只要没人试过，它就一直在那里。

因此两处都走**真实代码路径**，不用手抄一份逻辑来断言：

- 上传路由：真的调 `prepare_upload`（真文件、真魔数校验），只把底层
  `get_pdf_page_count` 换成会抛异常的桩；
- 500 出口：用真实的 `ErrorHandlerMiddleware` 包一个会抛异常的路由，
  走完整 HTTP 往返（而不是"patch 一个依赖再假设它生效"——FastAPI 在注册
  路由时已经捕获了依赖对象，patch 模块属性**不会**改变已注册的依赖，
  那样的测试会静默变空转）。

| 要证明的事 | 对应测试 |
|---|---|
| 底层异常文本（含服务器路径）不进响应 | `test_pdf_parse_error_does_not_leak_path` |
| 未知异常 → 500 且不含 traceback/SQL/路径 | `test_unhandled_exception_returns_generic_500` |
| 错误响应带 request_id（详情只进日志，得能对上） | `test_error_carries_request_id` |
| 启动日志写明生效姿态 / dev 与 SQL 日志要告警 | `TestSecurityPosture` |
"""

import io
import logging

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.main import _log_security_posture, app
from app.middleware.error_handler import ErrorHandlerMiddleware

LEAKY = (
    "cannot open D:\\engramnote\\backend\\data\\storage\\u1\\x.pdf: invalid header; "
    "sqlite3.OperationalError: no such table: knowledge_cards"
)


@pytest.mark.asyncio
class TestNoInternalDetailLeak:
    async def test_pdf_parse_error_does_not_leak_path(self, monkeypatch, tmp_path):
        """★ PDF 解析失败时，响应里不得出现服务器路径或解析库的原始报错

        `get_pdf_page_count` 抛出的 ValueError 形如
        `cannot open D:\\...\\x.pdf: invalid header` —— 原样回给客户端
        等于把内部目录结构送给任何能上传文件的人。
        """
        from app.api import upload as upload_mod
        from app.models.user import User

        monkeypatch.setattr(
            upload_mod.pdf_crop, "get_pdf_page_count",
            lambda *a, **k: (_ for _ in ()).throw(ValueError(LEAKY)),
        )

        # 真的构造一个带 PDF 魔数的上传（内容签名校验要求前几字节是 %PDF）
        upload = upload_mod.UploadFile(
            filename="x.pdf", file=io.BytesIO(b"%PDF-1.7\n%%EOF\n"),
        )
        user = User(id="u-leak", email="leak@e.com", username="leak", hashed_password="x")

        with pytest.raises(HTTPException) as exc:
            await upload_mod.prepare_upload(file=upload, current_user=user)

        detail = str(exc.value.detail)
        assert "cannot open" not in detail, "响应里带出了解析库的原始报错"
        assert "engramnote" not in detail and "D:\\" not in detail, "响应里带出了服务器路径"
        assert "sqlite3" not in detail
        assert "解析失败" in detail, "应当给出用户能理解的说明"


class TestErrorEnvelope:
    """500 出口用**真实的中间件** + 真实 HTTP 往返验证"""

    @staticmethod
    def _app_with_boom() -> FastAPI:
        test_app = FastAPI()
        test_app.add_middleware(ErrorHandlerMiddleware)

        @test_app.get("/boom")
        async def _boom():  # pragma: no cover - 由测试触发
            raise RuntimeError(LEAKY)

        return test_app

    def test_unhandled_exception_returns_generic_500(self):
        """★ 未知异常统一 500，且响应体不含 traceback / SQL / 路径"""
        resp = TestClient(self._app_with_boom(), raise_server_exceptions=False).get("/boom")

        assert resp.status_code == 500
        body = resp.text
        for fragment in ("Traceback", "cannot open", "sqlite3", "no such table", "D:\\", "engramnote"):
            assert fragment not in body, f"响应体泄露了内部信息: {fragment}"
        assert "服务器内部错误" in body
        assert resp.json()["error_code"] == "INTERNAL_ERROR"

    def test_error_carries_request_id(self):
        """详情只进日志，就必须让用户能凭 request_id 把它对上"""
        resp = TestClient(self._app_with_boom(), raise_server_exceptions=False).get("/boom")
        assert "request_id" in resp.json()


class TestSecurityPosture:
    def test_security_posture_logged(self, caplog, monkeypatch):
        """启动姿态必须写成一行可检索的日志"""
        import app.config as config_mod
        from app.config import Settings

        prod = Settings(
            app_env="prod", jwt_secret_key="k", deepseek_api_key="k", glm_api_key="k",
        )
        monkeypatch.setattr(config_mod, "get_settings", lambda: prod)
        with caplog.at_level(logging.INFO):
            _log_security_posture()
        text = caplog.text
        assert "安全姿态" in text
        for field in ("app_env=", "LLM=", "SQL 日志=", "traceback 回吐=", "JWT 密钥="):
            assert field in text, f"姿态日志缺少 {field}"

    def test_dev_mode_warns(self, caplog, monkeypatch):
        """★ dev 模式必须显式告警（traceback 回吐 + 自动生成 JWT 密钥都是静默降级）"""
        import app.config as config_mod
        from app.config import Settings

        dev = Settings(
            app_env="dev", jwt_secret_key="k", deepseek_api_key="k", glm_api_key="k",
        )
        monkeypatch.setattr(config_mod, "get_settings", lambda: dev)
        with caplog.at_level(logging.WARNING):
            _log_security_posture()
        assert "开发环境" in caplog.text
        assert "APP_ENV=prod" in caplog.text

    def test_log_sql_warns_even_in_prod(self, caplog, monkeypatch):
        """★ 生产环境开 SQL 日志也要告警（那里面是 bcrypt 哈希与卡片正文）"""
        import app.config as config_mod
        from app.config import Settings

        prod = Settings(
            app_env="prod", log_sql=True, jwt_secret_key="k",
            deepseek_api_key="k", glm_api_key="k",
        )
        monkeypatch.setattr(config_mod, "get_settings", lambda: prod)
        with caplog.at_level(logging.WARNING):
            _log_security_posture()
        assert "SQL 日志" in caplog.text


def test_app_still_importable():
    """把姿态日志接进 lifespan 之后，应用本身必须照常可导入（防低级错误）"""
    assert app is not None
