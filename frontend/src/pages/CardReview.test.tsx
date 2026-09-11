/**
 * @file 卡片复习页的交互测试（阶段 3.12 的前端一半，附录 AA）
 *
 * 这一页有三条**必须成立**的行为，而它们都只在运行时才看得出来：
 *
 * 1. 正文在翻面前不可见 —— 否则"先回忆再自评"退化成看答案，
 *    产生的自评数据是假的，而它会真的改变调度；
 * 2. 未自评不许跳到下一张 —— 否则卡片停在"看了但没结账"的状态；
 * 3. `total` 大于本页条数时必须提示"还有更多" —— 真库 1183 张全到期，
 *    而接口默认一次只给 20 张，不提示就会让用户以为清空了。
 */
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { getDueCards, submitCardReview, type DueCard } from '../api/review'
import CardReview from './CardReview'

vi.mock('../api/review', () => ({
  getDueCards: vi.fn(),
  submitCardReview: vi.fn(),
}))
vi.mock('../components/Toast', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}))
// 卡片里会渲染原文语境组件；它按需请求卡片详情，测试里不展开
vi.mock('../api/qa', () => ({ getKnowledgeCard: vi.fn() }))

const mockedDue = vi.mocked(getDueCards)
const mockedSubmit = vi.mocked(submitCardReview)

function makeCard(over: Partial<DueCard> = {}): DueCard {
  return {
    card_id: 'card-1',
    title: '浮充的定义',
    content: '蓄电池的一种运行方式，端电压保持恒定。',
    summary: '浮充 = 恒压运行',
    card_type: 'concept',
    chapter_title: '第一章',
    note_id: 'note-1',
    mastery_level: 57.4,
    interval_days: 6,
    repetition: 2,
    easiness_factor: 2.5,
    next_review_at: null,
    review_count: 3,
    lapses: 1,
    ...over,
  }
}

function renderPage() {
  return render(
    <MemoryRouter>
      <CardReview />
    </MemoryRouter>,
  )
}

describe('CardReview 的加载与列表', () => {
  beforeEach(() => {
    mockedDue.mockReset()
    mockedSubmit.mockReset()
  })

  it('加载完成后显示第一张卡的标题', async () => {
    mockedDue.mockResolvedValue({ items: [makeCard()], total: 1 })
    renderPage()
    expect(await screen.findByText('浮充的定义')).toBeInTheDocument()
  })

  it('★ 正文默认隐藏（否则"先回忆"变成看答案）', async () => {
    mockedDue.mockResolvedValue({ items: [makeCard()], total: 1 })
    renderPage()
    await screen.findByText('浮充的定义')

    expect(screen.queryByText(/蓄电池的一种运行方式/)).not.toBeInTheDocument()
    expect(screen.queryByText(/浮充 = 恒压运行/)).not.toBeInTheDocument()
    // 此时也**不该**出现自评按钮：还没翻面，用户无从对照
    expect(screen.queryByText('想起来了')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '显示答案' })).toBeInTheDocument()
  })

  it('点"显示答案"后才出现正文与四档自评', async () => {
    mockedDue.mockResolvedValue({ items: [makeCard()], total: 1 })
    renderPage()
    await screen.findByText('浮充的定义')

    await userEvent.click(screen.getByRole('button', { name: '显示答案' }))

    expect(screen.getByText(/蓄电池的一种运行方式/)).toBeInTheDocument()
    expect(screen.getByText(/浮充 = 恒压运行/)).toBeInTheDocument()
    expect(screen.getByText('想起来了')).toBeInTheDocument()
  })

  it('★ 队列为空时给出可操作的说明，而不是空白页', async () => {
    mockedDue.mockResolvedValue({ items: [], total: 0 })
    renderPage()
    expect(await screen.findByText('没有到期的卡片')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '去看知识卡片' })).toBeInTheDocument()
  })

  it('★ 到期总数大于本页条数时提示"还有更多"（真库 1183 张全到期）', async () => {
    mockedDue.mockResolvedValue({ items: [makeCard()], total: 1183 })
    renderPage()
    await screen.findByText('浮充的定义')
    expect(screen.getByText(/到期共 1183 张/)).toBeInTheDocument()
  })

  it('队列已清空时不显示"还有更多"', async () => {
    mockedDue.mockResolvedValue({ items: [makeCard()], total: 1 })
    renderPage()
    await screen.findByText('浮充的定义')
    expect(screen.queryByText(/到期共/)).not.toBeInTheDocument()
  })
})

