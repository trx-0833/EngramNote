/**
 * @file 任务进度组件的测试（阶段 5.11）
 * @description 这个组件的价值全在"不该出错的时候别出错、该如实说的时候别美化"，
 *   因此测试盯的是四件事：
 *   1. 进度与阶段名如实展示（而不是永远转圈）；
 *   2. **接口失败不能把页面搞挂**（进度是装饰，不是正确性）；
 *   3. 终态停止轮询（否则开着的页面会永远每 2 秒打一次接口）；
 *   4. 取消成功**不等于已停止** —— 后端 `terminated=false` 时必须如实告知。
 */
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import TaskProgress from './TaskProgress'

// 只 mock API 层：组件内部的轮询/状态机逻辑必须走真实实现
vi.mock('../api/tasks', async () => {
  const actual = await vi.importActual<typeof import('../api/tasks')>('../api/tasks')
  return {
    ...actual,
    listNoteTasks: vi.fn(),
    cancelTask: vi.fn(),
  }
})

import { cancelTask, listNoteTasks, type TaskRun } from '../api/tasks'

const mockList = vi.mocked(listNoteTasks)
const mockCancel = vi.mocked(cancelTask)

function run(overrides: Partial<TaskRun> = {}): TaskRun {
  return {
    task_id: 't-1',
    task_name: 'app.tasks.clean_tasks.clean_document_task',
    note_id: 'n-1',
    status: 'running',
    progress: 0.42,
    stage: '正在清洗文本',
    message: null,
    attempt: 1,
    max_attempts: 3,
    error: null,
    retryable: false,
    heartbeat_at: null,
    started_at: null,
    finished_at: null,
    ...overrides,
  }
}

describe('TaskProgress', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('展示真实进度与阶段名，而不是一句"请稍候"', async () => {
    mockList.mockResolvedValue({ items: [run()], total: 1 })
    render(<TaskProgress noteId="n-1" />)

    expect(await screen.findByTestId('task-progress-stage')).toHaveTextContent('正在清洗文本')
    expect(screen.getByTestId('task-progress-percent')).toHaveTextContent('42%')
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '42')
  })

  it('★ 接口失败时退回静态文案，不报错也不阻塞', async () => {
    mockList.mockRejectedValue(new Error('网络不通'))
    render(<TaskProgress noteId="n-1" fallbackText="正在转换中，请稍候..." />)

    const fallback = await screen.findByTestId('task-progress-fallback')
    expect(fallback).toHaveTextContent('正在转换中，请稍候...')
    expect(screen.queryByTestId('task-progress')).not.toBeInTheDocument()
  })

  it('没有任务记录时也退回静态文案', async () => {
    mockList.mockResolvedValue({ items: [], total: 0 })
    render(<TaskProgress noteId="n-1" fallbackText="处理中..." />)

    expect(await screen.findByTestId('task-progress-fallback')).toHaveTextContent('处理中...')
  })

  it('★ 已处于终态时停止轮询（不重复打接口）', async () => {
    mockList.mockResolvedValue({ items: [run({ status: 'succeeded', progress: 1 })], total: 1 })
    render(<TaskProgress noteId="n-1" />)

    await screen.findByTestId('task-progress')
    const callsAfterFirst = mockList.mock.calls.length
    await new Promise((resolve) => setTimeout(resolve, 2600))
    expect(mockList.mock.calls.length).toBe(callsAfterFirst)
  })

  it('运行中会继续轮询（进度条要能"动起来"）', async () => {
    mockList.mockResolvedValue({ items: [run()], total: 1 })
    render(<TaskProgress noteId="n-1" />)

    await screen.findByTestId('task-progress')
    await waitFor(() => expect(mockList.mock.calls.length).toBeGreaterThan(1), { timeout: 3500 })
  })

  it('★ 取消成功但服务端未强杀时，如实告知"下一个阶段边界退出"', async () => {
    mockList.mockResolvedValue({ items: [run()], total: 1 })
    mockCancel.mockResolvedValue({
      task_id: 't-1',
      status: 'cancelled',
      terminated: false,
      detail: '已标记为取消。',
    })
    render(<TaskProgress noteId="n-1" />)

    await userEvent.click(await screen.findByRole('button', { name: '取消任务' }))
    const notice = await screen.findByTestId('task-cancel-notice')
    // 不能显示成"已取消"——那会让人以为正在跑的那一步也已经停了
    expect(notice).toHaveTextContent('下一个阶段边界')
    expect(mockCancel).toHaveBeenCalledWith('t-1')
  })

  it('终止成功时显示"已取消"', async () => {
    mockList.mockResolvedValue({ items: [run()], total: 1 })
    mockCancel.mockResolvedValue({ task_id: 't-1', status: 'cancelled', terminated: true, detail: '' })
    render(<TaskProgress noteId="n-1" />)

    await userEvent.click(await screen.findByRole('button', { name: '取消任务' }))
    expect(await screen.findByTestId('task-cancel-notice')).toHaveTextContent('已取消。')
  })

  it('取消失败时提示可重试，且不假装成功', async () => {
    mockList.mockResolvedValue({ items: [run()], total: 1 })
    mockCancel.mockRejectedValue(new Error('任务不存在'))
    render(<TaskProgress noteId="n-1" />)

    await userEvent.click(await screen.findByRole('button', { name: '取消任务' }))
    expect(await screen.findByTestId('task-cancel-notice')).toHaveTextContent('取消失败')
  })

  it('终态时不显示取消按钮（已经结束的任务没有可取消的东西）', async () => {
    mockList.mockResolvedValue({ items: [run({ status: 'failed', error: '转换失败' })], total: 1 })
    render(<TaskProgress noteId="n-1" />)

    await screen.findByTestId('task-progress')
    expect(screen.queryByRole('button', { name: '取消任务' })).not.toBeInTheDocument()
    expect(screen.getByText('转换失败')).toBeInTheDocument()
  })

  it('重试次数大于 1 时展示尝试次数（重试是正常流程，不该让用户以为卡住了）', async () => {
    mockList.mockResolvedValue({ items: [run({ attempt: 2, max_attempts: 3 })], total: 1 })
    render(<TaskProgress noteId="n-1" />)

    expect(await screen.findByText('第 2/3 次尝试')).toBeInTheDocument()
  })

  it('allowCancel=false 时不显示取消按钮（父组件已有产品级停止入口）', async () => {
    mockList.mockResolvedValue({ items: [run()], total: 1 })
    render(<TaskProgress noteId="n-1" allowCancel={false} />)

    await screen.findByTestId('task-progress')
    expect(screen.queryByRole('button', { name: '取消任务' })).not.toBeInTheDocument()
  })
})
