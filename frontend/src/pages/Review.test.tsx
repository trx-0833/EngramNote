/**
 * @file 答题复习页的交互测试（5.12：与卡片复习页统一的那些约定）
 *
 * 5.12 之前，答题复习与卡片复习各有一套"看着差不多"的实现，其中两处
 * 差异是**缺陷**而不是设计：
 *
 * 1. 答题页的回车处理无差别 `preventDefault()` —— 焦点在"查看原文语境"
 *    按钮上按回车，原文不展开、人却被带去下一题；焦点在四档自评上按回车，
 *    自评根本没提交。卡片页在附录 AA.7 已有守卫，答题页没有。
 * 2. 答题页的进度条公式是各页手抄的一份，没有测试盯着。
 *
 * 本文件把答题这一侧钉住，与 `CardReview.test.tsx` 一起构成"两条流程
 * 行为一致"的证据。
 */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  getDueQuizzes, getReviewStats, submitAnswer,
  type DueQuiz, type ReviewStats, type SubmitAnswerResponse,
} from '../api/client'
import { getKnowledgeCard } from '../api/qa'
import Review from './Review'

vi.mock('../api/client', () => ({
  getDueQuizzes: vi.fn(),
  submitAnswer: vi.fn(),
  getReviewStats: vi.fn(),
}))
vi.mock('../components/Toast', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}))
// 原文语境组件按需请求卡片详情；测试里只关心"有没有请求"，不拉真网络
vi.mock('../api/qa', () => ({ getKnowledgeCard: vi.fn() }))

const mockedDue = vi.mocked(getDueQuizzes)
const mockedSubmit = vi.mocked(submitAnswer)
const mockedStats = vi.mocked(getReviewStats)
const mockedCard = vi.mocked(getKnowledgeCard)

function makeQuiz(over: Partial<DueQuiz> = {}): DueQuiz {
  return {
    id: 'q1',
    card_id: 'c1',
    note_id: 'n1',
    question_type: 'fill_blank',
    difficulty: 'medium',
    question: '浮充是什么？',
    options: null,
    next_review_at: null,
    review_count: 2,
    interval: 3,
    easiness_factor: 2.5,
    ...over,
  }
}

function makeStats(over: Partial<ReviewStats> = {}): ReviewStats {
  return {
    due_count: 2, today_done: 1, today_correct: 1, today_accuracy: 100,
    total_reviews: 10, total_correct: 8, total_accuracy: 80, total_quizzes: 20,
    daily_limit: 10,
    ...over,
  }
}

function makeResult(over: Partial<SubmitAnswerResponse> = {}): SubmitAnswerResponse {
  return {
    quiz_id: 'q1',
    is_correct: true,
    quality: 4,
    correct_answer: '蓄电池的一种恒压运行方式',
    explanation: null,
    options: null,
    question_type: 'fill_blank',
    sm2: {
      interval: 6, repetition: 2, easiness_factor: 2.5,
      next_review_at: '2026-09-17T04:00:00+00:00', rating: 3, predicted_retention: 0.62,
    },
    self_rating: null,
    grading_method: 'fill_blank',
    needs_self_assessment: false,
    completing_placeholder: false,
    grading_reason: null,
    grading_detail: null,
    ...over,
  }
}

/** 简答题的占位提交：后端要求用户自评，此刻调度尚未推进 */
function makePending(over: Partial<SubmitAnswerResponse> = {}): SubmitAnswerResponse {
  return makeResult({
    is_correct: false,
    quality: 1,
    question_type: 'short_answer',
    grading_method: 'ungraded',
    needs_self_assessment: true,
    self_rating: null,
    ...over,
  })
}

function renderPage() {
  return render(
    <MemoryRouter>
      <Review />
    </MemoryRouter>,
  )
}

/** 等第一题挂出来 */
async function loaded() {
  await screen.findByText('浮充是什么？')
}

function fillWidth(container: HTMLElement): string {
  const fill = container.querySelector('.progress-bar-fill')
  if (!(fill instanceof HTMLElement)) throw new Error('没有渲染 .progress-bar-fill')
  return fill.style.width
}

beforeEach(() => {
  mockedDue.mockReset()
  mockedSubmit.mockReset()
  mockedStats.mockReset()
  mockedCard.mockReset()
  mockedDue.mockResolvedValue({
    items: [makeQuiz(), makeQuiz({ id: 'q2', question: '均充是什么？' })],
    total: 2,
  })
  mockedStats.mockResolvedValue(makeStats())
})

describe('答题复习的进度显示（与卡片复习页共用同一组件）', () => {
  it('★ 进度条把"刚结账的这一张"算进去：提交后从 0% 涨到 50%', async () => {
    mockedSubmit.mockResolvedValue(makeResult())
    const { container } = renderPage()
    await loaded()

    expect(fillWidth(container)).toBe('0%')

    await userEvent.type(screen.getByPlaceholderText('请输入答案...'), '恒压运行')
    await userEvent.click(screen.getByRole('button', { name: '提交答案' }))

    await screen.findByText('回答正确!')
    expect(fillWidth(container)).toBe('50%')
  })

  it('两侧计数：题号进度在左、本次正确数与今日额度在右', async () => {
    renderPage()
    await loaded()

    expect(screen.getByText('1 / 2')).toBeInTheDocument()
    expect(screen.getByText('0/0 正确 | 今日 1/10')).toBeInTheDocument()
  })
})

