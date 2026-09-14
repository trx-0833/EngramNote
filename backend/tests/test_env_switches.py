"""阶段 4.10：环境 / SQL 日志 / LLM 供应商三个开关解耦测试

## 这份测试要证明什么

改造前只有一个 `debug`，同时决定四件事。于是**想单独做其中一件，就必须
接受其余三件** —— 这正是它值得单列一项的原因。测试要盯的正是"独立性"：

| 配置 | 改造前会怎样 | 现在必须怎样 |
|---|---|---|
| 生产 + 要 SQL 日志 | 只能开 debug → 供应商被换成 GLM、traceback 回吐 | SQL 日志开，供应商**仍是 DeepSeek** |
| 开发 + 要用 DeepSeek | 只能关 debug → 失去 SQL 日志、JWT 必须手工配 | 供应商显式指定，其余不受影响 |
| 既有 `.env` 写着 `DEBUG=true` | —— | 仍然按开发环境启动（兼容），但**不再**顺手打开 SQL 日志 |

最后一行是本项唯一的行为变化，也是最容易踩到的一条：升级后
"DEBUG=true 却没有 SQL 日志了"。这是**有意的**（那些日志含 bcrypt 哈希与
卡片正文），因此这里显式钉住它，让变化是被告知的而不是被发现的。
"""

import re
from pathlib import Path

import pytest

from app.config import Settings


def _settings(**kwargs) -> Settings:
    """构造一份完整配置（pydantic 需要 JWT 密钥才能过校验，这里显式给一个）"""
    base = dict(
        jwt_secret_key="test-secret-key-for-unit-tests",
        deepseek_api_key="ds-key", deepseek_model="deepseek-v4-flash",
        deepseek_base_url="https://api.deepseek.com",
        glm_api_key="glm-key", glm_model="glm-4.7-flash",
        glm_base_url="https://open.bigmodel.cn/api/paas/v4",
    )
    base.update(kwargs)
    return Settings(**base)


class TestDefaults:
    def test_defaults_are_production_safe(self):
        """默认必须是"生产安全"的：prod + 不打印 SQL + DeepSeek"""
        s = _settings()
        assert s.app_env == "prod"
        assert s.log_sql is False
        assert s.is_dev is False
        assert s.get_llm_config()["provider"] == "deepseek"

    def test_auto_provider_follows_environment(self):
        """`auto` 保持改造前的行为：dev → GLM，prod → DeepSeek"""
        assert _settings(app_env="dev").get_llm_config()["provider"] == "glm"
        assert _settings(app_env="prod").get_llm_config()["provider"] == "deepseek"


class TestIndependence:
    """★ 本项的全部价值：三个开关互不影响"""

    def test_production_can_log_sql_without_switching_provider(self):
        """★ 生产 + SQL 日志：供应商**不得**被换成 GLM

        改造前这是做不到的：想看 SQL 就得 `debug=True`，而那会把供应商
        换成 GLM、并让 traceback 回吐给客户端。
        """
        s = _settings(app_env="prod", log_sql=True)
        assert s.log_sql is True
        assert s.is_dev is False, "打开 SQL 日志不该把环境变成 dev（traceback 会回吐）"
        assert s.get_llm_config()["provider"] == "deepseek"
        assert s.get_llm_config()["model"] == "deepseek-v4-flash"

    def test_development_can_use_deepseek(self):
        """开发环境显式用 DeepSeek：其余开关不受影响"""
        s = _settings(app_env="dev", llm_provider="deepseek", log_sql=True)
        config = s.get_llm_config()
        assert config["provider"] == "deepseek"
        assert config["api_key"] == "ds-key"
        assert s.log_sql is True and s.is_dev is True

    def test_production_can_use_glm(self):
        """生产用 GLM 同样合法（供应商与环境的耦合被彻底切断）"""
        s = _settings(app_env="prod", llm_provider="glm")
        assert s.get_llm_config()["provider"] == "glm"
        assert s.is_dev is False

    def test_log_sql_does_not_affect_environment(self):
        for log_sql in (True, False):
            s = _settings(app_env="prod", log_sql=log_sql)
            assert s.is_dev is False


