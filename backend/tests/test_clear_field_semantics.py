# -*- coding: utf-8 -*-
"""
「清空字段」语义（B3）
======================

## 这一组守的是什么

`docs/overhaul-plan.md` 附录 BN.3 把「清空项目描述保存后、重载又出现旧描述」
列为**仅有的两条「确实影响使用」**之一（丙-1）。

根因是**一个值无法表达两种意思**：

```python
# 改造前的 service
if description is not None:      # "没传" 与 "显式清空" 在这里合流
    project.description = description
```

两种请求到达这里时 `description` 都是 `None`：

```json
{"name": "甲"}                        // 没传这个键 ⇒ 期望：不动描述
{"name": "甲", "description": null}    // 显式清空   ⇒ 期望：置空
```

于是用户的清空被**静默丢掉** —— 界面看起来保存了，重载后旧描述还在。

## 修法：用 `model_fields_set` 把"值"表达不了的信息补上

pydantic v2 的 `model_fields_set` 记录**客户端实际发了哪些键**，
这正是"值"无法表达的那部分。API 层据此翻译成一个布尔传给服务层。

## 三条判据（缺一不可）

| 请求 | 期望 | 缺了会怎样 |
|---|---|---|
| 不传 `description` | 描述**不变** | 旧客户端（或部分更新）会**误清空**所有描述 |
| `"description": null` | 描述**置空** | 这就是本 bug 本身 |
| `"description": "有值"` | 描述更新 | 若只判 `in model_fields_set`，有值也会被当成清空 |

第三条是最容易写错的一条：`clear_description` 必须**同时**要求
"键存在" **且** "值是 None"。
"""

from __future__ import annotations

import pytest


async def _seed_project(test_db, *, description: str | None = "原有描述"):
    """建一个用户 + 一个项目"""
    from app.models.project import Project
    from app.models.user import User

    async with test_db() as session:
        session.add(User(id="u1", email="a@x.c", username="a", hashed_password="x"))
        await session.flush()
        session.add(Project(
            id="p1", user_id="u1", name="甲项目", description=description
        ))
        await session.commit()


async def _description(test_db) -> str | None:
    from sqlalchemy import select

    from app.models.project import Project

    async with test_db() as session:
        return (await session.execute(
            select(Project).where(Project.id == "p1")
        )).scalar_one().description


async def _update(test_db, name=None, description=None, **kwargs):
    """跑一次真实的 `project_service.update_project`（作用在 p1/u1 上）"""
    from app.services.project_service import update_project

    async with test_db() as session:
        return await update_project(
            session, "p1", "u1", name, description, **kwargs
        )


class TestServiceLayer:
    """服务层：`clear_description` 与 `description` 的**互斥**关系"""

    @pytest.mark.asyncio
    async def test_explicit_clear_sets_description_to_none(self, test_db):
        """★ 核心：`clear_description=True` ⇒ 描述真的变空"""
        await _seed_project(test_db)
        await _update(test_db, None, None, clear_description=True)
        assert await _description(test_db) is None, "显式清空没有生效"

    @pytest.mark.asyncio
    async def test_omitted_field_leaves_description_untouched(self, test_db):
        """★ 反向断言：不传描述 ⇒ **不动**（旧客户端行为不变）

        没有这条，"修好清空"的代价就是"任何一次改名都把描述清掉了" ——
        那比原 bug 更严重。
        """
        await _seed_project(test_db, description="原有描述")
        await _update(test_db, "新名字", None)
        assert await _description(test_db) == "原有描述", "改名把描述清掉了"
        # 名字确实改了（证明这次调用真的执行了，不是静默失败）
        from sqlalchemy import select

        from app.models.project import Project

        async with test_db() as session:
            assert (await session.execute(
                select(Project).where(Project.id == "p1")
            )).scalar_one().name == "新名字"

    @pytest.mark.asyncio
    async def test_value_wins_over_clear_flag(self, test_db):
        """★ 有值时 `clear_description=True` 也**不许**清空

        `clear_description` 的语义是"我没给值、我要清空"。两者同时给出时
        以值优先 —— 否则一个写错的调用方就会把用户刚输入的描述清掉。
        """
        await _seed_project(test_db, description="旧")
        await _update(test_db, None, "新描述", clear_description=True)
        assert await _description(test_db) == "新描述"

    @pytest.mark.asyncio
    async def test_setting_a_value_still_works(self, test_db):
        """有值时的普通更新（既有行为）"""
        await _seed_project(test_db, description="旧")
        await _update(test_db, None, "新描述")
        assert await _description(test_db) == "新描述"

    @pytest.mark.asyncio
    async def test_clear_from_null_is_idempotent(self, test_db):
        """描述本来就是空时再清空一次不出错（幂等）"""
        await _seed_project(test_db, description=None)
        await _update(test_db, None, None, clear_description=True)
        assert await _description(test_db) is None


