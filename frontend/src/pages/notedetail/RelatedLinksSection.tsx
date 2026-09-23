/**
 * @file 关联的学习资料列表（含悬挂链接占位与清理）
 * @description
 * 由 `pages/NoteDetail.tsx` 拆分而来（overhaul-plan 5.5），只做搬运。
 *
 * ⚠️ `?.` / `?? []` 不是多余的防御：接口响应少一个字段时，
 * 这里原来是 `noteLinks.linked_materials.length` —— 直接
 * `undefined.length` 抛异常，而异常发生在渲染中，整页变成白屏
 * （本轮写表征测试时就以"mock 少给一个字段"的形式复现过一次）。
 * 字段缺失时最坏的结果应该是"这一块不显示"，不是"什么都看不到"。
 * 拆分后**必须**保留这些兜底，`NoteDetail.test.tsx` 里有一条断言盯着它。
 */
import type { NoteLinksResponse } from '../../api/client';

interface RelatedLinksSectionProps {
  /** 当前笔记的链接关系（为 null 时本组件不渲染任何内容） */
  noteLinks: NoteLinksResponse | null;
  /** 清理悬挂链接（以当前有效资料全量覆盖） */
  onCleanDanglingLinks: () => void;
}

/** 关联的学习资料 / 悬挂链接清理 */
export default function RelatedLinksSection({
  noteLinks,
  onCleanDanglingLinks,
}: RelatedLinksSectionProps) {
  if (!noteLinks) return null;

  return (
    <div className="card" style={{ marginBottom: '1rem' }}>
      <h3 style={{ fontSize: '1rem', marginBottom: '0.5rem' }}>关联的学习资料</h3>
      <ul style={{ listStyle: 'none', padding: 0 }}>
        {(noteLinks.linked_materials ?? []).map((m) => (
          <li key={m.id} style={{ padding: '0.25rem 0' }}>
            <a href={`/notes/${m.id}`} style={{ color: 'var(--color-primary)' }}>
              {m.title}
            </a>
          </li>
        ))}
        {/* 悬挂链接占位：资料已被彻底删除（悬挂引用策略保留行） */}
        {(noteLinks.dangling_material_count ?? 0) > 0 && (
          <li
            key="__dangling__"
            style={{
              padding: '0.25rem 0',
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              gap: 'var(--space-sm)',
            }}
          >
            <span style={{ color: 'var(--color-text-secondary)', fontStyle: 'italic' }}>
              [已删除的笔记]（{noteLinks.dangling_material_count} 个已彻底删除的资料）
            </span>
            <button
              className="btn btn-secondary"
              style={{ fontSize: '0.75rem', padding: '2px 8px' }}
              onClick={onCleanDanglingLinks}
            >
              清理此链接
            </button>
          </li>
        )}
      </ul>
    </div>
  );
}

/** 是否应该显示「关联的学习资料」区块（含悬挂链接占位；同样做字段缺失兜底） */
export function shouldShowRelatedLinks(noteLinks: NoteLinksResponse | null): boolean {
  if (!noteLinks) return false;
  return (
    (noteLinks.linked_materials?.length ?? 0) > 0 || (noteLinks.dangling_material_count ?? 0) > 0
  );
}
