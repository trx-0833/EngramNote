/**
 * @file 项目页的纯辅助：徽章映射、文件大小格式化、列表响应归一化
 * @description 自 `pages/Projects.tsx` 拆分（overhaul-plan 5.5），**只搬不改**。
 *
 * 归一化的**判据与上报**在 `pages/contractDrift.ts`（判据只能有一处，理由见该文件）；
 * 本文件只声明"每个接口期望什么形状 + 报给用户时叫什么名字"。容忍度与返回值一字未变：
 * 数组原样、`{items:[…]}` 拆包照常渲染、真正缺失/非数组才退化成空列表。
 */
import type { Note, NoteInFolder, Project } from '../../api/client'
import { coerceArrayPayload, unwrapPageItems } from '../contractDrift'

// 提示文案里的接口名：用户看到的"哪个接口不对"必须能直接拿去和后端对账
const SOURCE_PROJECTS = 'GET /projects'
const SOURCE_PROJECT_DETAIL = 'GET /projects/{id}'
const SOURCE_NOTES = 'GET /notes'

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

/**
 * 格式化文件大小
 *
 * `0` 是**真实大小**（0 字节的笔记就是 0 字节），不是"未知"。原来的 `if (!bytes)`
 * 把 0 和缺失值混为一谈，于是 0 字节的笔记显示成 `—`，与"后端没给这个字段"看起来一样。
 * 现在 `—` 只表示"没有值 / 不是有效数字"（`null` / `undefined` / `NaN`），
 * 0 及以上一律给出真实大小。
 */
export function formatSize(bytes: number | null | undefined): string {
  if (bytes == null || !Number.isFinite(bytes)) return '—'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

/**
 * 契约漂移兜底：`/projects` 可能回 204/空体（undefined）或 `{items:[...]}` 包装。
 * 原来直接 `setProjects(data)`，随后 `projects.length` / `projects.map` 在渲染中抛错
 * → 整页被错误边界接走。这里在入口归一成数组，退化成"还没有项目"的空状态；
 * **包装对象拆包后照常渲染**（数据其实在，不能告诉用户"一个都没有"）。
 * 形状不对会走 `pages/contractDrift.ts` 的提示（宽容解析不等于默默容忍）。
 */
export function unwrapProjects(data: unknown): Project[] {
  return coerceArrayPayload<Project>(data, SOURCE_PROJECTS)
}

/**
 * 契约漂移兜底：`/projects/{id}` 详情里的 `notes` 与 `/projects` 同源，判据也相同 ——
 * 后端若把数组包成 `{items:[…]}`（或整个字段缺失、类型不对），下游
 * `notes.length` / `notes.map` 会在渲染中抛错 → 整页被错误边界接走。
 * 与 unwrapProjects 一样：**包装对象拆包后照常渲染**（笔记其实在，不能显示"项目暂无笔记"），
 * 真正缺失/非数组才退化成空列表。故意不在每个使用点补 `?.`（那只会把"数组在不在"的判断
 * 散到渲染层，正是 NoteDetail 白屏那次的成因）。
 *
 * 调用方注意：**"详情还没加载"不要传进来**（那样 `undefined` 会被报成漂移）。
 * 没有详情就是没有笔记列表，见 `ProjectCard` 的 `detail ? … : []`。
 */
export function unwrapProjectNotes(data: unknown): NoteInFolder[] {
  return coerceArrayPayload<NoteInFolder>(data, SOURCE_PROJECT_DETAIL)
}

/**
 * `/notes` 候选笔记：与上面两条不同，这个接口的**分页对象本身就是契约**
 * （`{items,total,page,page_size}`），所以判据是"`items` 是不是数组"。
 * 原来直接 `data.items.filter(...)`：字段缺失/类型不对就在渲染前抛错，
 * 整段虽有 `try/catch` 兜住，但错误会显示成"加载候选笔记失败" ——
 * 与"接口形状不对"混为一谈。
 */
export function unwrapCandidateNotes(data: unknown): Note[] {
  return unwrapPageItems<Note>(data, SOURCE_NOTES)
}