class TestModelFieldsSet:
    """API 层的判据：`model_fields_set` 能区分"没传"与"显式 null"

    这三条锁住的是**修法的前提**。若哪天 pydantic 升级后
    `model_fields_set` 的语义变了（或有人改用 `exclude_unset` 之类），
    判据会在**没有报错**的情况下退化成"两种都当成不改" —— 本 bug 原样复发。
    """

    def test_absent_key_is_not_in_fields_set(self):
        from app.schemas.project import ProjectUpdate

        req = ProjectUpdate.model_validate({"name": "甲"})
        assert "description" not in req.model_fields_set
        assert req.description is None  # 值上完全看不出来

    def test_explicit_null_is_in_fields_set(self):
        from app.schemas.project import ProjectUpdate

        req = ProjectUpdate.model_validate({"name": "甲", "description": None})
        assert "description" in req.model_fields_set
        assert req.description is None

    def test_value_is_in_fields_set_and_not_none(self):
        from app.schemas.project import ProjectUpdate

        req = ProjectUpdate.model_validate({"name": "甲", "description": "有值"})
        assert "description" in req.model_fields_set
        assert req.description == "有值"

    def test_the_two_case_rule_from_the_api(self):
        """★ 把 API 里那行判据原样验一遍（三种输入 → 三种结论）"""
        from app.schemas.project import ProjectUpdate

        def clear(req) -> bool:
            return "description" in req.model_fields_set and req.description is None

        assert clear(ProjectUpdate.model_validate({"name": "甲"})) is False
        assert clear(ProjectUpdate.model_validate({"name": "甲", "description": None})) is True
        assert clear(ProjectUpdate.model_validate(
            {"name": "甲", "description": "有值"}
        )) is False


class TestEndpointWiring:
    """★ 端到端：HTTP 请求 → 数据库里的描述

    这一层单测服务层与单测 schema 都覆盖不到 —— 那两段各自正确，
    但**接线错**（比如把 `clear_description` 传成了常量、或忘了传）
    时，只有走一遍端点才能发现。
    """

    def _client(self):
        from fastapi.testclient import TestClient

        from app.main import app

        return TestClient(app, client=("198.51.100.41", 9411))

    def _auth(self, user_id: str = "u1") -> dict:
        from app.services.auth_service import create_access_token

        return {"Authorization": f"Bearer {create_access_token(user_id)}"}

    def test_patch_with_explicit_null_clears_the_description(self, test_db):
        """★ 走 HTTP：`{"description": null}` ⇒ 库里变空"""
        import asyncio

        client = self._client()
        asyncio.run(_seed_project(test_db, description="原有描述"))

        resp = client.patch("/api/projects/p1", json={"description": None},
                            headers=self._auth())

        assert resp.status_code == 200, resp.text
        assert resp.json()["description"] is None
        assert asyncio.run(_description(test_db)) is None

    def test_patch_without_description_keeps_it(self, test_db):
        """★ 走 HTTP：不传 `description` ⇒ 库里**不变**（旧客户端不受影响）"""
        import asyncio

        client = self._client()
        asyncio.run(_seed_project(test_db, description="原有描述"))

        resp = client.patch("/api/projects/p1", json={"name": "改名了"},
                            headers=self._auth())

        assert resp.status_code == 200, resp.text
        assert asyncio.run(_description(test_db)) == "原有描述"

    def test_patch_with_value_updates_it(self, test_db):
        """走 HTTP：给了值 ⇒ 更新（没被当成清空）"""
        import asyncio

        client = self._client()
        asyncio.run(_seed_project(test_db, description="旧"))

        resp = client.patch("/api/projects/p1", json={"description": "新"},
                            headers=self._auth())

        assert resp.status_code == 200, resp.text
        assert asyncio.run(_description(test_db)) == "新"

    def test_clear_then_reload_is_still_empty(self, test_db):
        """★★ BN.3 丙-1 的**原始验收判据**：清空 → 保存 → **重载** → 仍是空的

        这条是把 bug 报告里那句话逐字变成断言。前三条各自验一个分支，
        这条验的是"用户实际会看到的最终状态" —— 改造前它正是红的。
        """
        import asyncio

        client = self._client()
        asyncio.run(_seed_project(test_db, description="原有描述"))

        client.patch("/api/projects/p1", json={"description": None}, headers=self._auth())

        # 重载（用户刷新页面时发生的那次 GET）
        reloaded = client.get("/api/projects", headers=self._auth())
        assert reloaded.status_code == 200, reloaded.text
        projects = reloaded.json()
        items = projects["items"] if isinstance(projects, dict) else projects
        target = next(p for p in items if p["id"] == "p1")
        assert target["description"] is None, (
            f"清空后重载，旧描述又回来了：{target['description']!r} —— BN.3 丙-1 复发"
        )


class TestScopeIsStillNarrow:
    """已拍板：**先只改「项目描述」一个字段**

    另外两处（`api/understanding.py:465-468`、`api/knowledge.py:180-183`）
    等这个语义跑通、确认旧客户端不受影响之后再推广。
    这条守卫防止有人"顺手一起改了"——那会让回归面在无人察觉时扩大。
    """

    def test_other_two_sites_are_untouched(self):
        import pathlib

        backend = pathlib.Path(__file__).resolve().parents[1]
        for rel in ("app/api/understanding.py", "app/api/knowledge.py"):
            src = (backend / rel).read_text(encoding="utf-8")
            assert "clear_description" not in src, (
                f"{rel} 也动用了 clear_description —— 拍板的范围是"
                f"「先只改项目描述一个字段」"
            )
