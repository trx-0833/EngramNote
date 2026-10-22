"""make quiz_items.card_id nullable

Revision ID: 013_quiz_card_nullable
Revises: 012_user_locale
Create Date: 2026-10-22

## 为什么要把 `quiz_items.card_id` 改成可空

这是一个**实测出来的真实缺陷**的修复（不是预防性改动）。

重跑理解时 `backend/app/api/understanding.py:165-190` 按 **`note_id`** 删产物：

```python
await db.execute(sql_delete(QuizItem).where(QuizItem.note_id == note_id))       # :188
await db.execute(sql_delete(KnowledgeCard).where(KnowledgeCard.note_id == note_id))  # :189
```

但 `quiz_items.card_id → knowledge_cards.id` 是外键，而**题目可以引用别的笔记的卡片**
（`card_id` 指向的卡不属于本题的 `note_id`）。这类跨笔记题目按 `note_id` 删不到，
于是紧接着删卡片时撞外键：

```
IntegrityError: FOREIGN KEY constraint failed
[SQL: DELETE FROM knowledge_cards WHERE knowledge_cards.note_id = ?]
```

接口直接返回 **500**，用户**无法重跑那篇笔记**。

**实测影响面**（副本 `data/_uxwalk`）：该笔记 2 行被别的笔记引用；
全库 **8 条**跨笔记题目；受影响 3 篇笔记。

## 为什么选"置空"而不是"删掉那些题目"

按拍板（2026-10-22）：**`card_id` 改可空，重跑时把别的笔记引用本篇卡片的题目
`card_id` 置空** —— 保留题目本身，只断掉指向"即将被删并重建"的卡片的链。

这与 `review_log.card_id` 是**同一手法**：`app/models/review_log.py:79-81` 已经是
`Mapped[Optional[str]]` + `nullable=True`，那里的注释写明了道理 ——
卡片会被重建，而**题目/复习记录不该跟着消失**。

置空的语义是「这道题的原卡片已被重建，链接失效」，不是「这道题没有来源」。
`note_id` 仍在，所以题目依然归属那篇笔记。

## SQLite 需要重建表

SQLite 不支持直接去掉 `NOT NULL`，所以 `batch_alter_table` 会走
"建新表 → 拷数据 → 换名"的路径（Alembic 的 batch 模式自动处理）。

⚠️ 同一件事在 `app/database.py` 的 `_migrate_sqlite()` 里也做了一遍
（开发环境的启动时简易迁移）。**两处必须同时改** —— 那份是"启动时自动补"，
这份是"生产用 Alembic 迁移"。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '013_quiz_card_nullable'
down_revision: Union[str, None] = '012_user_locale'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ⚠️ SQLite 不能直接 `ALTER COLUMN ... DROP NOT NULL` ⇒ 必须用 batch 模式
    #    （它会在 SQLite 上走"建新表 → 拷数据 → 换名"）
    with op.batch_alter_table('quiz_items') as batch_op:
        batch_op.alter_column('card_id', existing_type=sa.String(), nullable=True)


def downgrade() -> None:
    # 回退前必须先把置空的行填回去 —— 否则加回 NOT NULL 会失败。
    # 这里**没有**可用的回填值（原卡片已经不存在了），所以如实报错而不是
    # 静默塞一个假 id：塞假 id 会造出一条指向不存在卡片的记录，
    # 比让回退失败更糟。
    conn = op.get_bind()
    dangling = conn.execute(
        sa.text("SELECT count(*) FROM quiz_items WHERE card_id IS NULL")
    ).scalar()
    if dangling:
        raise RuntimeError(
            f"无法回退：quiz_items 里有 {dangling} 行 card_id 为空"
            "（原卡片已被重跑删除，没有可回填的值）。"
            "如确需回退，请先人工决定这些题目的归属。"
        )
    with op.batch_alter_table('quiz_items') as batch_op:
        batch_op.alter_column('card_id', existing_type=sa.String(), nullable=False)
