# -*- coding: utf-8 -*-
"""插图分发端点 `GET /api/notes/{id}/assets/{path}`（2026-11-09）

## 为什么要单独一组 API 测试

`test_note_assets.py` 守的是**存储层**（写/搬/删/路径判据）。这里守的是
**对外契约**，四条都是"错了会在生产上出事、但在存储层看不出来"的：

| 判据 | 出错后果 |
|---|---|
| 自己的笔记 → 200 且 `Content-Type` 正确 | 图能显示 |
| 别人的笔记 → 404（**不是 403**） | 403 会告诉攻击者"这个 id 存在" |
| 回收站里的笔记 → 404 | 删了还能访问 = 删除没生效 |
| `.svg` / 非法路径 → 415 | 同源加载 svg = 存储型 XSS 面 |
| 不存在的图 → 404 | 与"没有资产"一致，页面降级成占位符 |

沿用本仓库既有做法：`TestClient` + 真 token（`create_access_token`），
**不用** `app.dependency_overrides` —— 全仓没有一处用它，引入新机制会让
"这个测试为什么和别的不一样"变成要解释的问题。
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.models.note import Note, NoteStatus, SourceType
from app.models.user import User
from app.services import note_assets, vault_path
from app.services.auth_service import create_access_token

JPEG_BYTES = b"\xff\xd8\xff\xe0JFIF-probe-jpeg-bytes"


def _client() -> TestClient:
    from app.main import app

    return TestClient(app, client=("198.51.100.77", 9577))


def _auth(user_id: str) -> dict:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


def _make_user(session_factory, *, active: bool = True) -> str:
    uid = str(uuid.uuid4())

    async def _seed():
        async with session_factory() as db:
            db.add(
                User(
                    id=uid,
                    email=f"{uid[:8]}@test.local",
                    username=f"u{uid[:8]}",
                    hashed_password="x",
                    is_active=active,
                )
            )
            await db.commit()

    asyncio.run(_seed())
    return uid


def _make_note(session_factory, user_id: str, *, trashed: bool = False) -> str:
    nid = str(uuid.uuid4())
    stored_prefix = vault_path.trash_prefix(user_id, nid) if trashed else vault_path.inbox_prefix(user_id)

    async def _seed():
        async with session_factory() as db:
            db.add(
                Note(
                    id=nid,
                    user_id=user_id,
                    title="带图笔记",
                    source_type=SourceType.pdf,
                    status=NoteStatus.cleaned,
                    original_file_path=vault_path.source_object(stored_prefix, "paper", ".pdf"),
                    trashed_at=datetime.now(UTC) - timedelta(hours=1) if trashed else None,
                )
            )
            await db.commit()

    asyncio.run(_seed())
    return nid


@pytest.fixture
def seeded(test_db):
    """client 先建（会跑 lifespan），再建用户/笔记 —— 顺序见 test_note_export 的说明"""
    client = _client()
    uid = _make_user(test_db)
    nid = _make_note(test_db, uid)
    prefix = vault_path.inbox_prefix(uid)
    from app.services.storage_service import upload_bytes

    upload_bytes(
        note_assets._markdown_bucket(),
        vault_path.assets_object(prefix, "paper", "images/fig-1.jpg"),
        JPEG_BYTES,
        content_type="image/jpeg",
    )
    return client, uid, nid


class TestAssetEndpoint:
    def test_owner_gets_bytes_and_content_type(self, seeded):
        client, uid, nid = seeded
        resp = client.get(f"/api/notes/{nid}/assets/images/fig-1.jpg", headers=_auth(uid))

        assert resp.status_code == 200, resp.text
        assert resp.content == JPEG_BYTES
        assert resp.headers["content-type"] == "image/jpeg"
        assert resp.headers["cache-control"].startswith("private")
        assert resp.headers["x-content-type-options"] == "nosniff"

    def test_other_user_gets_404_not_403(self, seeded, test_db):
        """别人的笔记必须回 404 —— 403 会泄漏"这个 id 存在" """
        client, _uid, nid = seeded
        intruder = _make_user(test_db)

        resp = client.get(f"/api/notes/{nid}/assets/images/fig-1.jpg", headers=_auth(intruder))
        assert resp.status_code == 404, resp.text
        assert resp.json()["error_code"] == "ASSET_NOT_FOUND"

    def test_trashed_note_is_not_served(self, seeded, test_db):
        client, uid, _nid = seeded
        trashed_nid = _make_note(test_db, uid, trashed=True)
        resp = client.get(f"/api/notes/{trashed_nid}/assets/images/fig-1.jpg", headers=_auth(uid))
        assert resp.status_code == 404

    def test_missing_image_is_404(self, seeded):
        client, uid, nid = seeded
        resp = client.get(f"/api/notes/{nid}/assets/images/nope.jpg", headers=_auth(uid))
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "ASSET_NOT_FOUND"

    @pytest.mark.parametrize(
        "path",
        ["images/evil.svg", "images/note.txt", "images/no-ext"],
    )
    def test_non_whitelisted_type_is_415(self, seeded, path):
        client, uid, nid = seeded
        resp = client.get(f"/api/notes/{nid}/assets/{path}", headers=_auth(uid))
        assert resp.status_code == 415, resp.text
        assert resp.json()["error_code"] == "ASSET_TYPE_UNSUPPORTED"

    def test_traversal_is_rejected(self, seeded):
        """`..` 段：Starlette 会先归一化 URL，**能到这里的就是被接受的形式** ——
        所以这里直接测"归一化后指向别处"的等价写法：多级 `..` 用编码后的点。
        """
        client, uid, nid = seeded
        resp = client.get(
            f"/api/notes/{nid}/assets/images/%2e%2e/images/fig-1.jpg",
            headers=_auth(uid),
        )
        # 路径不安全 → 415；万一被归一化吃掉，也必须是 404（绝不能 200 越界）
        assert resp.status_code in (404, 415), resp.text

    def test_requires_authentication(self, seeded):
        client, _uid, nid = seeded
        resp = client.get(f"/api/notes/{nid}/assets/images/fig-1.jpg")
        assert resp.status_code == 401


class TestSignedToken:
    """★ 签名令牌（2026-11-09）

    浏览器渲染 `<img src>` 时**不会**带 `Authorization` 头（JWT 在
    localStorage，由 fetch 手工加），所以图片必须能靠 URL 里的短期签名访问。
    这一组同时守住"没有凭证仍然 401"这条底线。
    """

    def test_detail_response_carries_assets_token(self, seeded):
        client, uid, nid = seeded
        body = client.get(f"/api/notes/{nid}", headers=_auth(uid)).json()
        token = body.get("assets_token")
        assert token and "." in token, body

    def test_valid_token_works_without_authorization(self, seeded):
        client, uid, nid = seeded
        token = client.get(f"/api/notes/{nid}", headers=_auth(uid)).json()["assets_token"]

        resp = client.get(f"/api/notes/{nid}/assets/images/fig-1.jpg?t={token}")
        assert resp.status_code == 200, resp.text
        assert resp.content == JPEG_BYTES

    def test_token_of_another_note_is_rejected(self, seeded, test_db):
        """令牌按**笔记**签名：拿 A 的令牌读 B 的图必须失败"""
        client, uid, nid = seeded
        other_nid = _make_note(test_db, uid)
        token = client.get(f"/api/notes/{nid}", headers=_auth(uid)).json()["assets_token"]

        resp = client.get(f"/api/notes/{other_nid}/assets/images/fig-1.jpg?t={token}")
        assert resp.status_code in (401, 404), resp.text

    def test_garbage_token_is_401(self, seeded):
        client, _uid, nid = seeded
        resp = client.get(f"/api/notes/{nid}/assets/images/fig-1.jpg?t=123.deadbeef")
        assert resp.status_code == 401, resp.text

    def test_issue_and_verify_roundtrip(self):
        from app.services import note_assets

        token = note_assets.issue_assets_token("note-x")
        assert note_assets.verify_assets_token("note-x", token) is True
        assert note_assets.verify_assets_token("note-y", token) is False

    def test_expired_token_is_rejected(self):
        from app.services import note_assets

        token = note_assets.issue_assets_token("note-x", ttl_seconds=-5)
        assert note_assets.verify_assets_token("note-x", token) is False
