"""note_versions: add unique (note_id, version_number) constraint

Revision ID: 008_note_version_unique
Revises: 007_safe_card_relation_dedup
Create Date: 2026-08-15

背景（deep_audit_report.md F-31）：
version_service.create_version 用 MAX(version_number)+1 取号，用户编辑、自动清洗、
恢复等入口并发写入时可能拿到相同 version_number，导致版本文件 v{N}.md 互相覆盖、
恢复/对比命中错误版本。为 (note_id, version_number) 建唯一索引从根上防重号；
create_version 已改为捕获 IntegrityError 重算重试。
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '008_note_version_unique'
down_revision: Union[str, None] = '007_safe_card_relation_dedup'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_note_versions_note_version "
        "ON note_versions (note_id, version_number)"
    )


def downgrade() -> None:
    with op.batch_alter_table('note_versions') as batch_op:
        batch_op.drop_index('uq_note_versions_note_version')