class TestLegacyDebug:
    """遗留 `DEBUG=true` 的兼容行为（含那一条有意的变化）"""

    def test_legacy_debug_still_means_dev(self):
        s = _settings(debug=True)
        assert s.is_dev is True
        assert s.app_env == "dev", "既有 .env 写 DEBUG=true 的人仍应拿到开发环境行为"

    def test_legacy_debug_no_longer_enables_sql_logging(self):
        """★ 有意的行为变化：`DEBUG=true` **不再**打开 SQL 日志

        那些日志包含 bcrypt 哈希与知识卡片/题目的正文。改造前它们跟着
        debug 一起来，而"只是想本地跑起来"的人不会预期这件事 ——
        §2.5 E-5 就是这么发生的。现在要 SQL 日志必须显式写 `LOG_SQL=true`。
        """
        s = _settings(debug=True)
        assert s.log_sql is False, "DEBUG=true 又顺手打开了 SQL 日志（含业务数据明文）"

    def test_explicit_provider_wins_over_legacy_debug(self):
        """`LLM_PROVIDER=deepseek` 优先于"dev 就用 GLM"的默认推断"""
        s = _settings(debug=True, llm_provider="deepseek")
        assert s.get_llm_config()["provider"] == "deepseek"


class TestValidation:
    def test_invalid_app_env_rejected(self):
        with pytest.raises(Exception) as exc:
            _settings(app_env="staging")
        assert "APP_ENV" in str(exc.value)

    def test_invalid_provider_rejected(self):
        with pytest.raises(Exception) as exc:
            _settings(llm_provider="openai")
        assert "LLM_PROVIDER" in str(exc.value)

    def test_production_still_requires_jwt_secret(self):
        """拆分之后这条防线不能松：非 dev 且没配密钥 → 启动即失败"""
        with pytest.raises(Exception) as exc:
            Settings(
                app_env="prod", jwt_secret_key="",
                deepseek_api_key="k", glm_api_key="k",
            )
        assert "JWT_SECRET_KEY" in str(exc.value)


class TestWiring:
    """三个开关必须真的接在它们该管的地方（而不是只存在于配置里）"""

    def test_engine_echo_follows_log_sql(self):
        """SQL echo 读 `log_sql`，不读 app_env/debug"""
        import io

        src = io.open("app/database.py", encoding="utf-8").read()
        assert '"echo": settings.log_sql' in src, "引擎的 echo 没有接在 log_sql 上"
        assert "settings.debug" not in src, "database.py 仍在读遗留的 settings.debug"

    def test_fastapi_debug_follows_app_env(self):
        """应用实例的 `debug` 必须等于 `settings.is_dev`（阶段 4.10）

        改造前这里读的是 `main.py` 的**源码文本**（断言里面有
        `debug=settings.is_dev` 这行字）。阶段 0.10 把构造搬进
        `create_app(config)` 工厂之后，"这行字在不在"已经不能表达
        "这个 app 到底绑在谁身上"（换一个变量名照样能过）。
        改成运行时断言：更强 —— 它读的是**真的那个 app 对象**。
        """
        from app.main import app, settings

        assert app.debug is settings.is_dev, "FastAPI 的 debug 没有接在 app_env 上"

    def test_llm_provider_is_not_read_from_debug(self):
        """供应商选择不得再直接读 `debug`（否则解耦是假的）"""
        import io
        import re

        src = io.open("app/config.py", encoding="utf-8").read()
        body = src.split("def get_llm_config", 1)[1].split("\n    def ", 1)[0]
        assert not re.search(r"self\.debug", body), (
            "get_llm_config 仍在读 debug —— 供应商与环境的耦合没有真正切断"
        )


