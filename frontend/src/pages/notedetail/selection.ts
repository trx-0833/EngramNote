/**
 * @file 选区上下文计算（纯 DOM helper）
 * @description
 * 由 `pages/NoteDetail.tsx` 拆分而来（overhaul-plan 5.5），只做搬运：
 * 计算逻辑（含 50 字符窗口、`slice` 方向、`trim` 位置）与拆分前逐字一致。
 */
import type { SelectionContext } from './types'

/**
 * 计算选区上下文：选中文本及其前后各 windowChars 字符（用于 AI 提问参考）
 *
 * `container` 为未就绪时返回空上下文（调用方据此判定"没有可用选区"）。
 */
export function computeSelectionContext(
  container: HTMLElement | null,
  range: Range,
  windowChars: number,
): SelectionContext {
  if (!container) return { text: '', contextBefore: '', contextAfter: '' }
  const text = range.toString().trim()
  // 获取选区前后的文本作为上下文
  const beforeNode = document.createRange()
  beforeNode.selectNodeContents(container)
  beforeNode.setEnd(range.startContainer, range.startOffset)
  const contextBefore = beforeNode.toString().slice(-windowChars)
  const afterNode = document.createRange()
  afterNode.selectNodeContents(container)
  afterNode.setStart(range.endContainer, range.endOffset)
  const contextAfter = afterNode.toString().slice(0, windowChars)
  return { text, contextBefore, contextAfter }
}

/**
 * 计算批注落库用的上下文：选区前后各 50 字符
 *
 * 与 `computeSelectionContext` 分开保留：批注的窗口是 50（存库），
 * AI 提问的窗口是 1500（喂模型），两者语义不同，不要合并。
 */
export function computeAnnotationContext(
  container: HTMLElement | null,
  range: Range,
): { contextBefore: string; contextAfter: string } | null {
  if (!container) return null

  // 获取选区前后的文本作为上下文
  const beforeNode = document.createRange()
  beforeNode.selectNodeContents(container)
  beforeNode.setEnd(range.startContainer, range.startOffset)
  const contextBefore = beforeNode.toString().slice(-50)

  const afterNode = document.createRange()
  afterNode.selectNodeContents(container)
  afterNode.setStart(range.endContainer, range.endOffset)
  const contextAfter = afterNode.toString().slice(0, 50)

  return { contextBefore, contextAfter }
}
