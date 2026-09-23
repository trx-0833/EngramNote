/**
 * @file 内容区域：按状态/模式在「进度面板 / 编辑分屏 / diff / Markdown / 空态」之间切换
 * @description
 * 由 `pages/NoteDetail.tsx` 拆分而来（overhaul-plan 5.5），只做搬运。
 *
 * 分支顺序是行为的一部分，不要重排：
 * 1. `converting` / `uploading` → 进度面板（阶段 5.11 的成果，不能被拆回静态文案）
 * 2. `editMode === 'edit'` → 编辑分屏
 * 3. `viewMode === 'diff'` → diff 视图
 * 4. 有正文 → Markdown 渲染
 * 5. 其余 → "暂无内容"
 */
import type { RefObject } from 'react';
import type { CleaningDiffResponse } from '../../api/client';
import DiffPanel from './DiffPanel';
import EditSplitView from './EditSplitView';
import MarkdownReader from './MarkdownReader';
import ProcessingPanel from './ProcessingPanel';
import type { EditMode, ViewMode } from './types';

interface ContentAreaProps {
  /** 笔记 ID */
  noteId: string | undefined;
  /** 笔记状态（决定是否显示进度面板） */
  status: string;
  /** 当前视图模式 */
  viewMode: ViewMode;
  /** 当前编辑模式 */
  editMode: EditMode;
  /** 当前应显示的 Markdown 原文 */
  mdContent: string;
  /** 正文渲染出的 HTML */
  htmlContent: string;
  /** 编辑预览渲染出的 HTML */
  editPreviewHtml: string;
  /** diff 数据 */
  diffData: CleaningDiffResponse | null;
  /** diff 是否加载中 */
  diffLoading: boolean;
  /** 正文容器 ref */
  markdownRef: RefObject<HTMLElement>;
  /** 选中文本后弹出批注浮层 */
  onMouseUp: () => void;
  /** 取消任务后刷新笔记数据 */
  onRefresh: () => void;
  /** ADHD Reader 状态与控制 */
  adhdReaderEnabled: boolean;
  adhdCurrentLineText: string;
  onToggleAdhdReader: () => void;
  /** 编辑态内容与控制 */
  editContent: string;
  onEditContentChange: (value: string) => void;
  saving: boolean;
  onSave: () => void;
  onCancelEdit: () => void;
}

/** 内容区域 */
export default function ContentArea({
  noteId,
  status,
  viewMode,
  editMode,
  mdContent,
  htmlContent,
  editPreviewHtml,
  diffData,
  diffLoading,
  markdownRef,
  onMouseUp,
  onRefresh,
  adhdReaderEnabled,
  adhdCurrentLineText,
  onToggleAdhdReader,
  editContent,
  onEditContentChange,
  saving,
  onSave,
  onCancelEdit,
}: ContentAreaProps) {
  if ((status === 'converting' || status === 'uploading') && noteId) {
    return <ProcessingPanel noteId={noteId} status={status} onCancelled={onRefresh} />;
  }

  if (editMode === 'edit') {
    return (
      <EditSplitView
        editContent={editContent}
        onEditContentChange={onEditContentChange}
        saving={saving}
        onSave={onSave}
        onCancel={onCancelEdit}
        previewHtml={editPreviewHtml}
      />
    );
  }

  if (viewMode === 'diff') {
    return <DiffPanel diffData={diffData} diffLoading={diffLoading} />;
  }

  if (mdContent) {
    return (
      <MarkdownReader
        htmlContent={htmlContent}
        markdownRef={markdownRef}
        onMouseUp={onMouseUp}
        adhdReaderEnabled={adhdReaderEnabled}
        adhdCurrentLineText={adhdCurrentLineText}
        onToggleAdhdReader={onToggleAdhdReader}
      />
    );
  }

  return (
    <div className="card" style={{ textAlign: 'center', padding: 'var(--space-xl)' }}>
      <p style={{ color: 'var(--color-text-secondary)' }}>暂无内容</p>
    </div>
  );
}
