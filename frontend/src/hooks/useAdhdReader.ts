/**
 * @file ADHD Reader 专注阅读 Hook
 * @description
 * 在 Markdown 阅读页开启“ADHD Reader”模式后，用鼠标实现渐变遮罩聚焦阅读：
 * 1. 鼠标所指的行保持清晰，其余块按与阅读位置的距离渐变模糊（越近越清晰、越远越模糊）；
 * 2. 行级标记是位于行盒下方的下划线，完全不遮挡文字；
 * 3. 实时读取并显示鼠标所在行的文本内容。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import type { RefObject } from 'react'

interface Point2D {
  x: number
  y: number
}

interface LineRect {
  top: number
  bottom: number
  left: number
  right: number
}

/** 块切换滞回：需连续 N 帧落在新块才真正切换 */
const BLOCK_SWITCH_HYSTERESIS = 3

/** 渐变遮罩：块与阅读行的垂直距离达到该像素值后模糊达到最大 */
const GRADIENT_RANGE = 280
/** 渐变遮罩的最大模糊半径（px） */
const MAX_BLUR = 5
/** 渐变遮罩的最小不透明度（距离最远时） */
const MIN_OPACITY = 0.45

/** 排除行级标记等内部元素后的顶层 Markdown 块 */
function isBlockElement(el: Element): el is HTMLElement {
  return el instanceof HTMLElement && !el.classList.contains('adhd-line-marker')
}

/** 计算一个块内的所有视觉行（Range.getClientRects 按行盒返回） */
function getBlockLineRects(block: HTMLElement): LineRect[] {
  const range = document.createRange()
  range.selectNodeContents(block)
  const lines: LineRect[] = []
  for (const r of Array.from(range.getClientRects())) {
    if (r.width === 0 && r.height === 0) continue
    const last = lines[lines.length - 1]
    if (last && Math.abs(last.top - r.top) < 2 && Math.abs(last.bottom - r.bottom) < 2) {
      last.left = Math.min(last.left, r.left)
      last.right = Math.max(last.right, r.right)
    } else {
      lines.push({ top: r.top, bottom: r.bottom, left: r.left, right: r.right })
    }
  }
  return lines
}

/** 块内没有可计算的视觉行时，退化为整个块 */
function blockToLineRect(block: HTMLElement): LineRect {
  const r = block.getBoundingClientRect()
  return { top: r.top, bottom: r.bottom, left: r.left, right: r.right }
}

/**
 * 渐变遮罩：按各块与当前阅读行的垂直距离写入内联模糊度与透明度。
 * 距离 0（当前块/阅读行所在块）完全清晰，距离越远越模糊，超过
 * GRADIENT_RANGE 后达到 MAX_BLUR / MIN_OPACITY 上限。
 */
function updateGradient(root: HTMLElement, focus: LineRect) {
  const blocks = Array.from(root.children).filter(isBlockElement)
  for (const el of blocks) {
    const r = el.getBoundingClientRect()
    let dist = 0
    if (r.bottom < focus.top) dist = focus.top - r.bottom
    else if (r.top > focus.bottom) dist = r.top - focus.bottom
    const ratio = Math.min(1, dist / GRADIENT_RANGE)
    if (ratio <= 0.01) {
      el.style.filter = 'none'
      el.style.opacity = '1'
    } else {
      el.style.filter = `blur(${(ratio * MAX_BLUR).toFixed(2)}px)`
      el.style.opacity = (1 - ratio * (1 - MIN_OPACITY)).toFixed(3)
    }
  }
}

/** 找到包含鼠标位置的行；没有包含的则取最近行 */
function findLineRect(block: HTMLElement, point: Point2D): LineRect {
  const lines = getBlockLineRects(block)
  if (lines.length === 0) return blockToLineRect(block)
  let best = lines[0]
  let bestDist = Number.POSITIVE_INFINITY
  for (const line of lines) {
    if (point.y >= line.top && point.y <= line.bottom) return line
    const dist = Math.abs((line.top + line.bottom) / 2 - point.y)
    if (dist < bestDist) {
      bestDist = dist
      best = line
    }
  }
  return best
}

