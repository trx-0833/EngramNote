"""expand user_settings for learning/content preferences

Revision ID: 015_settings_expansion
Revises: 014_user_settings
Create Date: 2026-11-05

P2 设置项：目标留存率、最长间隔、AI 输出语言、卡片上限与三条清洗开关。

`desired_retention` / `max_interval_days` 只影响**未来**排期；不重排已有卡片。
`output_locale='follow'` 时 worker 回落到 `users.locale`。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = '015_settings_expansion'
down_revision: Union[str, None] = '014_user_settings'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('user_settings') as batch_op:
        batch_op.add_column(
            sa.Column('desired_retention', sa.Float(), server_default='0.9', nullable=False)
        )
        batch_op.add_column(
            sa.Column('max_interval_days', sa.Integer(), server_default='3650', nullable=False)
        )
        batch_op.add_column(
            sa.Column('output_locale', sa.String(length=10), server_default='follow', nullable=False)
        )
        batch_op.add_column(
            sa.Column('card_max_new_per_run', sa.Integer(), server_default='500', nullable=False)
        )
        batch_op.add_column(
            sa.Column('clean_simplify_formula', sa.Boolean(), server_default='0', nullable=False)
        )
        batch_op.add_column(
            sa.Column('clean_normalize_spacing', sa.Boolean(), server_default='1', nullable=False)
        )
        batch_op.add_column(
            sa.Column('clean_normalize_heading_levels', sa.Boolean(), server_default='1', nullable=False)
        )


def downgrade() -> None:
    with op.batch_alter_table('user_settings') as batch_op:
        batch_op.drop_column('clean_normalize_heading_levels')
        batch_op.drop_column('clean_normalize_spacing')
        batch_op.drop_column('clean_simplify_formula')
        batch_op.drop_column('card_max_new_per_run')
        batch_op.drop_column('output_locale')
        batch_op.drop_column('max_interval_days')
        batch_op.drop_column('desired_retention')