class TestSchemaEndpointGating:
    """阶段 0.10：`/docs`、`/openapi.json`、`/redoc` 的环境闸门

    ## 这份测试要证明什么

    改造前这三条路由在**任何**环境都公开：`FastAPI(...)` 没设
    `docs_url` / `openapi_url` / `redoc_url`，于是一次匿名请求就能拿到
    全部路由、参数名与认证方案（对攻击者是侦察，对本项目是无谓的暴露面）。

    修法是生产姿态下**不注册**这几条路由（见 `main.py` 的
    `_schema_endpoint_kwargs`）。因此测试必须同时钉住两侧：
    生产真的没有了，开发真的还在 —— 只钉一侧的话，"把文档全删掉"
    也能让测试变绿。

    ⚠️ 为什么用 `create_app(cfg)` 现造应用：模块级 `app` 的姿态在 import
    那一刻就冻结了，一个进程只能验证一种。工厂让两种姿态在同一个进程里
    都能被真实地断言（这是 0.10 引入工厂的直接原因）。
    """

    @staticmethod
    def _app_and_client(cfg: Settings):
        """按给定配置造一个应用与它的进程内客户端（不起服务、不占端口）"""
        from fastapi.testclient import TestClient

        from app.main import create_app

        app = create_app(cfg)
        return app, TestClient(app)

    def test_production_hides_schema_endpoints(self):
        """★ 生产姿态：三条路由都不存在（404，与任意未知路径无法区分）"""
        app, client = self._app_and_client(_settings(app_env="prod", debug=False))
        assert (app.docs_url, app.redoc_url, app.openapi_url) == (None, None, None)

        for path in ("/docs", "/openapi.json", "/redoc"):
            resp = client.get(path)
            assert resp.status_code == 404, (
                f"{path} 在生产姿态仍可访问（{resp.status_code}）—— "
                "文档与 schema 在公网可达等于把 API 清单送给扫描器"
            )
            lowered = resp.text.lower()
            assert "swagger" not in lowered and "redoc" not in lowered

    def test_development_keeps_schema_endpoints(self):
        """★ 开发姿态：三条路由照旧可用（这是人操作 API 的方式）"""
        _, client = self._app_and_client(_settings(app_env="dev", debug=False))

        docs = client.get("/docs")
        assert docs.status_code == 200, docs.text
        assert "/openapi.json" in docs.text, "/docs 页面没有指向 schema"

        schema = client.get("/openapi.json")
        assert schema.status_code == 200, schema.text
        assert "/api/notes" in schema.json()["paths"]

        assert client.get("/redoc").status_code == 200

    def test_legacy_debug_true_also_unlocks_docs(self):
        """`DEBUG=true`（遗留开关）等价于 `APP_ENV=dev`，闸门也要跟着开

        否则老 `.env` 的使用者会遇到"本地起得来、但 /docs 没了"这种
        与本次改动无关的困惑。
        """
        _, client = self._app_and_client(_settings(debug=True))
        assert client.get("/docs").status_code == 200

    def test_in_process_schema_survives_production_posture(self):
        """★ 关掉的是 HTTP 路由，不是 schema 生成能力

        `scripts/dump_openapi.py` 走**进程内**的 `app.openapi()`，
        不经过 HTTP 路由。若这里变成空路径，前端的类型/客户端生成会
        静默断掉 —— "为了关文档而弄坏生成"是这次改动最可能的副作用。
        """
        app, _ = self._app_and_client(_settings(app_env="prod", debug=False))
        spec = app.openapi()
        assert len(spec["paths"]) >= 100, (
            f"生产姿态下 app.openapi() 只有 {len(spec['paths'])} 条路径 —— "
            "dump_openapi.py（前端生成的输入）会失效"
        )
        assert "/api/notes" in spec["paths"]
        assert "/ready" in spec["paths"], "就绪端点应当在契约里（与 /health 同等）"

    def test_schema_is_identical_in_both_postures(self):
        """环境只决定"路由是否暴露"，不决定**契约内容**

        两种姿态生成的 schema 必须逐字节相同，否则"生产关文档"就等于
        悄悄改变了对外的 API 契约（前端生成会随环境漂移）。
        """
        from app.main import create_app

        prod = create_app(_settings(app_env="prod", debug=False)).openapi()
        dev = create_app(_settings(app_env="dev", debug=False)).openapi()
        assert prod == dev


