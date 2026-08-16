/**
 * @file ADHD Reader 专注阅读 Hook
 * @description
 * 在 Markdown 阅读页开启“ADHD Reader”模式：
 * 1. 通过摄像头（WebGazer）估算用户正在看哪一行；
 * 2. 把该行所在的 Markdown 块设为当前块；
 * 3. 当前块保持清晰，其他块高斯模糊，帮助用户聚焦。
 *
 * 当前实现：
 * - 摄像头不可用或用户拒绝授权时，自动降级为鼠标/点击演示模式；
 * - WebGazer 脚本按需从官方 CDN 加载，加载失败不影响阅读功能；
 * - 聚焦逻辑基于“离视线最近的块”，后续可继续细化到“行级高亮”。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import type { RefObject } from 'react'

export type AdhdGazeSource = 'off' | 'mouse' | 'camera'

interface GazePoint {
  x: number
  y: number
}

const WEBGAZER_SRC = 'https://webgazer.cs.brown.edu/webgazer.js'

function isElement(node: ChildNode): node is HTMLElement {
  return node.nodeType === Node.ELEMENT_NODE
}

function loadWebGazer(): Promise<any> {
  const existing = (window as any).webgazer
  if (existing) return Promise.resolve(existing)

  return new Promise((resolve, reject) => {
    const script = document.createElement('script')
    script.src = WEBGAZER_SRC
    script.async = true
    script.onload = () => resolve((window as any).webgazer)
    script.onerror = () => reject(new Error('WebGazer 加载失败'))
    document.head.appendChild(script)
  })
}

/**
 * 管理 ADHD Reader 的开关、视线来源和 Markdown 块聚焦。
 *
 * @param containerRef - 指向 Markdown 渲染容器（通常是 article.markdown-body）
 * @returns 当前状态与控制函数
 */
export function useAdhdReader(containerRef: RefObject<HTMLElement>) {
  const [enabled, setEnabled] = useState(false)
  const [gazeSource, setGazeSource] = useState<AdhdGazeSource>('off')
  const [calibrating, setCalibrating] = useState(false)

  const enabledRef = useRef(enabled)
  const gazeSourceRef = useRef<AdhdGazeSource>(gazeSource)
  const lastPointRef = useRef<GazePoint | null>(null)
  const currentBlockRef = useRef<HTMLElement | null>(null)
  const webgazerRef = useRef<any>(null)

  enabledRef.current = enabled
  gazeSourceRef.current = gazeSource

  const applyFocus = useCallback((point: GazePoint | null) => {
    const root = containerRef.current
    if (!root || !enabledRef.current) return

    const blocks = Array.from(root.children).filter(isElement)
    if (blocks.length === 0) return

    let target = blocks[0]
    if (point) {
      let minDist = Number.POSITIVE_INFINITY
      for (const el of blocks) {
        const rect = el.getBoundingClientRect()
        const centerY = rect.top + rect.height / 2
        const dist = Math.abs(centerY - point.y)
        if (dist < minDist) {
          minDist = dist
          target = el
        }
      }
    }

    if (currentBlockRef.current) {
      currentBlockRef.current.classList.remove('adhd-current-block')
    }
    target.classList.add('adhd-current-block')
    currentBlockRef.current = target
  }, [containerRef])

  const applyFocusRef = useRef(applyFocus)
  applyFocusRef.current = applyFocus

  // 给所有 Markdown 顶层块打上类名；内容变化时通过 MutationObserver 自动同步
  useEffect(() => {
    const root = containerRef.current
    if (!root) return

    const sync = () => {
      const blocks = Array.from(root.children).filter(isElement)
      blocks.forEach(el => el.classList.add('adhd-block'))

      if (enabledRef.current) {
        root.classList.add('adhd-reader-active')
        applyFocusRef.current?.(lastPointRef.current)
      } else {
        root.classList.remove('adhd-reader-active')
        blocks.forEach(el => el.classList.remove('adhd-current-block'))
      }
    }

    sync()
    const observer = new MutationObserver(sync)
    observer.observe(root, { childList: true, subtree: false })
    return () => observer.disconnect()
  }, [containerRef, enabled])

  // 鼠标/点击降级模式（摄像头未启用时可用）
  useEffect(() => {
    if (!enabled) return
    const root = containerRef.current
    if (!root) return

    const onPointer = (e: MouseEvent) => {
      // 摄像头模式开启后忽略鼠标，避免两者互相干扰
      if (gazeSourceRef.current === 'camera') return
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

  const stopCamera = useCallback(() => {
    if (webgazerRef.current?.end) {
      try {
        webgazerRef.current.end()
      } catch {
        // 忽略停止时的异常
      }
    }
    webgazerRef.current = null
    setGazeSource('off')
    setCalibrating(false)
  }, [])

  const startCamera = useCallback(async () => {
    setCalibrating(true)
    try {
      const webgazer = await loadWebGazer()
      if (!enabledRef.current) return
      webgazerRef.current = webgazer

      await webgazer.begin()
      if (!enabledRef.current) {
        if (webgazer.end) {
          try {
            webgazer.end()
          } catch {
            // 忽略停止异常
          }
        }
        webgazerRef.current = null
        setGazeSource('off')
        setCalibrating(false)
        return
      }

      webgazer.showVideo?.(true)
      webgazer.showFaceOverlay?.(true)
      webgazer.setGazeListener?.((data: any) => {
        if (data && typeof data.x === 'number' && typeof data.y === 'number') {
          lastPointRef.current = { x: data.x, y: data.y }
          applyFocusRef.current?.(lastPointRef.current)
        }
      })

      setGazeSource('camera')
      setCalibrating(false)
    } catch (err) {
      if (!enabledRef.current) return
      console.warn('[ADHD Reader] 摄像头眼动不可用，已自动切换为鼠标/点击演示模式', err)
      setGazeSource('mouse')
      setCalibrating(false)
    }
  }, [])

  const enable = useCallback(() => {
    setEnabled(true)
    void startCamera()
  }, [startCamera])

  const disable = useCallback(() => {
    setEnabled(false)
    stopCamera()
  }, [stopCamera])

  const toggle = useCallback(() => {
    if (enabledRef.current) {
      disable()
    } else {
      enable()
    }
  }, [disable, enable])

  // 组件卸载时停止摄像头
  useEffect(() => {
    return () => {
      if (webgazerRef.current?.end) {
        try {
          webgazerRef.current.end()
        } catch {
          // 忽略清理异常
        }
      }
    }
  }, [])

  return {
    enabled,
    gazeSource,
    calibrating,
    toggle,
    disable,
  }
}
