"""A7 用例：`POST /api/understanding/suggestions`。

报告的验收："3 条里 ≥2 条可点开即有据" —— 那需要人工/评测集，本轮先守可测的部分：
有笔记 → 3 条；无笔记 → 空数组；LLM 失败 → 空数组（不 5xx）；解析要能去掉编号前缀。
"""

from __future__ import annotations

import pytest
from sqlalchemy import insert

from app.models.note import Note, NoteStatus, SourceType
from app.models.user import User

_UID = "user-sugg-1"


async def _seed(db, *, titles: list[str]) -> None:
    await db.execute(
        insert(User).values(id=_UID, email="s@t.local", username="sugg", hashed_password="x", is_active=True)
    )
    for i, t in enumerate(titles):
        await db.execute(
            insert(Note).values(
                id=f"n-sugg-{i}",
                user_id=_UID,
                title=t,
                source_type=SourceType.pdf.value,
                status=NoteStatus.cleaned.value,
                file_size=1,
                original_file_path=f"/n{i}.pdf",
                original_md_path=f"/n{i}.md",
            )
        )
    await db.commit()


def _client():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def _auth(uid: str) -> dict:
    from app.services.auth_service import create_access_token

    return {"Authorization": f"Bearer {create_access_token(uid)}"}


class _FakeLLM:
    def __init__(self, reply: str):
        self.reply = reply

    async def chat(self, *_a, **_kw) -> str:
        return self.reply


@pytest.mark.asyncio
class TestQASuggestions:
    async def test_returns_three_questions_parsed(self, test_db, monkeypatch):
        from app.services import llm_service as llm_mod

        async with test_db() as db:
            await _seed(db, titles=["拉哇水电站运行规程", "油系统运行技术标准"])

        monkeypatch.setattr(
            llm_mod,
            "LLMService",
            lambda: _FakeLLM("1. 水导轴承有几块瓦？\n- 油温报警值是多少？\n3) 顶盖排水泵怎么切换？\n多余的一行"),
        )

        with _client() as client:
            resp = client.post("/api/understanding/suggestions", json={}, headers=_auth(_UID))
        assert resp.status_code == 200, resp.text
        got = resp.json()["suggestions"]
        assert len(got) == 3, got
        # 编号前缀必须被去掉
        assert got[0] == "水导轴承有几块瓦？", got
        assert got[1] == "油温报警值是多少？", got
        assert got[2] == "顶盖排水泵怎么切换？", got

    async def test_no_notes_returns_empty(self, test_db, monkeypatch):
        from app.services import llm_service as llm_mod

        async with test_db() as db:
            await _seed(db, titles=[])

        called = {"n": 0}

        def _boom():
            called["n"] += 1
            return _FakeLLM("不该被调用")

        monkeypatch.setattr(llm_mod, "LLMService", _boom)

        with _client() as client:
            resp = client.post("/api/understanding/suggestions", json={}, headers=_auth(_UID))
        assert resp.status_code == 200
        assert resp.json()["suggestions"] == []
        assert called["n"] == 0, "没有笔记时不该调用 LLM（省一次付费调用）"

    async def test_llm_failure_returns_empty_not_5xx(self, test_db, monkeypatch):
        from app.services import llm_service as llm_mod

        async with test_db() as db:
            await _seed(db, titles=["拉哇水电站运行规程"])

        class _Broken:
            async def chat(self, *_a, **_kw):
                raise RuntimeError("LLM down")

        monkeypatch.setattr(llm_mod, "LLMService", lambda: _Broken())

        with _client() as client:
            resp = client.post("/api/understanding/suggestions", json={}, headers=_auth(_UID))
        assert resp.status_code == 200, resp.text
        assert resp.json()["suggestions"] == []
