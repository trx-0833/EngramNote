"""
复习状态迁移脚本（overhaul-plan 阶段 3.1）

把存量数据的调度状态从 `quiz_items` 搬进 `review_states`，并为
**每一张卡片**补建卡片级复习状态（阶段 3.12 的前提）。

## 为什么需要显式迁移，而不是只靠惰性补建

`review_state_service.get_state()` 会在读取时按需补建，那对"用户即将复习的
那一项"足够。但两类场景**必须**提前建好，否则功能不可用：

1. **到期队列**：`list_due_states()` 只返回已存在于 `review_states` 的行。
   存量 1058 道题若只靠惰性补建，它们永远不会出现在到期队列里 ——
   用户打开复习页会看到"没有到期题目"，而实际上有上千道。
2. **卡片直接复习**：卡片级状态没有任何旧字段可依赖，不预建就等于
   "所有卡片都不可复习"，阶段 3.12 形同虚设。

## 幂等

以 `(user_id, item_type, item_id)` 为键，已存在则**跳过不覆盖**。
因此可以安全地重复运行：它只补缺失的行，不会把已经复习过的状态重置。

## 用法（在 backend/ 目录下）

    python scripts/migrate_review_states.py --dry-run   # 只统计，不写入
    python scripts/migrate_review_states.py             # 实际执行
    python scripts/migrate_review_states.py --user <id>  # 只处理某个用户
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


async def _migrate(user_id: str | None, dry_run: bool) -> dict:
    from sqlalchemy import select

    from app import database as db_mod
    from app.models.knowledge_card import KnowledgeCard
    from app.models.quiz_item import QuizItem
    from app.models.review_state import (
        ITEM_TYPE_CARD,
        ITEM_TYPE_QUIZ,
        ReviewState,
    )
    from app.services.review_state_service import _bootstrap_state

    await db_mod.init_db()
    session_factory = db_mod.get_session_factory()

    stats = {"quiz_scanned": 0, "quiz_created": 0, "card_scanned": 0, "card_created": 0}

    async with session_factory() as session:
        # 已存在的键，避免逐条查询
        existing = {
            (r.user_id, r.item_type, r.item_id)
            for r in (await session.execute(
                select(ReviewState.user_id, ReviewState.item_type, ReviewState.item_id)
            )).all()
        }
        print(f"已有复习状态: {len(existing)} 条")

        # ---- 1. 题目维度：从 quiz_items 旧字段搬过来 ----
        quiz_query = select(QuizItem.id, QuizItem.user_id)
        if user_id:
            quiz_query = quiz_query.where(QuizItem.user_id == user_id)
        quizzes = (await session.execute(quiz_query)).all()
        stats["quiz_scanned"] = len(quizzes)

        for quiz_id, owner_id in quizzes:
            key = (owner_id, ITEM_TYPE_QUIZ, quiz_id)
            if key in existing:
                continue
            stats["quiz_created"] += 1
            if not dry_run:
                await _bootstrap_state(session, owner_id, ITEM_TYPE_QUIZ, quiz_id)

        print(
            f"题目维度: 扫描 {stats['quiz_scanned']}，"
            f"新建 {stats['quiz_created']}"
            + ("（dry-run，未写入）" if dry_run else "")
        )

        # ---- 2. 卡片维度：为每张卡片建"可复习"状态 ----
        card_query = select(KnowledgeCard.id, KnowledgeCard.user_id)
        if user_id:
            card_query = card_query.where(KnowledgeCard.user_id == user_id)
        cards = (await session.execute(card_query)).all()
        stats["card_scanned"] = len(cards)

        for card_id, owner_id in cards:
            key = (owner_id, ITEM_TYPE_CARD, card_id)
            if key in existing:
                continue
            stats["card_created"] += 1
            if not dry_run:
                await _bootstrap_state(session, owner_id, ITEM_TYPE_CARD, card_id)

        print(
            f"卡片维度: 扫描 {stats['card_scanned']}，"
            f"新建 {stats['card_created']}"
            + ("（dry-run，未写入）" if dry_run else "")
        )

    if not dry_run:
        async with session_factory() as session:
            from app.services.review_state_service import count_states

            stats_by_type = await count_states(session)
            print(f"迁移后统计: {stats_by_type}")

    return stats


async def _recalibrate_mastery(user_id: str | None, dry_run: bool) -> None:
    """按新公式重算全部卡片掌握度（阶段 3.9）

    公式变更后库里存量 `mastery_level` 是旧公式的产物
    （现场实测 1078/1183 为 0），不重算的话用户看到的仍是旧数据。
    """
    if dry_run:
        print("掌握度重算: dry-run 跳过")
        return

    from app import database as db_mod
    from app.services.mastery_service import recalibrate_all_mastery

    async with db_mod.get_session_factory()() as session:
        result = await recalibrate_all_mastery(session, user_id=user_id)
    print(f"掌握度重算: {result}")


def main() -> int:
    parser = argparse.ArgumentParser(description="迁移复习状态到 review_states")
    parser.add_argument("--user", help="只处理指定用户 ID")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不写入")
    parser.add_argument(
        "--skip-mastery", action="store_true",
        help="跳过掌握度重算（默认会按新公式重算）",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("复习状态迁移（阶段 3.1）")
    print("=" * 60)

    stats = asyncio.run(_migrate(args.user, args.dry_run))

    if not args.skip_mastery:
        print("-" * 60)
        asyncio.run(_recalibrate_mastery(args.user, args.dry_run))

    print("=" * 60)
    print(
        f"完成: 题目 {stats['quiz_created']}/{stats['quiz_scanned']} 新建，"
        f"卡片 {stats['card_created']}/{stats['card_scanned']} 新建"
    )
    print("提示：本脚本幂等，可重复运行；重复运行只会补缺失的行。")
    return 0


if __name__ == "__main__":
    # 迁移本身不做破坏性 schema 变更，但需要 init_db 建出 review_states 表；
    # 若库里存在旧的 NOT NULL schema，重建走同一道破坏性闸门。
    if "--dry-run" not in sys.argv:
        os.environ.setdefault("ENGRAMNOTE_ALLOW_DESTRUCTIVE_MIGRATION", "0")
    raise SystemExit(main())
