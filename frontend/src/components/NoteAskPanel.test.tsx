/**
 * @file AI 提问浮层的窄屏定位测试（overhaul-plan 5.10）
 *
 * ## 为什么这个"布局"能在 jsdom 里测
 *
 * `NoteAskPanel` 的位置**不是 CSS 算的，是 JS 算的**：左边界由
 * `window.innerWidth` 与面板宽度在渲染期夹取出来，写进内联样式。
 * 内联值是 DOM 的一部分，jsdom 读得到 —— 所以"面板是否整个落在视口内"
 * 这条真正的窄屏缺陷（F-16：375px 屏上被推出屏幕外，选段提问完全够不着）
 * 可以用断言钉住，而不是只能靠人眼。
 *
 * 桌面端行为必须**逐像素不变**：宽屏下夹取区间与改动前完全一致，
 * 下面第二个用例专门守住这一点。
 *
 * ## jsdom 测不到的
 *
 * 拖拽（mousedown → mousemove）与 `maxHeight: 60vh` 的实际裁切效果需要真浏览器；
 * 本文件只覆盖"静态渲染时算出来的 left/width"。
 *
 * ## 为什么这里用模块导出的类名找元素（overhaul-plan 5.6 第二批）
 *
 * 面板根节点的类名原来是字面量 `.ask-ai-panel`，随 `.ask-ai-*` 迁进
 * CSS Modules 后被哈希，字面量查询必然失效（拿到 null → 读 `style` 直接抛错）。
 * 这里**没有**改成语义查询，是因为本文件断言的是**内联几何**（`style.left` /
 * `style.width`）而不是用户可见语义：面板根节点在 a11y 树上没有稳定角色，
 * 为了测试给它加 `role="dialog"` 属于产品/无障碍改动（还要连带考虑焦点管理），
 * 不该混在一次"纯搬家"里 —— 与试点轮把内联样式重构推后是同一个判断。
 * 规范 §6 给了这条路：`import styles from './X.module.css'` 后用 `styles.foo`，
 * 测试环境下它返回真实类名（实测 `_askAiPanel_<hash>`）。
 */
import { render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import NoteAskPanel from './NoteAskPanel'
// 面板根节点的类名（哈希后无法用字面量查询，理由见文件头）
import styles from './NoteAskPanel.module.css'

// 组件顶层 import 了流式问答接口；本文件不提交任何问题，桩掉避免真实请求
vi.mock('../api/notes', () => ({ askNoteQuestionStream: vi.fn() }))

const DEFAULT_WIDTH = 1024

function setViewportWidth(width: number) {
  Object.defineProperty(window, 'innerWidth', { configurable: true, writable: true, value: width })
}

/** 渲染浮层并返回它的内联几何（left / width） */
function renderPanelGeometry(viewportWidth: number, anchorX = 100) {
  setViewportWidth(viewportWidth)
  render(
    <NoteAskPanel
      noteId="note-1"
      noteTitle="浮充的定义"
      initialText="蓄电池的一种运行方式"
      contextBefore=""
      contextAfter=""
      viewMode="clean"
      markdown="蓄电池的一种运行方式，端电压保持恒定。"
      pos={{ x: anchorX, y: 300 }}
      onClose={vi.fn()}
    />,
  )
  const panel = document.querySelector(`.${styles.askAiPanel}`) as HTMLElement
  return {
    left: Number.parseFloat(panel.style.left),
    width: Number.parseFloat(panel.style.width),
  }
}

afterEach(() => {
  setViewportWidth(DEFAULT_WIDTH)
})

describe('AI 提问浮层：窄屏必须整体落在视口内', () => {
  it('★ 375px 视口（iPhone SE/8 逻辑宽度）：面板不越出左右边界', () => {
    const { left, width } = renderPanelGeometry(375)

    // 面板宽度不能超过视口（留 12px 边距），否则再怎么做夹取也会溢出
    expect(width).toBeLessThanOrEqual(375 - 24)
    // translate(-50%) 之后的实际左右边缘
    expect(left - width / 2).toBeGreaterThanOrEqual(0)
    expect(left + width / 2).toBeLessThanOrEqual(375)
  })

  it('★ 窄视口下锚点靠右时也不越界（选区常在正文右侧）', () => {
    const { left, width } = renderPanelGeometry(360, 340)

    expect(left - width / 2).toBeGreaterThanOrEqual(0)
    expect(left + width / 2).toBeLessThanOrEqual(360)
  })

  it('★ 桌面端（1024px）行为逐像素不变：宽度仍为 480，锚点 100 被夹到 248', () => {
    const { left, width } = renderPanelGeometry(DEFAULT_WIDTH)

    expect(width).toBe(480)
    expect(left).toBe(248)
  })
})
