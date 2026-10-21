# -*- coding: utf-8 -*-
"""
题目编辑 / 删除（B5）
======================

## 为什么需要这一组

计划里说 B5 的两件事"完全未实现"，并要求**开工前先用代码实测确认**。
实测（`grep '@router.' app/api/understanding.py`）：

```
448: @router.put("/cards/{card_id}")      ← 卡片能改
489: @router.delete("/cards/{card_id}")   ← 卡片能删
780: @router.get("/{note_id}/questions")  ← 题目只能看
828: @router.get("/questions")            ← 题目只能看
```

**卡片有 PUT/DELETE，题目没有。** 后果：模型出一道坏题时用户**没有任何入口**
修它，只能整篇"重新理解"（那会删掉该笔记的全部卡片/题目/复习记录）。
而 A2/A3′ 重跑之后题库会整批换掉 —— 没有审核入口就无从对比新旧。

## 这一组最盯的是什么：**级联**

删除一道题要清三张表，而其中最隐蔽的是 `review_states`：

它的定位键是 `(user_id, item_type, item_id)`，`item_id` 是**普通字符串**
（没有外键）。所以数据库**不会**替你级联 —— 漏了它，孤儿行会一直留在
"到期队列"里，用户看到一个**点不开的复习项**，而且没有任何报错。

这属于"错了不会报错、只会多出看不见的脏数据"那一类，所以要有专门用例。

## 为什么编辑**不**重置复习进度

`models/review_state.py` 开头那条原则：内容可替换、学习历史不该被替换。
用户改的可能是解析里的错字 —— 那不该让复习了一个月的进度归零。
"""

from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient


def _client() -> TestClient:
    from app.main import app

    return TestClient(app, client=("198.51.100.51", 9511))


def _auth(user_id: str = "u1") -> dict:
    from app.services.auth_service import create_access_token

    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


def _seed(test_db, *, question_type: str = "choice", options=None):
    """建用户 + 笔记 + 卡片 + **一道题**（可选带复习记录与复习状态）"""
    import asyncio
    from datetime import datetime, timezone

    from app.models.knowledge_card import CardType, KnowledgeCard
    from app.models.note import Note, NoteStatus, SourceType
    from app.models.quiz_item import DifficultyLevel, QuestionType, QuizItem
    from app.models.user import User

    def _go():
        async def _inner():
            async with test_db() as session:
                session.add(User(id="u1", email="a@x.c", username="a", hashed_password="x"))
                await session.flush()
                session.add(Note(
                    id="n1", user_id="u1", title="笔记标题", source_type=SourceType.pdf,
                    status=NoteStatus.cleaned, file_size=1,
                    original_file_path="/u1/n1.pdf", original_md_path="/u1/n1.md",
                ))
                await session.flush()
                session.add(KnowledgeCard(
                    id="c1", user_id="u1", note_id="n1", card_type=CardType.concept,
                    title="卡片", content="内容", has_embedding=False,
                ))
                await session.flush()
                session.add(QuizItem(
                    id="q1", user_id="u1", card_id="c1", note_id="n1",
                    question_type=QuestionType(question_type),
                    difficulty=DifficultyLevel.medium,
                    question="原题干预设",
                    answer="原答案预设",
                    options=json.dumps(options, ensure_ascii=False) if options else None,
                    explanation="原解析预设",
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                ))
                await session.commit()

        asyncio.run(_inner())

    _go()


def _seed_review_artifacts(test_db, *, quiz_id: str = "q1"):
    """给这道题造一条复习记录 + 一条复习状态（证明删除会级联）"""
    import asyncio
    from datetime import datetime, timezone

    from app.models.review_log import ReviewLog
    from app.models.review_state import ITEM_TYPE_CARD, ITEM_TYPE_QUIZ, ReviewState

    def _go():
        async def _inner():
            async with test_db() as session:
                session.add(ReviewLog(
                    id="rl1", user_id="u1", quiz_id=quiz_id, quality=4,
                    # 列名是 `review_at`（不是 `reviewed_at`）—— 实测照名字猜会得到
                    # `TypeError: 'reviewed_at' is an invalid keyword argument`
                    review_at=datetime.now(timezone.utc),
                ))
                session.add(ReviewState(
                    user_id="u1", item_type=ITEM_TYPE_QUIZ, item_id=quiz_id,
                    interval_days=3, repetition=2, easiness_factor=2.5,
                ))
                # 同名的 **card** 状态：删除题目**不能**碰它（反向断言用）
                session.add(ReviewState(
                    user_id="u1", item_type=ITEM_TYPE_CARD, item_id="c1",
                    interval_days=7, repetition=5, easiness_factor=2.6,
                ))
                await session.commit()

        asyncio.run(_inner())

    _go()


