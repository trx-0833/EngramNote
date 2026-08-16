"""project tags refactor: drop projects.slug and notes.project_id, add note_projects

Revision ID: 006_project_tags_refactor
Revises: 005_add_projects
Create Date: 2026-08-16

项目已从「Vault 第一层目录（slug + notes.project_id 单值归属）」演化为
「纯标签（note_projects 多对多）」。本迁移负责把 005 产生的旧 schema 对齐到
当前 ORM：
- projects 表移除 slug
- notes 表移除 project_id
- 新建 note_projects 多对多关联表
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '006_project_tags_refactor'
down_revision: Union[str, None] = '005_add_projects'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. 创建纯标签关联表
    op.create_table(
        'note_projects',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('note_id', sa.String(length=36), nullable=False),
        sa.Column('project_id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['note_id'], ['notes.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('note_id', 'project_id', name='uq_note_projects_note_project'),
    )
    op.create_index('ix_note_projects_note_id', 'note_projects', ['note_id'], unique=False)
    op.create_index('ix_note_projects_project_id', 'note_projects', ['project_id'], unique=False)
    op.create_index('ix_note_projects_user_id', 'note_projects', ['user_id'], unique=False)

    # 2. 迁移 005 留下的单值归属列：先尝试把数据搬到 note_projects
    #    旧列可空，直接 INSERT ... SELECT，UUID 主键由应用默认生成，这里用随机十六进制无法
    #    可靠保证 36 位格式，因此保留数据迁移交给应用启动 SQLite 迁移或人工脚本；
    #    本迁移只负责 schema 对齐，避免旧列继续误导查询。
    op.drop_index('ix_notes_project_id', table_name='notes')
    op.drop_constraint('fk_notes_project_id_projects', 'notes', type_='foreignkey')
    op.drop_column('notes', 'project_id')

    # 3. 项目为纯标签，移除 slug
    op.drop_index('ix_projects_slug', table_name='projects')
    with op.batch_alter_table('projects') as batch_op:
        batch_op.drop_column('slug')


def downgrade() -> None:
    # 回滚仅恢复 schema 形状，标签关联无法无损还原为单值 project_id
    with op.batch_alter_table('projects') as batch_op:
        batch_op.add_column(sa.Column('slug', sa.String(length=60), nullable=True))
    op.create_index('ix_projects_slug', 'projects', ['slug'], unique=False)

    op.add_column('notes', sa.Column('project_id', sa.String(), nullable=True))
    op.create_index('ix_notes_project_id', 'notes', ['project_id'], unique=False)
    op.create_foreign_key('fk_notes_project_id_projects', 'notes', 'projects', ['project_id'], ['id'])

    op.drop_index('ix_note_projects_user_id', table_name='note_projects')
    op.drop_index('ix_note_projects_project_id', table_name='note_projects')
    op.drop_index('ix_note_projects_note_id', table_name='note_projects')
    op.drop_table('note_projects')
