"""ASR 转写路径的 LLM 配置获取（`services/asr/converter._load_llm_config`）

## 这个文件守的是什么

`_load_llm_config` 只做一件事：把 EngramNote 的统一配置翻译成 ASR 那两个
增强功能（标点恢复 / 标题生成）需要的三元组。它的**正常路径**很好验证，
真正值得钉住的是**拿不到配置时怎么办** —— 2026-10-19 修掉的那段兜底。

## 修之前是什么样（这段历史必须留着，否则会被人"顺手加回来"）

原来的 `except` 分支读 `os.environ.get("DEEPSEEK_API_KEY")` /
`os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")` /
`os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")`。

那三个变量**永远拿不到值**：pydantic-settings 读 `.env` 是读进 `Settings`
对象，**不会导出到进程环境**（本文件的 `test_env_vars_are_not_exported`
就是那个实测的固化）。所以兜底的实际效果是
"key 为 None + base_url 落到官方端点"，而它还在日志里说"回退到环境变量"。

危害没有想象中大（调用方有 `and api_key` 守卫，key 为 None 时直接跳过 LLM 步骤），
但它**假装能恢复**。修法是：不给任何替代值，如实返回 `(None, "", "")`。

## 为什么"不提供替代值"是刻意的（而不是偷懒）

给一个"官方 base_url + 空 key"的组合是**危险**的：将来某天有人补上 key 的来源，
就会变成**用 OpenCode 的密钥打官方端点**。而本项目 `backend/.env` 里
那两块共用 `DEEPSEEK_API_KEY` 变量名（靠 `DEEPSEEK_BASE_URL` 区分），
这正是最容易踩的坑 —— 所以宁可不给，也不给一个可能配错的组合。
`test_fallback_never_pairs_a_base_url_with_a_missing_key` 就是这条判据。
"""

from __future__ import annotations

import logging
import os

import pytest

from app.services.asr.converter import _load_llm_config


class TestNormalPath:
    """能读到配置时，原样翻译三元组"""

    def test_returns_the_configured_triple(self):
        api_key, base_url, model = _load_llm_config()

        # 本机 `backend/.env` 配的是什么就返回什么 —— 不写死具体值
        # （写死会让这个测试在任何人换密钥时变红，而它要证明的不是"配了哪把密钥"）
        assert isinstance(api_key, str) or api_key is None
        assert isinstance(base_url, str)
        assert isinstance(model, str)

    def test_matches_get_llm_config_exactly(self):
        """★ 必须与统一配置体系**逐字一致** —— 这里不允许有任何自己的加工

        ASR 是"另一个入口"，最容易出现的问题就是它悄悄用了一套不同的默认值
        （旧的兜底默认 `deepseek-chat` 就是一个：主配置的默认是
        `deepseek-v4-flash`）。这条断言把"同一个真相"钉住。
        """
        from app.config import get_settings

        expected = get_settings().get_llm_config()
        api_key, base_url, model = _load_llm_config()

        assert api_key == expected["api_key"]
        assert base_url == expected["base_url"]
        assert model == expected["model"]

    def test_base_url_is_never_empty_when_a_key_exists(self):
        """★ 有 key 就必须有配套的 base_url

        两者必须**同时**有效：只给 key 不给端点会让请求打到 SDK 的默认地址上
        （而那个地址与密钥的来源多半不匹配）—— 与 `backend/.env` 里
        "认准 BASE_URL，不要看变量名"那条注释是同一件事。
        """
        api_key, base_url, _ = _load_llm_config()

        if api_key:
            assert base_url, "有 api_key 却没有 base_url —— 请求会打到 SDK 默认端点上"


class TestFallback:
    """拿不到配置时的行为（2026-10-19 修掉的那段）"""

    @pytest.fixture
    def broken_settings(self, monkeypatch):
        """让 `get_settings()` 抛异常，模拟配置体系不可用"""
        from app import config

        def _boom():
            raise RuntimeError("模拟：配置体系不可用")

        monkeypatch.setattr(config, "get_settings", _boom)

    def test_returns_empty_triple_instead_of_raising(self, broken_settings):
        """★ 不重抛：转写本身不该因为 LLM 配置读不出来就整体失败"""
        assert _load_llm_config() == (None, "", "")

    def test_fallback_never_pairs_a_base_url_with_a_missing_key(self, broken_settings):
        """★★ 本文件最重要的一条：兜底不许给出"端点 + 空 key"的组合

        见文件头：那样的组合在将来某天会变成"用 OpenCode 的密钥打官方端点"。
        """
        api_key, base_url, model = _load_llm_config()

        assert api_key is None
        assert base_url == "", "兜底不许给 base_url —— 它可能与将来补上的 key 不配套"
        assert model == "", "同上：`deepseek-chat` 这种默认值会掩盖真实的模型配置"

    def test_does_not_fall_back_to_official_deepseek_endpoint(self, broken_settings):
        """★ 明确断言"没有回退到官方端点"（旧实现正是那样）"""
        _, base_url, _ = _load_llm_config()

        assert "api.deepseek.com" not in base_url
        assert "opencode.ai" not in base_url

    def test_logs_an_honest_warning(self, broken_settings, caplog):
        """★ 日志必须如实：不再说"回退到环境变量"（那句话无法兑现）

        而且要**说清影响范围** —— "转写本身不受影响"这句是给排查的人看的：
        没有它，看到 warning 的人会以为整个音视频转写坏了。
        """
        with caplog.at_level(logging.WARNING):
            _load_llm_config()

        text = caplog.text
        assert "标点恢复" in text
        assert "标题生成" in text
        assert "转写本身不受影响" in text
        # 旧的错误说法不许回来
        assert "回退到环境变量" not in text
        # 异常类型要进日志（否则排查时不知道是配置读不出来还是别的）
        assert "RuntimeError" in text


class TestEnvVarsAreNotExported:
    """固化"修这段兜底"的那个实测依据

    这条不是在测产品行为，而是在**守住一个环境事实** ——
    将来若有人把 `.env` 的读取方式改成"真的导出到 os.environ"
    （例如加了 `dotenv.load_dotenv()`），那么"读环境变量"就重新变得可行，
    这段兜底的历史结论也随之失效。那时这条会红，提醒重读本文件的说明。
    """

    def test_env_vars_are_not_exported(self):
        from app.config import get_settings

        settings = get_settings()
        if not settings.deepseek_api_key:
            pytest.skip("本机没有配 DEEPSEEK_API_KEY，这条环境事实无从验证")

        assert "DEEPSEEK_API_KEY" not in os.environ, (
            "pydantic-settings 竟然把 .env 导出到了进程环境 —— "
            "那么 `os.environ.get('DEEPSEEK_API_KEY')` 这类读法重新变得可行，"
            "请重读本文件头部关于那段兜底的说明再决定怎么写"
        )
        assert "DEEPSEEK_BASE_URL" not in os.environ
