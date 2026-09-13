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
        import io

        src = io.open("app/main.py", encoding="utf-8").read()
        assert "debug=settings.is_dev" in src, "FastAPI 的 debug 没有接在 app_env 上"

    def test_llm_provider_is_not_read_from_debug(self):
        """供应商选择不得再直接读 `debug`（否则解耦是假的）"""
        import io
        import re

        src = io.open("app/config.py", encoding="utf-8").read()
        body = src.split("def get_llm_config", 1)[1].split("\n    def ", 1)[0]
        assert not re.search(r"self\.debug", body), (
            "get_llm_config 仍在读 debug —— 供应商与环境的耦合没有真正切断"
        )