describe('CardReview 的自评与调度反馈', () => {
  beforeEach(() => {
    mockedDue.mockReset()
    mockedSubmit.mockReset()
    mockedDue.mockResolvedValue({ items: [makeCard()], total: 1 })
  })

  async function reveal() {
    renderPage()
    await screen.findByText('浮充的定义')
    await userEvent.click(screen.getByRole('button', { name: '显示答案' }))
  }

  it('★ 未自评时"下一张"禁用（调度尚未推进，放行会留下结不了账的卡片）', async () => {
    await reveal()
    // 只有一张卡时按钮文案是"完成本轮"
    expect(screen.getByRole('button', { name: '完成本轮' })).toBeDisabled()
    expect(mockedSubmit).not.toHaveBeenCalled()
  })

  it('★ 自评按 quality 提交，并展示"凭什么排到 N 天后"', async () => {
    mockedSubmit.mockResolvedValue({
      card_id: 'card-1',
      quality: 4,
      is_correct: true,
      interval_days: 21,
      repetition: 3,
      easiness_factor: 2.46,
      next_review_at: '2026-10-02T04:00:00+00:00',
      mastery_level: 63.1,
      stability: 21.4,
      difficulty: 5.27,
      predicted_retention: 0.62,
    })
    await reveal()

    await userEvent.click(screen.getByText('想起来了'))

    await waitFor(() => expect(mockedSubmit).toHaveBeenCalledTimes(1))
    expect(mockedSubmit).toHaveBeenCalledWith('card-1', 4, '', expect.any(Number))

    expect(await screen.findByText(/下次复习:/)).toBeInTheDocument()
    expect(screen.getByText('21 天后')).toBeInTheDocument()
    expect(screen.getByText(/复习前模型认为你还能想起:/)).toBeInTheDocument()
    expect(screen.getByText('62%')).toBeInTheDocument()
    expect(screen.getByText(/记忆强度 21.4 天/)).toBeInTheDocument()
    expect(screen.getByText(/难度 5.3/)).toBeInTheDocument()
    expect(screen.getByText(/掌握度 57 → 63/)).toBeInTheDocument()
  })

  it('★ 首次复习（预测保持率 = 1）不显示那一行，避免"预测还能想起 100%"这种废话', async () => {
    mockedSubmit.mockResolvedValue({
      card_id: 'card-1', quality: 4, is_correct: true,
      interval_days: 1, repetition: 1, easiness_factor: 2.46,
      next_review_at: '2026-09-12T04:00:00+00:00', mastery_level: 100,
      stability: 3.17, difficulty: 5.28, predicted_retention: 1,
    })
    await reveal()
    await userEvent.click(screen.getByText('想起来了'))

    await screen.findByText(/下次复习:/)
    expect(screen.queryByText(/复习前模型认为你还能想起/)).not.toBeInTheDocument()
  })

  it('★ 提交期间重复点击不会重复提交（in-flight 锁）', async () => {
    let resolve!: (v: unknown) => void
    mockedSubmit.mockReturnValue(new Promise(r => { resolve = r }) as never)
    await reveal()

    const button = screen.getByText('想起来了')
    await userEvent.click(button)
    await userEvent.click(button)

    expect(mockedSubmit).toHaveBeenCalledTimes(1)
    resolve({
      card_id: 'card-1', quality: 4, is_correct: true,
      interval_days: 6, repetition: 3, easiness_factor: 2.5,
      next_review_at: null, mastery_level: 60,
      stability: 6, difficulty: 5, predicted_retention: 0.9,
    })
    await screen.findByText(/下次复习:/)
  })

  it('自评完成后"完成本轮"可用，并进入汇总', async () => {
    mockedSubmit.mockResolvedValue({
      card_id: 'card-1', quality: 4, is_correct: true,
      interval_days: 6, repetition: 3, easiness_factor: 2.5,
      next_review_at: null, mastery_level: 60,
      stability: 6, difficulty: 5, predicted_retention: 0.9,
    })
    await reveal()
    await userEvent.click(screen.getByText('想起来了'))

    const finish = await screen.findByRole('button', { name: '完成本轮' })
    expect(finish).not.toBeDisabled()
    await userEvent.click(finish)

    expect(await screen.findByText('本轮卡片复习完成')).toBeInTheDocument()
    expect(screen.getByText('复习卡片: 1 张')).toBeInTheDocument()
    expect(screen.getByText('想起来了: 1 张')).toBeInTheDocument()
  })
})
