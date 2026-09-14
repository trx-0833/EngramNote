/**
 * @file 复习流程的回车键约定（5.12：两条复习流程的统一件）
 *
 * ## 两条流程共享的约定
 *
 * 回车 = "推进当前这一步"：答题复习是"提交答案 / 下一题"，卡片复习是
 * "显示答案 / 下一张"。交互模型相同，处理方式就必须相同 —— 此前两份实现
 * 只差一个守卫，而那个守卫恰好是关键的。
 *
 * ## 为什么必须跳过按钮目标（这一条修掉了一个真实缺陷）
 *
 * 答题复习此前无差别 `preventDefault()`：焦点落在按钮上时按回车，
 * **既不会激活按钮、又会推进流程**（`preventDefault` 抑制了按钮的原生
 * 回车激活）。于是键盘用户：
 *
 * - 焦点在"查看原文语境"上按回车 → 原文没展开，人被带到下一题；
 * - 焦点在四档自评上按回车 → 自评没提交（只有 Space 能提交）。
 *
 * 卡片复习在附录 AA.7 已经踩到同一个坑并加了守卫（原注："让按钮走自己的
 * 原生回车行为，避免一次回车触发两件事"）。这里把它提升为两条流程共同的
 * 约定：**按钮目标一律放行**。
 *
 * ## 为什么"焦点收回容器"是可选项
 *
 * 卡片复习页上没有任何输入控件：点完按钮后焦点落到 body，键盘事件再也
 * 冒泡不到容器，回车键会"时灵时不灵"（附录 AA.7）。所以那里必须把焦点
 * 收回容器（容器 `tabIndex={-1}`：只允许程序聚焦，不插进 Tab 顺序）。
 *
 * 答题复习页恰恰相反：焦点应当留在填空/简答输入框里，每翻一题都抢一次
 * 焦点会把用户正在打字的光标踢出去。所以焦点由 `refocusKey` 控制，
 * 不传就完全不碰焦点。
 *
 * ⚠️ 这个 hook 放在 `components/quiz/` 而不是 `src/hooks/`：本轮的改动范围
 * 限定在 `pages/**` 与 `components/**`，而它服务的正是这一组答题/复习组件。
 */
import { useCallback, useEffect, useRef, type KeyboardEvent, type RefObject } from 'react'

interface UseReviewKeyboardOptions {
  /** 回车时要推进的那一步（语义由页面决定） */
  onEnter: () => void
  /**
   * 变化时把焦点收回容器；不传则完全不接管焦点（见文件头）。
   * 传一个随"当前项 / 当前阶段"变化的字符串即可。
   */
  refocusKey?: string
}

interface UseReviewKeyboardResult {
  /** 挂到外层容器上的 ref（配合 `tabIndex={-1}` 使用） */
  containerRef: RefObject<HTMLDivElement>
  /** 挂到同一个容器的 `onKeyDown` */
  handleKeyDown: (e: KeyboardEvent<HTMLElement>) => void
}

export function useReviewKeyboard({
  onEnter,
  refocusKey,
}: UseReviewKeyboardOptions): UseReviewKeyboardResult {
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (refocusKey === undefined) return
    containerRef.current?.focus()
  }, [refocusKey])

  const handleKeyDown = useCallback((e: KeyboardEvent<HTMLElement>) => {
    if (e.key !== 'Enter' || e.shiftKey) return
    // 焦点在按钮上时放行：让按钮走自己的原生回车激活（见文件头）
    if (e.target instanceof HTMLButtonElement) return
    e.preventDefault()
    onEnter()
  }, [onEnter])

  return { containerRef, handleKeyDown }
}
