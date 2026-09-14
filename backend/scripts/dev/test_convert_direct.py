"""直接调用转换任务测试（绕过 Celery broker）"""
import asyncio
import os
import sys

# 确保能找到项目模块
# 本文件位于 backend/scripts/dev/（计划 0.2 的搬迁），项目根 = 上溯三级
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.tasks.convert_tasks import _convert_document
from app.models.note import NoteStatus
from app.config import get_settings
from app.database import init_db

settings = get_settings()


async def main():
    # 初始化数据库
    await init_db()

    # 查找一个状态为 converting 的笔记
    from sqlalchemy import select
    from app.database import async_session
    from app.models.note import Note

    async with async_session() as session:
        result = await session.execute(
            select(Note).where(Note.status == NoteStatus.converting).limit(1)
        )
        note = result.scalars().first()

        if not note:
            print("No converting notes found. Upload a file first.")
            return

        note_id = note.id
        file_path = note.original_file_path
        source_type = note.source_type.value

        print(f"Found note: {note.title}")
        print(f"  ID: {note_id}")
        print(f"  File path: {file_path}")
        print(f"  Source type: {source_type}")
        print(f"  Status: {note.status}")

    print("\nStarting conversion...")
    try:
        await _convert_document(note_id, file_path, source_type)
        print("\nConversion completed!")
    except Exception as e:
        print(f"\nConversion failed: {e}")
        import traceback
        traceback.print_exc()

    # 检查结果
    async with async_session() as session:
        result = await session.execute(select(Note).where(Note.id == note_id))
        note = result.scalars().first()
        print(f"\nFinal status: {note.status}")
        if note.error_message:
            print(f"Error: {note.error_message}")
        if note.original_md_path:
            print(f"Markdown path: {note.original_md_path}")

            # 读取 Markdown 内容
            from app.services.storage_service import get_object_bytes
            try:
                data = get_object_bytes(settings.minio_bucket_markdown, note.original_md_path)
                md_content = data.decode("utf-8")
                print(f"\nMarkdown content ({len(md_content)} chars):")
                print(md_content[:1000])
            except Exception as e:
                print(f"Failed to read markdown: {e}")


asyncio.run(main())
