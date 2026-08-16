"""safe card_relation dedup: keep directional/typed relations, add unique index

Revision ID: 007_safe_card_relation_dedup
Revises: 006_project_tags_refactor
Create Date: 2026-08-15

背景（deep_audit_report.md C-1）：
旧逻辑每次启动按 (user_id, 小id, 大id) 分组保 MIN(id) 去重，会把「同两张卡、
方向相反」（prerequisite(A,B) vs prerequisite(B,A)）或「同两张卡、不同类型」
（related + prerequisite）的合法关系当作重复永久删除。

本迁移一次性安全去重：仅删除 (user_id, card_id_1, card_id_2, relation_type, status)
完全同键的重复行（保 MIN(id)），并为完全同键组合建唯一索引，从根上防止再次产生。
注意：唯一索引键保留 card_id_1/card_id_2 原始方向（有向关系语义），
不做大小排序（与 ORM 存储约定一致，create_relation 已统一）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '007_safe_card_relation_dedup'
down_revision: Union[str, None] = '006_project_tags_refactor'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. 安全去重：仅删完全同键（同方向、同类型、同状态）的重复行
    op.execute(
        """
        DELETE FROM card_relations WHERE id NOT IN (
            SELECT MIN(id) FROM card_relations
            GROUP BY user_id, card_id_1, card_id_2, relation_type, status
        )
        """
    )
    # 2. 建唯一索引（防止再次产生完全重复；存在则跳过）
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_card_relations_pair_type "
        "ON card_relations (user_id, card_id_1, card_id_2, relation_type)"
    )


def downgrade() -> None:
    # 回滚仅移除唯一索引（不恢复被删的重复行，数据不可逆）
    with op.batch_alter_table('card_relations') as batch_op:
        batch_op.drop_index('uq_card_relations_pair_type')
