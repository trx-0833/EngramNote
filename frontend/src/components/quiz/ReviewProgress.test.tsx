/**
 * @file 复习会话进度条的测试（5.12：两条复习流程共用的进度显示）
 *
 * 这里钉的是那条**曾经被四个页面各写一遍**的宽度公式：
 * `(当前序号 + 已结账 ? 1 : 0) / 总数`。
 *
 * 它值钱的不是像素，而是"已结账的这一张算不算进度"：算错了用户只会觉得
 * 进度条涨得慢一点，没有任何报错。两种排版（一行式 / 带标题）必须共用
 * 同一个公式 —— 这正是把它们收进一个组件的原因。
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import ReviewProgress from './ReviewProgress'

function fillWidth(container: HTMLElement): string {
  const fill = container.querySelector('.progress-bar-fill')
  if (!(fill instanceof HTMLElement)) throw new Error('没有渲染 .progress-bar-fill')
  return fill.style.width
}

describe('ReviewProgress 的进度公式', () => {
  it('当前项未结账时只算已完成的那些', () => {
    const { container } = render(
      <ReviewProgress index={2} total={5} done={false} label="3 / 5" />,
    )
    expect(fillWidth(container)).toBe('40%')
  })

  it('★ 当前项已结账时把它计入进度（少算这一张就是四页各自漂移过的地方）', () => {
    const { container } = render(
      <ReviewProgress index={2} total={5} done label="3 / 5" />,
    )
    expect(fillWidth(container)).toBe('60%')
  })

  it('末项结账后是 100%', () => {
    const { container } = render(
      <ReviewProgress index={4} total={5} done label="5 / 5" />,
    )
    expect(fillWidth(container)).toBe('100%')
  })

  it('★ 两种排版共用同一个公式（带标题的卡片复习页不会另算一套）', () => {
    const row = render(<ReviewProgress index={1} total={4} done label="2 / 4" />)
    const rowWidth = fillWidth(row.container)
    row.unmount()

    const stacked = render(
      <ReviewProgress index={1} total={4} done label="2 / 4" title="卡片复习" />,
    )
    expect(fillWidth(stacked.container)).toBe(rowWidth)
    expect(rowWidth).toBe('50%')
  })

  it('总数缺失时给 0% 而不是 NaN%（"渲染坏了"比"没有进度"糟得多）', () => {
    const { container } = render(
      <ReviewProgress index={0} total={0} done={false} label="0 / 0" />,
    )
    expect(fillWidth(container)).toBe('0%')
  })
})

describe('ReviewProgress 的两种排版', () => {
  it('一行式：计数在左、补充计数在右', () => {
    render(
      <ReviewProgress
        index={0}
        total={3}
        done={false}
        label="1 / 3"
        trailing="0/0 正确"
      />,
    )
    expect(screen.getByText('1 / 3')).toBeInTheDocument()
    expect(screen.getByText('0/0 正确')).toBeInTheDocument()
    expect(screen.queryByRole('heading')).not.toBeInTheDocument()
  })

  it('带标题式：标题成为页面标题，计数在标题行右侧；没有补充计数的位置', () => {
    render(
      <ReviewProgress
        index={0}
        total={3}
        done={false}
        label="第 1 / 3 张 · 到期共 1183 张"
        trailing="不该出现"
        title="卡片复习"
      />,
    )
    expect(screen.getByRole('heading', { name: '卡片复习' })).toBeInTheDocument()
    expect(screen.getByText('第 1 / 3 张 · 到期共 1183 张')).toBeInTheDocument()
    expect(screen.queryByText('不该出现')).not.toBeInTheDocument()
  })
})
