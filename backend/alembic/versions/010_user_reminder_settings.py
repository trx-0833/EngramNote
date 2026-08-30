"""add email_reminder_enabled / last_reminded_at to users

Revision ID: 010_user_reminder_settings
Revises: 009_trash_soft_delete
Create Date: 2026-08-30

邮件提醒用户级开关 + 去重记录：
1. users.email_reminder_enabled（Boolean，默认 True）——保持「全局开启即发」的向后兼容；
2. users.last_reminded_at（DateTime，nullable）——记录最近一次提醒发送时间，供去重与展示。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '010_user_reminder_settings'
down_revision: Union[str, None] = '009_trash_soft_delete'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('users') as batch_op:
        batch_op.add_column(sa.Column('email_reminder_enabled', sa.Boolean(),
                                      server_default='1', nullable=False))
        batch_op.add_column(sa.Column('last_reminded_at', sa.DateTime(timezone=True),
                                      nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('users') as batch_op:
        batch_op.drop_column('last_reminded_at')
        batch_op.drop_column('email_reminder_enabled')