class TestEnvExampleTemplateAgreesWithCode:
    """`.env.example` 必须与代码默认值一致（阶段 0.10 修的是它自相矛盾）

    ## 这份测试要证明什么

    模板里曾经躺着一条**激活**的 `DEBUG=true`，还配着一句早已失效的说明
    （"DEBUG=true 时使用 GLM，false 时使用 DeepSeek"）—— 而 4.10 之后
    `debug` 只是遗留别名，供应商由 `LLM_PROVIDER` 决定。模板是**唯一**
    会被照抄的东西（`cp .env.example .env`），所以它自相矛盾时，
    后果是"照文档做的人得到与文档不同的行为"。

    判据是机械的两条：

    1. 每条**未注释**的赋值都必须是 `Settings` 的字段名（拼错即失败）；
    2. 其值必须等于该字段在代码里的默认值。

    第 2 条背后的约定是：**模板等于默认值**。想改行为请写进自己的 `.env`，
    而不是改模板 —— 改模板等于改"新部署的默认行为"，而那种改动
    没有任何测试看得见（这正是 `DEBUG=true` 能潜伏这么久的原因）。
    """

    #: 形如 `KEY=value` 的赋值行（注释行在前面就被跳过了）
    _ASSIGNMENT = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")

    @staticmethod
    def _template_path() -> Path:
        return Path(__file__).resolve().parent.parent / ".env.example"

    def _active_assignments(self) -> list[tuple[int, str, str]]:
        """返回 [(行号, KEY, value)]，只含**未注释且非空行**的赋值"""
        text = self._template_path().read_text(encoding="utf-8")
        found: list[tuple[int, str, str]] = []
        for lineno, raw in enumerate(text.splitlines(), start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            match = self._ASSIGNMENT.match(line)
            if match:
                found.append((lineno, match.group(1), match.group(2).strip()))
        return found

    def test_template_exists_and_is_not_empty(self):
        """防空转守卫：上面两条断言在"文件读不到"时会变成空转"""
        assert self._template_path().is_file(), f"找不到 {self._template_path()}"
        assignments = self._active_assignments()
        assert len(assignments) >= 10, (
            f"只解析到 {len(assignments)} 条激活赋值 —— 解析方式可能失效了"
        )

    def test_active_assignments_are_real_settings_fields(self):
        unknown = [
            (lineno, key) for lineno, key, _ in self._active_assignments()
            if key.lower() not in Settings.model_fields
        ]
        assert not unknown, (
            "`.env.example` 里有 Settings 不认识的字段（拼错了？改了名没同步？）: "
            f"{unknown}"
        )

    def test_active_assignments_match_code_defaults(self):
        """★ 这条断言就是本轮修的缺陷（模板写着 DEBUG=true，代码默认 False）"""
        mismatched = []
        for lineno, key, value in self._active_assignments():
            default = Settings.model_fields[key.lower()].default
            if str(default).strip().lower() != value.lower():
                mismatched.append(
                    f"第 {lineno} 行 {key}={value}（代码默认 {default!r}）"
                )
        assert not mismatched, (
            "`.env.example` 的激活项与代码默认值不一致 —— 照抄模板的人会得到"
            "与代码不同的行为：\n  " + "\n  ".join(mismatched)
        )

    def test_legacy_debug_is_not_enabled_by_the_template(self):
        """★ 模板不得再提供**激活**的 `DEBUG=true`（它是遗留开关，见附录 AK）"""
        text = self._template_path().read_text(encoding="utf-8")
        assert not re.search(r"(?m)^\s*DEBUG\s*=\s*(true|1|yes)\s*$", text, re.IGNORECASE), (
            "`.env.example` 又出现了激活的 DEBUG=true —— 它是遗留开关，"
            "新部署应当用 APP_ENV / LOG_SQL / LLM_PROVIDER"
        )
        # 但必须**说明**它是什么：照抄旧 .env 的人要能看到"为什么还留着它"
        assert "遗留" in text, "模板没有说明 DEBUG 是遗留开关"
        assert "APP_ENV" in text and "LOG_SQL" in text and "LLM_PROVIDER" in text, (
            "模板没有给出替代 DEBUG 的三个开关"
        )
