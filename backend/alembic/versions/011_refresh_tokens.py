"""add refresh_tokens table (jti revocation list + rotation chain)

Revision ID: 011_refresh_tokens
Revises: 010_user_reminder_settings
Create Date: 2026-09-12

阶段 6.3：刷新令牌 + 轮换 + jti 黑名单 + 登出撤销。

新建 `refresh_tokens` 表（一枚刷新令牌一行）：

- `jti` 唯一 —— 令牌与数据库行的对应关系，也是黑名单的主键语义；
- `revoked_at` 非空 = 已作废（轮换换掉的、登出撤销的、整链撤销的）；
- `replaced_by_jti` 记录轮换链，配合 `family_id` 实现重放检测
  （旧令牌再次出现 ⇒ 判定泄露 ⇒ 撤销整条链）。

⚠️ 为什么不需要 `_migrate_sqlite` 里的防御性建表：这是一张**全新的表**，
`Base.metadata.create_all()` 会为全新库与存量库都建出来。database.py 里
那些手工 CREATE TABLE 针对的是"表已存在但没有新列"的情形（create_all
不会 ALTER 已有表），与本迁移无关。本表也不需要给已有表加列，
因此没有"新增字段必须同时登记到 _migrate_sqlite"的问题。

⚠️ 本表**只存刷新令牌**，不存访问令牌：访问令牌保持无状态
（不改 `sub` + `exp` 的轻量形态），代价是它在 `exp` 之前无法被单独吊销 ——
详见 docs/overhaul-plan.md 附录 BA.3 与 services/refresh_token_service.py 的说明。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '011_refresh_tokens'
down_revision: Union[str, None] = '010_user_reminder_settings'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'refresh_tokens',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('jti', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('family_id', sa.String(length=64), nullable=False),
        sa.Column('issued_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('replaced_by_jti', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    # jti 唯一索引：令牌 ↔ 行一一对应（同时兜住"同一枚令牌被插两次"）
    op.create_index('ix_refresh_tokens_jti', 'refresh_tokens', ['jti'], unique=True)
    # 按用户查（登出全部设备）与按链查（重放时整链撤销）
    op.create_index('ix_refresh_tokens_user_id', 'refresh_tokens', ['user_id'], unique=False)
    op.create_index('ix_refresh_tokens_family_id', 'refresh_tokens', ['family_id'], unique=False)
    # 过期行清理任务按它扫描
    op.create_index('ix_refresh_tokens_expires_at', 'refresh_tokens', ['expires_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_refresh_tokens_expires_at', table_name='refresh_tokens')
    op.drop_index('ix_refresh_tokens_family_id', table_name='refresh_tokens')
    op.drop_index('ix_refresh_tokens_user_id', table_name='refresh_tokens')
    op.drop_index('ix_refresh_tokens_jti', table_name='refresh_tokens')
    op.drop_table('refresh_tokens')
