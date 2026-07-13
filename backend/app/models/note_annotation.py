"""
笔记标注模型模块

本模块定义用户在笔记上的高亮/下划线等标注信息。
标注与具体的视图模式（原文 / 清洗版）绑定，并记录标注文本及其前后文上下文，
便于跨视图复原标注位置。

主要职责：
- 定义 note_annotations 表，记录每条标注的类型、文本、上下文与颜色
- 通过 view_mode 区分标注应用于原文视图还是清洗视图

设计决策：
- view_mode 与 type 使用 String 而非 Enum，方便前端扩展新的标注形态
- context_before / context_after 用于在文档中精确定位标注位置，避免依赖字符偏移
- color 可选，未指定时由前端使用默认颜色
- 外键设置 ON DELETE CASCADE，笔记删除时自动清理标注
"""

from typing import Optional

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import BaseModel


class NoteAnnotation(BaseModel):
    """
    笔记标注模型

    对应数据库中的 note_annotations 表，记录用户在笔记上的标注。

    Attributes:
        id: UUID 主键（继承自 BaseModel）
        user_id: 所属用户 ID，外键关联 users 表
        note_id: 所属笔记 ID，外键关联 notes 表，删除时级联
        view_mode: 视图模式，original（原文）/ clean（清洗版）
        type: 标注类型，highlight（高亮）/ underline（下划线）
        text_content: 标注的文本内容
        context_before: 标注前文的上下文文本，默认空字符串
        context_after: 标注后文的上下文文本，默认空字符串
        color: 标注颜色（可选）
        created_at: 创建时间（继承自 BaseModel）
        updated_at: 更新时间（继承自 BaseModel）
    """
    __tablename__ = "note_annotations"

    # 所属用户，建立索引加速按用户查询
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False, index=True)
    # 所属笔记，外键关联 notes 表，删除时级联
    note_id: Mapped[str] = mapped_column(
        String, ForeignKey("notes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 视图模式：original（原文）/ clean（清洗版）
    view_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    # 标注类型：highlight（高亮）/ underline（下划线）
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    # 标注的文本内容
    text_content: Mapped[str] = mapped_column(Text, nullable=False)
    # 标注前文上下文，用于精确定位标注位置
    context_before: Mapped[str] = mapped_column(Text, default="")
    # 标注后文上下文，用于精确定位标注位置
    context_after: Mapped[str] = mapped_column(Text, default="")
    # 标注颜色（可选），未指定时前端使用默认颜色
    color: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)


if __name__ == "__main__":
    pass
