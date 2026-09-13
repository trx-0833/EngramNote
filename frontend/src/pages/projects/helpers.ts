/**
 * @file 项目页的纯辅助：徽章映射、文件大小格式化、`/projects` 响应归一化
 * @description 自 `pages/Projects.tsx` 拆分（overhaul-plan 5.5），**只搬不改**。
 */
import type { NoteInFolder, Project } from '../../api/client'

/** 来源类型徽章样式映射（与全局 .badge-* 对应） */
export const TYPE_BADGE: Record<string, string> = {
  pdf: 'badge-pdf',
  image: 'badge-image',
  docx: 'badge-docx',
  pptx: 'badge-pptx',
  xlsx: 'badge-xlsx',
  audio: 'badge-audio',
  video: 'badge-video',
  markdown: 'badge-markdown',
}

// 状态样式统一走 utils/labels.ts 的 statusClass()（单一数据源）。
// 本页面此前因"全局缺少 .status-learning-failed / .status-archived"
// 而手写了一张绕过表（把学习失败映射成 failed 红、把归档映射成 converted 绿），
// 导致同一状态在项目页与其余四个页面显示不一致。缺失的 CSS 类已补齐，
// 绕过表已删除 —— 见 docs/overhaul-plan.md §2.8 F-13。

/** 格式化文件大小 */
export function formatSize(bytes: number | null | undefined): string {
  if (!bytes) return '—'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

/**
 * 契约漂移兜底：`/projects` 可能回 204/空体（undefined）或 `{items:[...]}` 包装。
 * 原来直接 `setProjects(data)`，随后 `projects.length` / `projects.map` 在渲染中抛错
 * → 整页被错误边界接走。这里在入口归一成数组，退化成"还没有项目"的空状态；
 * **包装对象拆包后照常渲染**（数据其实在，不能告诉用户"一个都没有"）。
 */
export function unwrapProjects(data: unknown): Project[] {
  const wrapped = (data as { items?: unknown } | null | undefined)?.items
  return Array.isArray(data) ? data : Array.isArray(wrapped) ? (wrapped as Project[]) : []
}

/**
 * 契约漂移兜底：`/projects/{id}` 详情里的 `notes` 与 `/projects` 同源，判据也相同 ——
 * 后端若把数组包成 `{items:[…]}`（或整个字段缺失、类型不对），下游
 * `notes.length` / `notes.map` 会在渲染中抛错 → 整页被错误边界接走。
 * 与 unwrapProjects 一样：**包装对象拆包后照常渲染**（笔记其实在，不能显示"项目暂无笔记"），
 * 真正缺失/非数组才退化成空列表。故意不在每个使用点补 `?.`（那只会把"数组在不在"的判断
 * 散到渲染层，正是 NoteDetail 白屏那次的成因）。
 */
export function unwrapProjectNotes(data: unknown): NoteInFolder[] {
  const wrapped = (data as { items?: unknown } | null | undefined)?.items
  return Array.isArray(data) ? data : Array.isArray(wrapped) ? (wrapped as NoteInFolder[]) : []
}
