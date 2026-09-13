"""阶段 6.1 / 6.2：密码策略、bcrypt cost、登录时序对齐

## 这份测试要证明什么

这三件事都属于"**没有测试就会静默回退**"的安全属性：

| 属性 | 回退了会怎样 | 对应测试 |
|---|---|---|
| 弱密码被拒 | 撞库字典第一梯队就能攻破账号 | `TestPasswordPolicy` |
| 超长密码被拒（而不是静默截断） | 用户以为的"超长密码"只有前 72 字节有效 | `test_rejects_over_72_bytes` |
| 只约束注册、不追溯存量 | 存量短密码用户被锁在门外 | `test_existing_weak_password_can_still_login` |
| bcrypt cost 显式可控 | 部署方无法按硬件提升爆破成本 | `test_hash_uses_configured_rounds` |
| 邮箱不存在也跑 bcrypt | 响应耗时可精确枚举已注册邮箱 | `test_login_runs_bcrypt_for_unknown_email` |

最后一类是 6.2 的核心：它**已经在实现里**（`_DUMMY_HASH`），但此前没有任何
测试 —— 一条只有注释守护的安全属性，下次重构就会被顺手删掉。
"""

import re
import uuid

import pytest
from fastapi.testclient import TestClient

from app.services.password_policy import (
    BCRYPT_MAX_BYTES,
    MIN_PASSWORD_LENGTH,
    validate_password,
)


class TestPasswordPolicy:
    def test_accepts_reasonable_passwords(self):
        for password in ("ContractPass123!", "浮充与均充的安全密码123", "x7#Kp9$Lm2"):
            assert validate_password(password) is None, f"{password} 被误拒"

    def test_rejects_too_short(self):
        reason = validate_password("Ab1!xyz")
        assert reason is not None and str(MIN_PASSWORD_LENGTH) in reason

    def test_rejects_over_72_bytes(self):
        """★ bcrypt 只取前 72 字节 —— 与其静默截断，不如拒绝并说明

        中文 3 字节/字：25 个字就是 75 字节，第 25 个字之后**根本不参与校验**。
        用户以为设了超长密码，实际有效长度只有前 72 字节。
        """
        short_enough = "密码" * 12          # 24 字 × 3 字节 = 72 字节
        too_long = "密码" * 13              # 26 字 × 3 字节 = 78 字节
        assert len(short_enough.encode("utf-8")) == BCRYPT_MAX_BYTES
        assert validate_password(short_enough) is None
        reason = validate_password(too_long)
        assert reason is not None and "字节" in reason

    def test_rejects_all_digits(self):
        assert validate_password("1234567890") is not None

    def test_rejects_single_repeated_char(self):
        assert validate_password("aaaaaaaaaa") is not None

    def test_rejects_common_passwords_including_obfuscated(self):
        assert validate_password("password") is not None
        assert validate_password("Password123") is not None
        # 全角数字经 NFKC 归一化后仍是弱口令
        assert validate_password("１２３４５６７８") is not None

    def test_rejects_password_containing_username(self):
        assert validate_password("alice2026x", username="alice") is not None
        # 太短的用户名不参与判断（否则 "ab" 会误伤大量正常密码）
        assert validate_password("ab2026xyz", username="ab") is None

    def test_rejects_password_containing_full_email(self):
        assert validate_password("x-bob@example.com-x", email="bob@example.com") is not None

    def test_does_not_reject_password_sharing_a_word_with_email(self):
        """★ 刻意**不**匹配邮箱 @ 之前的部分：那会造成大量误拒

        本轮实测踩到：邮箱 `contract@example.com` 配 `ContractPass123!`
        被自己的规则拒了。局部字符串重合不等于可被社工猜中，
        而误拒的代价是用户被赶去挑 `xxx123!` 这类更弱、更好猜的密码。
        """
        assert validate_password("ContractPass123!", email="contract@example.com") is None

    def test_rejects_blank(self):
        assert validate_password("        ") is not None


class TestBcryptCost:
    def test_hash_uses_configured_rounds(self):
        """★ cost 必须来自配置：它决定离线爆破成本，应当是部署方的显式选择"""
        from app.config import get_settings
        from app.services.auth_service import hash_password

        hashed = hash_password("ContractPass123!")
        match = re.match(r"^\$2[aby]\$(\d+)\$", hashed)
        assert match, f"不是合法的 bcrypt 哈希: {hashed[:12]}"
        assert int(match.group(1)) == get_settings().bcrypt_rounds

    def test_dummy_hash_uses_same_cost(self):
        """时序对齐用的假哈希必须与真实哈希同 cost —— 否则它重新变成侧信道"""
        from app.config import get_settings
        from app.services.auth_service import _DUMMY_HASH

        cost = int(re.match(r"^\$2[aby]\$(\d+)\$", _DUMMY_HASH).group(1))
        assert cost == get_settings().bcrypt_rounds


