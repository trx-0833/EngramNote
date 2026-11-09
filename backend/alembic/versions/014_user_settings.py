"""add user_settings table

Revision ID: 014_user_settings
Revises: 013_quiz_card_nullable
Create Date: 2026-11-04

用户级设置覆盖表。

## 为什么是「覆盖」而不是「全量」

没有行 = 全部使用环境配置里的默认值；只有用户真正改过的值才落库。
这样新用户、老用户都不需要迁移数据，`GET /api/settings` 也不会写库。

## 为什么用显式列

设置值直接参与额度/提醒计算，数据库层能用 CHECK 挡住越界；JSON 列做不到
这一点，且 SQLAlchemy 对 JSON 原地修改的追踪是个已知坑。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = '014_user_settings'
down_revision: Union[str, None] = '013_quiz_card_nullable'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'user_settings',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('daily_review_limit', sa.Integer(), server_default='10', nullable=False),
        sa.Column('email_hour', sa.Integer(), server_default='9', nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', name='uq_user_settings_user_id'),
        sa.CheckConstraint(
            'daily_review_limit BETWEEN 5 AND 100',
            name='ck_user_settings_daily_review_limit',
        ),
        sa.CheckConstraint(
            'email_hour BETWEEN 0 AND 23',
            name='ck_user_settings_email_hour',
        ),
    )
    op.create_index('ix_user_settings_user_id', 'user_settings', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_user_settings_user_id', table_name='user_settings')
    op.drop_table('user_settings')
