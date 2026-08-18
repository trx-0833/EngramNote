"""trash: soft delete for notes + dangling-reference FKs

Revision ID: 009_trash_soft_delete
Revises: 008_note_version_unique
Create Date: 2026-08-18

回收站功能数据层改造：
1. notes 新增 trashed_at（NULL=正常，非 NULL=已移入回收站）；
2. 悬挂引用策略——物理删除笔记/卡片时关系记录永不级联删除，改为置 NULL：
   - card_relations.card_id_1 / card_id_2 → nullable + ON DELETE SET NULL
   - note_material_links.personal_note_id / material_note_id → nullable + ON DELETE SET NULL
   - quiz_items.note_id → nullable + ON DELETE SET NULL
   - review_logs.note_id → nullable + ON DELETE SET NULL
   - knowledge_cards.note_id → nullable（由应用层控制"提升核心卡片"置 NULL）
使用 batch_alter_table 兼容 SQLite 与 PostgreSQL。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '009_trash_soft_delete'
down_revision: Union[str, None] = '008_note_version_unique'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) notes.trashed_at 软删除标记
    with op.batch_alter_table('notes') as batch_op:
        batch_op.add_column(sa.Column('trashed_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index('ix_notes_trashed_at', 'notes', ['trashed_at'], unique=False)

    # 2) card_relations 卡片端 → 悬挂引用
    with op.batch_alter_table('card_relations') as batch_op:
        batch_op.alter_column('card_id_1',
                              existing_type=sa.String(),
                              nullable=True)
        batch_op.alter_column('card_id_2',
                              existing_type=sa.String(),
                              nullable=True)
        batch_op.create_foreign_key('fk_card_relations_card1_dangling', 'knowledge_cards',
                                    ['card_id_1'], ['id'], ondelete='SET NULL')
        batch_op.create_foreign_key('fk_card_relations_card2_dangling', 'knowledge_cards',
                                    ['card_id_2'], ['id'], ondelete='SET NULL')

    # 3) note_material_links 两端 → 悬挂引用
    with op.batch_alter_table('note_material_links') as batch_op:
        batch_op.alter_column('personal_note_id',
                              existing_type=sa.String(),
                              nullable=True)
        batch_op.alter_column('material_note_id',
                              existing_type=sa.String(),
                              nullable=True)
        batch_op.create_foreign_key('fk_nml_personal_dangling', 'notes',
                                    ['personal_note_id'], ['id'], ondelete='SET NULL')
        batch_op.create_foreign_key('fk_nml_material_dangling', 'notes',
                                    ['material_note_id'], ['id'], ondelete='SET NULL')

    # 4) knowledge_cards.note_id → 可空（独立节点）
    with op.batch_alter_table('knowledge_cards') as batch_op:
        batch_op.alter_column('note_id',
                              existing_type=sa.String(),
                              nullable=True)

    # 5) quiz_items.note_id → 悬挂引用
    with op.batch_alter_table('quiz_items') as batch_op:
        batch_op.alter_column('note_id',
                              existing_type=sa.String(),
                              nullable=True)
        batch_op.create_foreign_key('fk_quiz_items_note_dangling', 'notes',
                                    ['note_id'], ['id'], ondelete='SET NULL')

    # 6) review_logs.note_id → 悬挂引用
    with op.batch_alter_table('review_logs') as batch_op:
        batch_op.alter_column('note_id',
                              existing_type=sa.String(),
                              nullable=True)
        batch_op.create_foreign_key('fk_review_logs_note_dangling', 'notes',
                                    ['note_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    with op.batch_alter_table('review_logs') as batch_op:
        batch_op.alter_column('note_id', existing_type=sa.String(), nullable=False)
    with op.batch_alter_table('quiz_items') as batch_op:
        batch_op.alter_column('note_id', existing_type=sa.String(), nullable=False)
    with op.batch_alter_table('knowledge_cards') as batch_op:
        batch_op.alter_column('note_id', existing_type=sa.String(), nullable=False)
    with op.batch_alter_table('note_material_links') as batch_op:
        batch_op.alter_column('personal_note_id', existing_type=sa.String(), nullable=False)
        batch_op.alter_column('material_note_id', existing_type=sa.String(), nullable=False)
    with op.batch_alter_table('card_relations') as batch_op:
        batch_op.alter_column('card_id_1', existing_type=sa.String(), nullable=False)
        batch_op.alter_column('card_id_2', existing_type=sa.String(), nullable=False)
    with op.batch_alter_table('notes') as batch_op:
        batch_op.drop_column('trashed_at')
