/**
 * @file 笔记详情页的共享类型
 * @description
 * 由 `pages/NoteDetail.tsx` 拆分而来（overhaul-plan 5.5），只做搬运：
 * 这里放的是**被多个子模块共同引用**的类型定义，页面私有类型留在各自模块里。
 */

/** 视图模式 */
export type ViewMode = 'original' | 'clean' | 'diff';

/** 编辑模式：view=阅读（可批注/选中），edit=实时分屏编辑 */
export type EditMode = 'view' | 'edit';

/** 选中文本及其前后上下文（用于批注落库与 AI 提问参考） */
export interface SelectionContext {
  text: string;
  contextBefore: string;
  contextAfter: string;
}

/**
 * AI 提问浮层的状态：选中文本 + 选区上下文 + 浮层位置
 * （`null` 表示浮层关闭）
 */
export interface AskAIState {
  text: string;
  contextBefore: string;
  contextAfter: string;
  pos: { x: number; y: number };
}
