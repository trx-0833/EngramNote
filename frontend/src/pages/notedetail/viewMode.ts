/**
 * @file 笔记详情页的视图/内容判定（纯函数，无 React 依赖）
 * @description
 * 由 `pages/NoteDetail.tsx` 拆分而来（overhaul-plan 5.5），只做搬运：
 * 判定条件与渲染结果保持逐字一致 —— 拆分不改变任何行为。
 */
import type { NoteDetail } from '../../api/client';
import type { ViewMode } from './types';

/**
 * 引用回跳参数（阶段 2.7）
 *
 * QA 页点击引用时带 `?view=clean&cs=<char_start>&ce=<char_end>` 过来。
 * `view=clean` 是必需的：chunk 偏移基于 clean 副本计算，
 * 若页面显示 original 副本，同一组偏移指向的是**另一段文字**。
 */
export interface CitationJump {
  /** 选区起始字符偏移 */
  charStart: number;
  /** 选区结束字符偏移 */
  charEnd: number;
  /** 偏移是否有效（两个都是有限数且 end > start） */
  hasJump: boolean;
}

/** 从 URL 查询串解析引用回跳参数（无效参数解析为 NaN，由 hasJump 判定兜底） */
export function parseCitationJump(searchParams: URLSearchParams): CitationJump {
  const charStart = Number(searchParams.get('cs'));
  const charEnd = Number(searchParams.get('ce'));
  return {
    charStart,
    charEnd,
    hasJump: Number.isFinite(charStart) && Number.isFinite(charEnd) && charEnd > charStart,
  };
}

/**
 * 选择要显示的 Markdown 内容
 *
 * clean 视图下 `clean_md_content` 为空（或未加载）时回退到原始版 ——
 * 与拆分前 `viewMode === 'clean' && note.clean_md_content ? ... : ...` 等价。
 */
export function computeMdContent(note: NoteDetail, viewMode: ViewMode): string {
  return viewMode === 'clean' && note.clean_md_content
    ? note.clean_md_content
    : note.original_md_content || '';
}

/** 是否可以显示清洗版（cleaned/archived/learning_failed 状态都可以查看） */
export function canShowClean(note: NoteDetail): boolean {
  return (
    (note.status === 'cleaned' ||
      note.status === 'archived' ||
      note.status === 'learning_failed') &&
    !!note.clean_md_content
  );
}

/** 是否可以显示 diff（cleaned/archived/learning_failed 状态都可以查看） */
export function canShowDiff(note: NoteDetail): boolean {
  return (
    note.status === 'cleaned' || note.status === 'archived' || note.status === 'learning_failed'
  );
}

/**
 * 批注 record 里的 view_mode 取值范围只有 original/clean，
 * diff 视图下正文用的是原始版（见 computeMdContent），故按 original 落库。
 */
export function getAnnotationViewMode(viewMode: ViewMode): 'original' | 'clean' {
  return viewMode === 'diff' ? 'original' : viewMode;
}