describe('每日限额的分流依据是 error_code，不是中文文案（阶段 0.11）', () => {
  /** 造一个带 code 的异常，模拟 api/client 抛出的 ApiError（结构一致） */
  function apiError(code: string, message: string): Error {
    const err = new Error(message) as Error & { code?: string }
    err.code = code
    return err
  }

  it('★ error_code=DAILY_REVIEW_LIMIT_REACHED 时进入"复习完成"，且刷新统计', async () => {
    // 文案里**故意不带**"每日上限"四个字：只要分支还在匹配中文，这条就会红
    // （这正是 F-19 要消灭的形态 —— 后端改文案不该影响前端分支）。
    mockedSubmit.mockRejectedValue(
      apiError('DAILY_REVIEW_LIMIT_REACHED', '今日额度已用完'),
    )
    mockedStats.mockResolvedValue(makeStats({ today_done: 10, daily_limit: 10 }))

    renderPage()
    await loaded()

    await userEvent.type(screen.getByPlaceholderText('请输入答案...'), '恒压运行')
    await userEvent.click(screen.getByRole('button', { name: '提交答案' }))

    expect(await screen.findByText('复习完成')).toBeInTheDocument()
    // 进入完成页前必须重新拉一次统计（否则"今日已完成"显示的是旧值）
    expect(mockedStats).toHaveBeenCalledTimes(2)
  })

  it('其它错误码仍然报错，不会被误判成"今日完成"', async () => {
    mockedSubmit.mockRejectedValue(apiError('REVIEW_QUIZ_NOT_FOUND', '题目不存在'))

    renderPage()
    await loaded()

    await userEvent.type(screen.getByPlaceholderText('请输入答案...'), '恒压运行')
    await userEvent.click(screen.getByRole('button', { name: '提交答案' }))

    // 没被误判成"额度用尽"：没有跳完成页，也没有多拉一次统计
    expect(screen.queryByText('复习完成')).not.toBeInTheDocument()
    expect(screen.getByText('浮充是什么？')).toBeInTheDocument()
    expect(mockedStats).toHaveBeenCalledTimes(1)
  })
})

describe('答题复习的回车约定（与卡片复习页一致）', () => {  it('★ 焦点不在按钮上时，回车提交答案', async () => {
    mockedSubmit.mockResolvedValue(makeResult())
    renderPage()
    await loaded()

    await userEvent.type(screen.getByPlaceholderText('请输入答案...'), '恒压运行{Enter}')

    expect(mockedSubmit).toHaveBeenCalledTimes(1)
    expect(mockedSubmit).toHaveBeenCalledWith('q1', '恒压运行', expect.any(Number), undefined, false)
  })

  it('★ 焦点在"提交答案"按钮上时回车只提交一次（原生激活与容器接管不叠加）', async () => {
    mockedSubmit.mockResolvedValue(makeResult())
    renderPage()
    await loaded()

    await userEvent.type(screen.getByPlaceholderText('请输入答案...'), '恒压运行')
    screen.getByRole('button', { name: '提交答案' }).focus()
    await userEvent.keyboard('{Enter}')

    expect(mockedSubmit).toHaveBeenCalledTimes(1)
  })

  it('★ 焦点在四档自评上时，回车要真的提交自评（旧实现把它 preventDefault 掉了）', async () => {
    mockedDue.mockResolvedValue({
      items: [makeQuiz({ question_type: 'short_answer' })],
      total: 1,
    })
    mockedSubmit
      .mockResolvedValueOnce(makePending())
      .mockResolvedValueOnce(makeResult({
        question_type: 'short_answer', grading_method: 'self_rating', self_rating: 4,
      }))
    renderPage()
    await loaded()

    await userEvent.type(screen.getByPlaceholderText('请输入你的回答...'), '恒压运行')
    await userEvent.click(screen.getByRole('button', { name: '提交答案' }))
    await screen.findByText('请对照答案，给自己的回忆程度打分')

    screen.getByText('想起来了').closest('button')?.focus()
    await userEvent.keyboard('{Enter}')

    // 第二次提交必须带上自评分 —— 也就是"回车真的激活了那个按钮"
    expect(mockedSubmit).toHaveBeenCalledTimes(2)
    expect(mockedSubmit).toHaveBeenLastCalledWith(
      'q1', '恒压运行', expect.any(Number), 4,
    )
  })

  it('★ 焦点在"查看原文语境"上时，回车展开原文而不是被带去下一题', async () => {
    mockedSubmit.mockResolvedValue(makeResult())
    mockedCard.mockResolvedValue({ source_text: '原文：浮充是恒压运行。' } as never)
    renderPage()
    await loaded()

    await userEvent.type(screen.getByPlaceholderText('请输入答案...'), '恒压运行')
    await userEvent.click(screen.getByRole('button', { name: '提交答案' }))
    await screen.findByText('回答正确!')

    screen.getByRole('button', { name: '查看原文语境' }).focus()
    await userEvent.keyboard('{Enter}')

    expect(mockedCard).toHaveBeenCalledWith('c1')
    expect(await screen.findByText('原文：浮充是恒压运行。')).toBeInTheDocument()
    // 关键：人被留在这一题上（旧实现会在这里推进到第 2 题）
    expect(screen.getByText('1 / 2')).toBeInTheDocument()
    expect(screen.queryByText('2 / 2')).not.toBeInTheDocument()
  })
})
