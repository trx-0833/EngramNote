"""add locale to users

Revision ID: 012_user_locale
Revises: 011_refresh_tokens
Create Date: 2026-10-19

界面语言偏好（i18n 阶段 0）：

`users.locale`（String(10)，默认 `zh-CN`，NOT NULL）。

## 为什么需要这一列（而不是只靠 `Accept-Language`）

LLM 提示词要跟界面语言走，而提示词的调用点在 **Celery worker** 里 ——
那里没有请求上下文（任务签名只有 `note_id`），读不到 `Accept-Language`。
worker 需要一条"从 note_id 反查用户语言"的路：`note.user_id → users.locale`。

## 为什么 `server_default` 是必须的

加一个 `NOT NULL` 列时，既有行需要一个值 —— 不给 `server_default`
会被数据库拒绝（SQLite 与 PostgreSQL 都是）。默认 `zh-CN` 与
`core/i18n.DEFAULT_LOCALE` 一致，所以既有用户的观感一字不变。

⚠️ 同一件事在 `app/database.py` 的 `_migrate_sqlite()` 里也做了一遍
（开发环境的简易迁移，见该函数 docstring）。两处必须同时改 ——
那份是"启动时自动补列"，这份是"生产用 Alembic 迁移"。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '012_user_locale'
down_revision: Union[str, None] = '011_refresh_tokens'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('users') as batch_op:
        batch_op.add_column(
            sa.Column('locale', sa.String(length=10), server_default='zh-CN', nullable=False)
        )


def downgrade() -> None:
    with op.batch_alter_table('users') as batch_op:
        batch_op.drop_column('locale')