@pytest.mark.asyncio
class TestTimingEqualizer:
    """6.2：邮箱不存在时也要跑一次 bcrypt"""

    async def test_login_runs_bcrypt_for_unknown_email(self, test_db, monkeypatch):
        from app.services import auth_service

        calls = {"n": 0}
        real = auth_service.verify_password

        def spy(password, hashed):
            calls["n"] += 1
            return real(password, hashed)

        monkeypatch.setattr(auth_service, "verify_password", spy)
        async with test_db() as db:
            user = await auth_service.authenticate_user(db, "nobody@example.com", "whatever123")
        assert user is None
        assert calls["n"] == 1, "邮箱不存在时没有跑 bcrypt —— 响应耗时可枚举账号"

    async def test_login_runs_bcrypt_for_wrong_password(self, test_db):
        from app.models.user import User
        from app.services.auth_service import authenticate_user, hash_password

        async with test_db() as db:
            db.add(User(
                id=str(uuid.uuid4()), email="real@example.com", username="realuser",
                hashed_password=hash_password("ContractPass123!"), is_active=True,
            ))
            await db.commit()
            assert await authenticate_user(db, "real@example.com", "wrong-pass-123") is None
            assert await authenticate_user(db, "real@example.com", "ContractPass123!") is not None


class TestRegistrationEndpoint:
    """接口层：弱密码返回 422 且**给出理由**（而不是笼统的"参数错误"）"""

    _ip_seq = 700

    @classmethod
    def _client(cls) -> TestClient:
        from app.main import app

        cls._ip_seq += 1
        return TestClient(app, client=(f"203.0.113.{cls._ip_seq % 250}", 9500))

    @staticmethod
    def _body(password: str) -> dict:
        suffix = uuid.uuid4().hex[:8]
        return {"email": f"pw{suffix}@example.com", "username": f"pw{suffix}", "password": password}

    def test_weak_password_rejected_with_reason(self):
        resp = self._client().post("/api/auth/register", json=self._body("12345678"))
        assert resp.status_code == 422
        assert "数字" in resp.text, resp.text

    def test_short_password_rejected(self):
        resp = self._client().post("/api/auth/register", json=self._body("Ab1!xy"))
        assert resp.status_code == 422

    def test_strong_password_accepted(self):
        resp = self._client().post("/api/auth/register", json=self._body("ContractPass123!"))
        assert resp.status_code in (200, 201), resp.text

    def test_existing_weak_password_can_still_login(self, test_db):
        """★ 策略**不追溯**既有账号：短密码的老用户必须还能登进来

        追溯应用等于在用户毫无准备时锁死账号 —— 而这些人往往正是最需要
        先登进来改密码的人。
        """
        import asyncio

        from app.models.user import User
        from app.services.auth_service import authenticate_user, hash_password

        async def _run():
            async with test_db() as db:
                db.add(User(
                    id=str(uuid.uuid4()), email="legacy@example.com", username="legacyuser",
                    hashed_password=hash_password("123456"), is_active=True,
                ))
                await db.commit()
                return await authenticate_user(db, "legacy@example.com", "123456")

        assert asyncio.get_event_loop_policy().new_event_loop().run_until_complete(_run()) is not None

    def test_registration_does_not_reveal_which_field_is_taken(self, test_db):
        """6.2 的另一半：邮箱/用户名冲突文案统一，不提供免费的枚举接口"""
        import asyncio

        from app.models.user import User
        from app.services.auth_service import register_user
        from app.schemas.user import UserRegisterRequest

        async def _run():
            async with test_db() as db:
                db.add(User(
                    id=str(uuid.uuid4()), email="taken@example.com", username="takenuser",
                    hashed_password="x", is_active=True,
                ))
                await db.commit()
                reasons = []
                for req in (
                    UserRegisterRequest(email="taken@example.com", username="fresh1", password="ContractPass123!"),
                    UserRegisterRequest(email="fresh@example.com", username="takenuser", password="ContractPass123!"),
                ):
                    with pytest.raises(ValueError) as exc:
                        await register_user(db, req)
                    reasons.append(str(exc.value))
                return reasons

        reasons = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(_run())
        assert reasons[0] == reasons[1], f"两种冲突给出了不同文案：{reasons}"
