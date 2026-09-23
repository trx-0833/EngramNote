/**
 * @file 被引用笔记列表
 * @description
 * 由 `pages/NoteDetail.tsx` 拆分而来（overhaul-plan 5.5），只做搬运：
 * `linked_personal_notes` 缺失时按空数组处理（契约漂移兜底，与拆分前一致）。
 */
import type { NoteLinksResponse } from '../../api/client';

interface CitingNotesSectionProps {
  /** 当前笔记的链接关系（为 null 时本组件不渲染任何内容） */
  noteLinks: NoteLinksResponse | null;
}

/** 被以下笔记引用 */
export default function CitingNotesSection({ noteLinks }: CitingNotesSectionProps) {
  if (!noteLinks) return null;

  return (
    <div className="card" style={{ marginBottom: '1rem' }}>
      <h3 style={{ fontSize: '1rem', marginBottom: '0.5rem' }}>被以下笔记引用</h3>
      <ul style={{ listStyle: 'none', padding: 0 }}>
        {(noteLinks.linked_personal_notes ?? []).map((n) => (
          <li key={n.id} style={{ padding: '0.25rem 0' }}>
            <a href={`/notes/${n.id}`} style={{ color: 'var(--color-primary)' }}>
              {n.title}
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** 是否应该显示「被以下笔记引用」区块 */
export function shouldShowCitingNotes(noteLinks: NoteLinksResponse | null): boolean {
  if (!noteLinks) return false;
  return (noteLinks.linked_personal_notes?.length ?? 0) > 0;
}