def _count(test_db, model, **filters) -> int:
    import asyncio

    from sqlalchemy import func, select

    async def _inner() -> int:
        async with test_db() as session:
            stmt = select(func.count()).select_from(model)
            for k, v in filters.items():
                stmt = stmt.where(getattr(model, k) == v)
            return int((await session.execute(stmt)).scalar_one())

    return asyncio.run(_inner())


def _quiz_row(test_db, quiz_id: str = "q1"):
    import asyncio

    from sqlalchemy import select

    from app.models.quiz_item import QuizItem

    async def _inner():
        async with test_db() as session:
            return (await session.execute(
                select(QuizItem).where(QuizItem.id == quiz_id)
            )).scalars().first()

    return asyncio.run(_inner())


class TestEdit:
    """PUT /api/understanding/questions/{quiz_id}"""

    def test_edit_updates_all_four_fields(self, test_db):
        """★ 验收判据：题干 / 选项 / 答案 / 解析 都能改并落库"""

        client = _client()
        _seed(test_db, options=["A.甲", "B.乙"])

        resp = client.put(
            "/api/understanding/questions/q1",
            json={
                "question": "改过的题干",
                "answer": "改过的答案",
                "options": ["A.甲", "B.乙", "C.丙"],
                "explanation": "改过的解析",
            },
            headers=_auth(),
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["question"] == "改过的题干"
        assert body["answer"] == "改过的答案"
        assert body["explanation"] == "改过的解析"

        row = _quiz_row(test_db)
        assert row.question == "改过的题干"
        assert json.loads(row.options) == ["A.甲", "B.乙", "C.丙"]
        assert row.explanation == "改过的解析"

    def test_edit_can_clear_the_explanation(self, test_db):
        """★ PUT 语义下 `explanation=null` 明确表示**清空**（不是"没传"）

        这正是 B3 那个坑的反面：B3 用 PATCH，`None` 有歧义，得靠
        `model_fields_set` 补信息；这里用 PUT 全量替换，歧义从设计上就不存在。
        """

        client = _client()
        _seed(test_db, options=["A.甲", "B.乙"])

        resp = client.put(
            "/api/understanding/questions/q1",
            json={"question": "题干", "answer": "答案", "options": ["A.甲", "B.乙"],
                  "explanation": None},
            headers=_auth(),
        )

        assert resp.status_code == 200, resp.text
        assert _quiz_row(test_db).explanation is None

    def test_edit_does_not_touch_review_progress(self, test_db):
        """★ 编辑**不**重置复习进度（改错字不该让复习一个月的进度归零）

        `models/review_state.py` 开头那条原则：内容可替换、学习历史不该被替换。
        """

        from app.models.review_state import ITEM_TYPE_QUIZ, ReviewState

        client = _client()
        _seed(test_db, options=["A.甲", "B.乙"])
        _seed_review_artifacts(test_db)

        client.put(
            "/api/understanding/questions/q1",
            json={"question": "新题干", "answer": "新答案",
                  "options": ["A.甲", "B.乙"], "explanation": None},
            headers=_auth(),
        )

        async def _state():
            from sqlalchemy import select

            async with test_db() as session:
                return (await session.execute(
                    select(ReviewState).where(
                        ReviewState.item_type == ITEM_TYPE_QUIZ,
                        ReviewState.item_id == "q1",
                    )
                )).scalars().first()

        state = asyncio.run(_state())
        assert state is not None, "编辑题目把复习状态删了"
        assert state.interval_days == 3 and state.repetition == 2

    def test_choice_question_needs_at_least_two_options(self, test_db):
        """★ 选择题选项少于 2 个 ⇒ 400（少于 2 个无法区分对错）"""

        client = _client()
        _seed(test_db, options=["A.甲", "B.乙"])

        resp = client.put(
            "/api/understanding/questions/q1",
            json={"question": "题干", "answer": "答案", "options": ["A.甲"]},
            headers=_auth(),
        )

        assert resp.status_code == 400, resp.text
        assert "选项" in resp.json()["detail"] if isinstance(resp.json(), dict) else True

    def test_non_choice_options_are_dropped(self, test_db):
        """★ 非选择题传了选项 ⇒ 存成 None（存了但永远不读的字段是噪音）"""

        client = _client()
        _seed(test_db, question_type="fill_blank")

        resp = client.put(
            "/api/understanding/questions/q1",
            json={"question": "填空 ____ 题", "answer": "答案",
                  "options": ["不该被保存的选项"]},
            headers=_auth(),
        )

        assert resp.status_code == 200, resp.text
        assert _quiz_row(test_db).options is None

    def test_edit_returns_note_title(self, test_db):
        """响应带 `note_title`：前端直接用它替换列表里那一行，不必再拉一次列表"""

        client = _client()
        _seed(test_db, options=["A.甲", "B.乙"])

        resp = client.put(
            "/api/understanding/questions/q1",
            json={"question": "题干", "answer": "答案", "options": ["A.甲", "B.乙"]},
            headers=_auth(),
        )

        assert resp.json()["note_title"] == "笔记标题"

    def test_empty_question_is_rejected(self, test_db):
        """空题干是死题 ⇒ 422（schema 的 min_length）"""

        client = _client()
        _seed(test_db, options=["A.甲", "B.乙"])

        resp = client.put(
            "/api/understanding/questions/q1",
            json={"question": "", "answer": "答案", "options": ["A.甲", "B.乙"]},
            headers=_auth(),
        )

        assert resp.status_code == 422, resp.text

    def test_missing_quiz_returns_404(self, test_db):

        client = _client()
        _seed(test_db, options=["A.甲", "B.乙"])

        resp = client.put(
            "/api/understanding/questions/不存在的题",
            json={"question": "题干", "answer": "答案", "options": ["A.甲", "B.乙"]},
            headers=_auth(),
        )

        assert resp.status_code == 404, resp.text

    def test_other_users_quiz_also_returns_404_not_403(self, test_db):
        """★ 别人的题报 404 而不是 403 —— 403 会泄露"这个 id 存在"

        与 `cards/{card_id}` 同一口径。这条用另一个用户身份请求。
        """

        client = _client()
        _seed(test_db, options=["A.甲", "B.乙"])
        # 另一个用户（与他自己的 token）
        from app.models.user import User

        async def _add_other():
            async with test_db() as session:
                session.add(User(id="u2", email="b@x.c", username="b", hashed_password="x"))
                await session.commit()

        asyncio.run(_add_other())

        resp = client.put(
            "/api/understanding/questions/q1",
            json={"question": "题干", "answer": "答案", "options": ["A.甲", "B.乙"]},
            headers=_auth("u2"),
        )

        assert resp.status_code == 404, resp.text


class TestDelete:
    """DELETE /api/understanding/questions/{quiz_id}"""

    def test_delete_removes_the_quiz(self, test_db):

        from app.models.quiz_item import QuizItem

        client = _client()
        _seed(test_db, options=["A.甲", "B.乙"])

        resp = client.delete("/api/understanding/questions/q1", headers=_auth())

        assert resp.status_code == 204, resp.text
        assert _count(test_db, QuizItem) == 0

    def test_delete_cascades_review_logs(self, test_db):
        """★ 级联 ①：`review_logs` 里指向它的行要删掉

        不删的后果：复习历史指向不存在的题，掌握度统计仍把它们算进去。
        """

        from app.models.review_log import ReviewLog

        client = _client()
        _seed(test_db, options=["A.甲", "B.乙"])
        _seed_review_artifacts(test_db)
        assert _count(test_db, ReviewLog) == 1  # 前置：确有记录

        client.delete("/api/understanding/questions/q1", headers=_auth())

        assert _count(test_db, ReviewLog) == 0, "复习记录变成了孤儿"

    def test_delete_cascades_quiz_review_state(self, test_db):
        """★★ 级联 ②（最隐蔽的一条）：`review_states` 里 `item_type='quiz'` 的行

        那张表用 `(user_id, item_type, item_id)` 定位，`item_id` 是**普通字符串**
        （没有外键）⇒ 数据库**不会**替你级联。漏掉它，孤儿行会一直留在
        "到期队列"里，用户看到一个**点不开的复习项**，而且没有任何报错。
        """

        from app.models.review_state import ITEM_TYPE_QUIZ, ReviewState

        client = _client()
        _seed(test_db, options=["A.甲", "B.乙"])
        _seed_review_artifacts(test_db)
        assert _count(
            test_db, ReviewState, item_type=ITEM_TYPE_QUIZ, item_id="q1"
        ) == 1

        client.delete("/api/understanding/questions/q1", headers=_auth())

        assert _count(
            test_db, ReviewState, item_type=ITEM_TYPE_QUIZ, item_id="q1"
        ) == 0, "review_states 里留下了点不开的孤儿复习项"

    def test_delete_does_not_touch_card_review_state(self, test_db):
        """★★ 反向断言：`item_type='card'` 的行**不能**被删

        删题目不该影响那张卡片本身的复习进度（卡片还能整体回忆）。
        没有这条，把级联写成"删掉 item_id 相同的所有 review_states"
        也能让上面那条绿 —— 而那是**误伤**。
        """

        from app.models.review_state import ITEM_TYPE_CARD, ReviewState

        client = _client()
        _seed(test_db, options=["A.甲", "B.乙"])
        _seed_review_artifacts(test_db)

        client.delete("/api/understanding/questions/q1", headers=_auth())

        assert _count(
            test_db, ReviewState, item_type=ITEM_TYPE_CARD, item_id="c1"
        ) == 1, "删题目把卡片本身的复习状态也删了"

    def test_delete_other_users_quiz_returns_404(self, test_db):

        from app.models.quiz_item import QuizItem
        from app.models.user import User

        client = _client()
        _seed(test_db, options=["A.甲", "B.乙"])

        async def _add_other():
            async with test_db() as session:
                session.add(User(id="u2", email="b@x.c", username="b", hashed_password="x"))
                await session.commit()

        asyncio.run(_add_other())

        resp = client.delete("/api/understanding/questions/q1", headers=_auth("u2"))

        assert resp.status_code == 404
        assert _count(test_db, QuizItem) == 1, "别人的题也删掉了"

    def test_delete_missing_returns_404(self, test_db):

        client = _client()
        _seed(test_db, options=["A.甲", "B.乙"])

        resp = client.delete("/api/understanding/questions/nope", headers=_auth())

        assert resp.status_code == 404


class TestEndpointsExist:
    """★ 计划要求"开工前用代码实测确认"——把这四条路由钉住

    改造前题目**只有 GET**。这条断言防止哪天有人把 PUT/DELETE 删掉
    （或重构路由时漏掉），而"没有编辑入口"这个状态**不会报任何错**。

    ⚠️ 判据读 **OpenAPI 契约**而不是 `app.routes`：实测本项目的
    `app.routes` 只有 3 条（IncludedRouter 没被摊平），逐条遍历会得到
    一个**空集合** —— 而"空集合里找不到路由"会让这条守卫变成恒红或恒绿，
    两种都毫无意义。契约里的是**对外承诺**，也正是前端生成类型的来源。
    """

    def test_quiz_put_and_delete_are_in_the_openapi_contract(self):
        from app.main import app

        spec = app.openapi()
        path = "/api/understanding/questions/{quiz_id}"
        assert path in spec["paths"], (
            f"题目编辑/删除不在契约里；现有 questions 相关路径："
            f"{sorted(p for p in spec['paths'] if 'question' in p)}"
        )
        methods = spec["paths"][path]
        assert "put" in methods, "题目编辑（PUT）不见了"
        assert "delete" in methods, "题目删除（DELETE）不见了"

    def test_contract_declares_the_edit_request_shape(self):
        """编辑请求的形状也要在契约里（前端靠它生成类型）"""
        from app.main import app

        spec = app.openapi()
        req = spec["paths"]["/api/understanding/questions/{quiz_id}"]["put"]["requestBody"]
        schema_ref = req["content"]["application/json"]["schema"]["$ref"]
        schema_name = schema_ref.rsplit("/", 1)[-1]
        assert schema_name in spec["components"]["schemas"], schema_name
        props = spec["components"]["schemas"][schema_name]["properties"]
        for field in ("question", "answer", "options", "explanation"):
            assert field in props, f"编辑请求缺字段 {field}"

    def test_delete_declares_204(self):
        from app.main import app

        spec = app.openapi()
        responses = spec["paths"]["/api/understanding/questions/{quiz_id}"]["delete"]["responses"]
        assert "204" in responses, f"删除没声明 204：{sorted(responses)}"
