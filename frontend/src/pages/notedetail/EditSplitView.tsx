/**
 * @file 编辑模式：实时分屏预览
 * @description
 * 由 `pages/NoteDetail.tsx` 拆分而来（overhaul-plan 5.5），只做搬运：
 * 左侧 Markdown 文本域、右侧即时渲染（`renderMarkdown(editContent)`），
 * 按钮文案与禁用条件均与拆分前一致。
 */
// 分屏栅格的类名归模块所有（overhaul-plan 5.6 序 8）；见 EditSplitView.module.css
import styles from './EditSplitView.module.css';

interface EditSplitViewProps {
  /** 编辑中的 Markdown 原文 */
  editContent: string;
  /** 编辑内容变化 */
  onEditContentChange: (value: string) => void;
  /** 是否正在保存 */
  saving: boolean;
  /** 保存 */
  onSave: () => void;
  /** 取消编辑（有未保存修改时确认） */
  onCancel: () => void;
  /** 右侧即时渲染出的 HTML */
  previewHtml: string;
}

/** 编辑模式：实时分屏预览（左侧 Markdown 编辑，右侧即时渲染） */
export default function EditSplitView({
  editContent,
  onEditContentChange,
  saving,
  onSave,
  onCancel,
  previewHtml,
}: EditSplitViewProps) {
  return (
    <div>
      <div style={{ display: 'flex', gap: 'var(--space-sm)', marginBottom: 'var(--space-sm)' }}>
        <button className="btn btn-primary" onClick={onSave} disabled={saving}>
          {saving ? '保存中...' : '保存'}
        </button>
        <button className="btn btn-secondary" onClick={onCancel} disabled={saving}>
          取消
        </button>
      </div>
      {/* 分屏栅格用 min(320px, 100%) 做内在尺寸：窄屏自动变单栏，不需要额外断点。
          ⚠️ 768px 那条显式兜底（grid-template-columns: 1fr）也住在同一个模块里
          —— 类名哈希后写在 responsive.css 里的选择器会永远选不中。 */}
      <div className={styles.editSplit}>
        <textarea
          className={`${styles.markdownEditor} card`}
          value={editContent}
          onChange={(e) => onEditContentChange(e.target.value)}
          disabled={saving}
          style={{
            width: '100%',
            minHeight: '60vh',
            fontFamily: 'inherit',
            fontSize: '0.95rem',
            lineHeight: '1.6',
            resize: 'vertical',
            border: '1px solid var(--color-border)',
            borderRadius: '0.5rem',
            outline: 'none',
          }}
        />
        <article
          className="card markdown-body"
          dangerouslySetInnerHTML={{ __html: previewHtml }}
          style={{ minHeight: '60vh' }}
        />
      </div>
    </div>
  );
}