/** 读取鼠标所在行的真实文本（通过 caretRangeFromPoint + 同一行内扩展开区间） */
function extractLineText(block: HTMLElement, point: Point2D): string | null {
  try {
    const range = document.caretRangeFromPoint(point.x, point.y)
    if (!range) return null
    let node: Node = range.startContainer
    if (node.nodeType !== Node.TEXT_NODE) {
      const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT)
      const textNode = walker.nextNode()
      if (!textNode) return null
      node = textNode
    }
    const text = (node as Text).data
    if (!text || !text.trim()) return null
    const baseOffset = node === range.startContainer ? range.startOffset : 0

    const topAt = (offset: number): number => {
      const r = document.createRange()
      r.setStart(node, Math.max(0, Math.min(offset, text.length)))
      r.collapse(true)
      return r.getBoundingClientRect().top
    }

    const lineTop = topAt(baseOffset)
    let start = baseOffset
    while (start > 0 && Math.abs(topAt(start - 1) - lineTop) < 2) start--
    let end = baseOffset
    while (end < text.length && Math.abs(topAt(end + 1) - lineTop) < 2) end++
    const lineText = text.slice(start, end).trim()
    return lineText.length > 0 ? lineText : null
  } catch {
    const fallback = block.textContent?.trim() || ''
    return fallback.length > 0 ? fallback.slice(0, 60) : null
  }
}

/**
 * 管理 ADHD Reader 的开关、鼠标遮罩（模糊/高亮）与“行级文本捕捉”。
 *
 * @param containerRef - 指向 Markdown 渲染容器（通常是 article.markdown-body）
 * @returns 当前状态与控制函数
 */
