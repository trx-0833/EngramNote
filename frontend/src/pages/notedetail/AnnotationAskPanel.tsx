/**
 * @file AI 提问浮层（笔记选区）
 * @description
 * 由 `pages/NoteDetail.tsx` 拆分而来（overhaul-plan 5.5），只做搬运：
 * `viewMode === 'diff'` 时降级为 'original'（diff 正文用的是原始版，
 * 见 `viewMode.ts#getAnnotationViewMode`），其余 props 原样透传。
 * 显示条件（`askAIState && editMode === 'view'`）由页面侧判断。
 */
import NoteAskPanel from '../../components/NoteAskPanel';
import type { AskAIState, ViewMode } from './types';

interface AnnotationAskPanelProps {
  /** 笔记 ID */
  noteId: string;
  /** 笔记标题 */
  noteTitle: string;
  /** 当前浮层状态（选中文本 + 上下文 + 位置） */
  askAIState: AskAIState;
  /** 当前视图模式 */
  viewMode: ViewMode;
  /** 当前显示的 Markdown 原文 */
  markdown: string;
  /** 关闭浮层 */
  onClose: () => void;
}

/** AI 提问浮层：基于当前笔记选区上下文流式提问 */
export default function AnnotationAskPanel({
  noteId,
  noteTitle,
  askAIState,
  viewMode,
  markdown,
  onClose,
}: AnnotationAskPanelProps) {
  return (
    <NoteAskPanel
      noteId={noteId}
      noteTitle={noteTitle}
      initialText={askAIState.text}
      contextBefore={askAIState.contextBefore}
      contextAfter={askAIState.contextAfter}
      viewMode={viewMode === 'diff' ? 'original' : viewMode}
      markdown={markdown}
      pos={askAIState.pos}
      onClose={onClose}
    />
  );
}