export function useAdhdReader(containerRef: RefObject<HTMLElement>) {
  const [enabled, setEnabled] = useState(false)
  const [currentLineText, setCurrentLineText] = useState('')

  const enabledRef = useRef(enabled)
  const lastPointRef = useRef<Point2D | null>(null)
  const currentBlockRef = useRef<HTMLElement | null>(null)
  const pendingBlockRef = useRef<HTMLElement | null>(null)
  const pendingSwitchCountRef = useRef(0)
  const markerRef = useRef<HTMLDivElement | null>(null)
  const lastLineTextRef = useRef('')

  enabledRef.current = enabled

  const ensureMarker = useCallback((root: HTMLElement): HTMLDivElement => {
    if (!markerRef.current || !root.contains(markerRef.current)) {
      const marker = document.createElement('div')
      marker.className = 'adhd-line-marker'
      root.appendChild(marker)
      markerRef.current = marker
    }
    return markerRef.current
  }, [])

  const applyFocus = useCallback((point: Point2D | null) => {
    const root = containerRef.current
    if (!root || !enabledRef.current) return

    const blocks = Array.from(root.children).filter(isBlockElement)
    if (blocks.length === 0) return

    let target: HTMLElement | null = null
    if (point) {
      const articleRect = root.getBoundingClientRect()
      const hMargin = Math.max(120, articleRect.width * 0.12)
      const withinX = point.x >= articleRect.left - hMargin && point.x <= articleRect.right + hMargin
      const withinY = point.y >= articleRect.top - 32 && point.y <= articleRect.bottom + 32
      // 鼠标不在正文区域（例如停在工具栏）时，保持当前高亮不动
      if (!withinX || !withinY) return

      let nearest = blocks[0]
      let minDist = Number.POSITIVE_INFINITY
      for (const el of blocks) {
        const rect = el.getBoundingClientRect()
        const dist = Math.abs(rect.top + rect.height / 2 - point.y)
        if (dist < minDist) {
          minDist = dist
          nearest = el
        }
      }

      // 滞回：连续多帧落在同一新块才切换，抑制块级闪烁
      if (pendingBlockRef.current !== nearest) {
        pendingBlockRef.current = nearest
        pendingSwitchCountRef.current = 1
      } else {
        pendingSwitchCountRef.current += 1
      }
      target =
        currentBlockRef.current && pendingSwitchCountRef.current < BLOCK_SWITCH_HYSTERESIS
          ? currentBlockRef.current
          : nearest
    } else {
      target = blocks[0]
      pendingBlockRef.current = null
    }

    if (currentBlockRef.current !== target) {
      currentBlockRef.current?.classList.remove('adhd-current-block')
      target.classList.add('adhd-current-block')
      currentBlockRef.current = target
      pendingBlockRef.current = null
      pendingSwitchCountRef.current = 0
    }

    // 计算当前阅读区域（行级优先），用于定位下划线标记并驱动渐变模糊
    let focus: LineRect
    if (point) {
      focus = findLineRect(target, point)
    } else {
      const r = target.getBoundingClientRect()
      focus = { top: r.top, bottom: r.bottom, left: r.left, right: r.right }
    }

    // 渐变遮罩：越靠近阅读行的块越清晰，越远越模糊
    updateGradient(root, focus)

    // 行级下划线：定位在行盒下方 1px 的行距空隙里，不遮挡任何文字
    if (point) {
      const marker = ensureMarker(root)
      const articleRect = root.getBoundingClientRect()
      marker.style.display = 'block'
      marker.style.top = Math.max(0, focus.bottom - articleRect.top + 1) + 'px'
      marker.style.left = Math.max(0, focus.left - articleRect.left) + 'px'
      marker.style.width = Math.max(4, focus.right - focus.left) + 'px'
      marker.style.height = '3px'

      const lineText = extractLineText(target, point)
      if (lineText && lineText !== lastLineTextRef.current) {
        lastLineTextRef.current = lineText
        setCurrentLineText(lineText)
      }
    }
  }, [containerRef, ensureMarker])

  const applyFocusRef = useRef(applyFocus)
  applyFocusRef.current = applyFocus

  // 给所有 Markdown 顶层块打上类名；内容变化时通过 MutationObserver 自动同步
  useEffect(() => {
    const root = containerRef.current
    if (!root) return

    const sync = () => {
      const blocks = Array.from(root.children).filter(isBlockElement)
      blocks.forEach(el => el.classList.add('adhd-block'))

      if (enabledRef.current) {
        root.classList.add('adhd-reader-active')
        applyFocusRef.current?.(lastPointRef.current)
      } else {
        root.classList.remove('adhd-reader-active')
        blocks.forEach(el => {
          el.classList.remove('adhd-current-block')
          el.style.filter = ''
          el.style.opacity = ''
        })
        markerRef.current?.remove()
        markerRef.current = null
      }
    }

    sync()
    const observer = new MutationObserver(sync)
    observer.observe(root, { childList: true, subtree: false })
    return () => observer.disconnect()
  }, [containerRef, enabled])

  // 鼠标遮罩模式：跟随鼠标位置高亮所在行/块
  useEffect(() => {
    if (!enabled) return
    const root = containerRef.current
    if (!root) return

    const onPointer = (e: MouseEvent) => {
      lastPointRef.current = { x: e.clientX, y: e.clientY }
      applyFocusRef.current?.(lastPointRef.current)
    }

    root.addEventListener('mousemove', onPointer)
    root.addEventListener('click', onPointer)
    return () => {
      root.removeEventListener('mousemove', onPointer)
      root.removeEventListener('click', onPointer)
    }
  }, [enabled, containerRef])

  const enable = useCallback(() => {
    setEnabled(true)
  }, [])

  const disable = useCallback(() => {
    setEnabled(false)
    markerRef.current?.remove()
    markerRef.current = null
    lastLineTextRef.current = ''
    setCurrentLineText('')
  }, [])

  const toggle = useCallback(() => {
    if (enabledRef.current) {
      disable()
    } else {
      enable()
    }
  }, [disable, enable])

  return {
    enabled,
    currentLineText,
    toggle,
    disable,
  }
}
