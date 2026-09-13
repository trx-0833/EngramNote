/**
 * @file 笔记详情页的表征测试（overhaul-plan 阶段 5.5 的前置条件）
 *
 * ## 为什么先写测试再拆页面
 *
 * 5.5 要把 `NoteDetail.tsx`（1138 行）拆到 300 行以下。而这个文件此前
 * **一行测试都没有** —— 直接拆等于盲改：拆错了（少传一个 prop、把
 * "自动切到清洗版"的条件写反）不会有任何东西告诉我。
 *
 * 因此这里先把**最容易被拆坏、且后果最严重**的行为固定下来：
 *
 * | 行为 | 拆坏了会怎样 |
 * |---|---|
 * | 已清洗的笔记打开时自动显示清洗版 | 用户以为自己的清洗结果丢了 |
 * | 编辑保存调用的是 `updateNoteContent`（带 target） | 保存到错误的版本，覆盖掉清洗副本 |
 * | 删除要**先弹确认**再调 API | 误点一次就丢笔记 |
 * | 加载失败显示 ErrorDisplay + 可重试 | 白屏，用户不知道发生了什么 |
 * | 转换中显示进度组件（而不是永远转圈） | 阶段 5.11 的成果被拆回原形 |
 * | `archived` 重新理解要带 confirm=true 二次确认 | 清空全部旧产物且不可撤销 |
 *
 * 这些断言刻意是**行为级**的（点哪个按钮 → 调哪个接口），不锁 DOM 结构，
 * 这样重构可以自由改结构而测试不必跟着改。
 */
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import NoteDetail from './NoteDetail'

// ── mock 掉整条 API 层：本文件测的是页面行为，不是接口契约 ──
vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client')
  return {
    ...actual,
    getNote: vi.fn(),
    getKnowledgeCards: vi.fn(),
    getQuestions: vi.fn(),
    getAnnotations: vi.fn(),
    getNoteLinks: vi.fn(),
    getNotes: vi.fn(),
    updateNoteLinks: vi.fn(),
    updateNoteContent: vi.fn(),
    updateNoteRole: vi.fn(),
    createAnnotation: vi.fn(),
    deleteAnnotation: vi.fn(),
    deleteNote: vi.fn(),
    startUnderstanding: vi.fn(),
    startCleaning: vi.fn(),
    stopCleaning: vi.fn(),
    restoreBlock: vi.fn(),
    deleteBlock: vi.fn(),
  }
})

vi.mock('../components/Toast', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}))
// 子组件按需请求数据；本文件只关心页面本体，把它们换成桩
vi.mock('../components/NoteAskPanel', () => ({ default: () => <div data-testid="ask-panel" /> }))
vi.mock('../components/VersionHistory', () => ({ default: () => <div data-testid="version-history" /> }))
vi.mock('../components/DiffView', () => ({ default: () => <div data-testid="diff-view" /> }))
vi.mock('../api/tasks', async () => {
  const actual = await vi.importActual<typeof import('../api/tasks')>('../api/tasks')
  return { ...actual, listNoteTasks: vi.fn().mockResolvedValue({ items: [], total: 0 }) }
})

import {
  deleteNote,
  getAnnotations,
  getKnowledgeCards,
  getNote,
  getNoteLinks,
  getQuestions,
  startUnderstanding,
  updateNoteContent,
  type NoteDetail as NoteDetailType,
} from '../api/client'

const mockedGetNote = vi.mocked(getNote)
const mockedCards = vi.mocked(getKnowledgeCards)
const mockedQuestions = vi.mocked(getQuestions)
const mockedAnnotations = vi.mocked(getAnnotations)
const mockedLinks = vi.mocked(getNoteLinks)
const mockedDelete = vi.mocked(deleteNote)
const mockedUnderstand = vi.mocked(startUnderstanding)
const mockedUpdateContent = vi.mocked(updateNoteContent)

function makeNote(over: Partial<NoteDetailType> = {}): NoteDetailType {
  return {
    id: 'note-1',
    user_id: 'u-1',
    title: '锂离子电池的浮充与均充',
    source_type: 'pdf',
    status: 'cleaned',
    file_size: 1024,
    original_file_path: '/u-1/note-1.pdf',
    original_md_path: '/u-1/note-1.md',
    clean_md_path: '/u-1/note-1.clean.md',
    original_md_content: '# 原始内容\n\n浮充是蓄电池的一种运行方式。',
    clean_md_content: '# 清洗后内容\n\n浮充：恒压运行方式。',
    error_message: null,
    created_at: '2026-09-01T10:00:00Z',
    updated_at: '2026-09-02T10:00:00Z',
    ...over,
  } as NoteDetailType
}

/** 与 `NoteLinksResponse` 契约一致的响应（少一个字段会让页面整体崩掉，见文件末尾说明） */
function emptyLinks() {
  return {
    linked_materials: [],
    linked_personal_notes: [],
    dangling_links: [],
  } as never
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/notes/note-1']}>
      <Routes>
        <Route path="/notes/:noteId" element={<NoteDetail />} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  mockedCards.mockResolvedValue({ items: [], total: 0 } as never)
  mockedQuestions.mockResolvedValue({ items: [], total: 0 } as never)
  mockedAnnotations.mockResolvedValue([] as never)
  mockedLinks.mockResolvedValue(emptyLinks())
  mockedDelete.mockResolvedValue(undefined as never)
  mockedUpdateContent.mockResolvedValue({} as never)
})

describe('加载与状态展示', () => {
  it('加载成功后显示笔记标题', async () => {
    mockedGetNote.mockResolvedValue(makeNote())
    renderPage()
    expect(await screen.findByText('锂离子电池的浮充与均充')).toBeInTheDocument()
  })

  it('★ 已清洗的笔记默认显示清洗版（原始版只是其中一个视图）', async () => {
    mockedGetNote.mockResolvedValue(makeNote())
    renderPage()
    // 清洗版正文出现，原始版正文不出现 —— 用户上次看到的就是清洗结果
    expect(await screen.findByText(/浮充：恒压运行方式/)).toBeInTheDocument()
    expect(screen.queryByText(/浮充是蓄电池的一种运行方式/)).not.toBeInTheDocument()
  })

  it('加载失败时显示错误与重试入口，而不是白屏', async () => {
    mockedGetNote.mockRejectedValue(new Error('笔记不存在'))
    renderPage()
    expect(await screen.findByText(/笔记不存在/)).toBeInTheDocument()
  })

  it('★ 转换中的笔记显示任务进度组件（阶段 5.11 的成果，不能被拆回静态文案）', async () => {
    mockedGetNote.mockResolvedValue(makeNote({ status: 'converting', clean_md_content: null }))
    renderPage()
    // 没有 task_runs 记录 → 组件退回静态兜底文案
    expect(await screen.findByTestId('task-progress-fallback')).toHaveTextContent('正在转换中')
  })
})

describe('编辑与保存', () => {
  it('★ 进入编辑、修改、保存走的是 updateNoteContent', async () => {
    mockedGetNote.mockResolvedValue(makeNote())
    renderPage()
    await screen.findByText('锂离子电池的浮充与均充')

    await userEvent.click(screen.getByRole('button', { name: /编辑/ }))
    const textarea = await screen.findByRole('textbox')
    await userEvent.clear(textarea)
    await userEvent.type(textarea, '改过的内容')
    await userEvent.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => expect(mockedUpdateContent).toHaveBeenCalled())
    const [noteId, content] = mockedUpdateContent.mock.calls[0]
    expect(noteId).toBe('note-1')
    expect(content).toContain('改过的内容')
  })

  it('取消编辑不调用保存接口（改了又反悔不该留下痕迹）', async () => {
    mockedGetNote.mockResolvedValue(makeNote())
    renderPage()
    await screen.findByText('锂离子电池的浮充与均充')

    await userEvent.click(screen.getByRole('button', { name: /编辑/ }))
    await screen.findByRole('textbox')
    await userEvent.click(screen.getByRole('button', { name: '取消' }))

    expect(mockedUpdateContent).not.toHaveBeenCalled()
    expect(await screen.findByText('锂离子电池的浮充与均充')).toBeInTheDocument()
  })
})

describe('契约漂移时的健壮性', () => {
  it('★ 关联资料响应缺字段时不整页崩掉（本轮实测过：undefined.length = 白屏）', async () => {
    mockedGetNote.mockResolvedValue(makeNote())
    // 模拟"后端少返回一个数组字段"（契约漂移 / 缓存里的旧结构）
    mockedLinks.mockResolvedValue({ linked_materials: [] } as never)
    renderPage()

    // 页面主体必须照常渲染：字段缺失最坏只能是"这一块不显示"
    expect(await screen.findByText('锂离子电池的浮充与均充')).toBeInTheDocument()
    expect(screen.queryByText('被以下笔记引用')).not.toBeInTheDocument()
    expect(screen.queryByText('关联的学习资料')).not.toBeInTheDocument()
  })
})

describe('删除与重新理解', () => {
  it('★ 删除必须先确认：点"删除"只弹窗、不调接口', async () => {
    mockedGetNote.mockResolvedValue(makeNote())
    renderPage()
    await screen.findByText('锂离子电池的浮充与均充')

    // 按钮文案是"删除"，"移入回收站"是弹窗标题
    await userEvent.click(screen.getByRole('button', { name: '删除' }))
    expect(mockedDelete).not.toHaveBeenCalled()
    expect(await screen.findByText('移入回收站')).toBeInTheDocument()
  })

  it('★ archived 笔记重新学习要二次确认：第一次不带 confirm，确认后才带 confirm=true', async () => {
    mockedGetNote.mockResolvedValue(makeNote({ status: 'archived' }))
    mockedUnderstand.mockResolvedValue({
      requires_confirm: true,
      impact: { cards: 3, quizzes: 0, review_logs: 0, relations: 0 },
    } as never)
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    renderPage()
    await screen.findByText('锂离子电池的浮充与均充')

    await userEvent.click(screen.getByRole('button', { name: 'AI预处理' }))

    await waitFor(() => expect(mockedUnderstand).toHaveBeenCalledTimes(2))
    // 第一次探测影响（不带 confirm），第二次才是真的清空并重跑
    expect(mockedUnderstand.mock.calls[0][1]).toBe(false)
    expect(mockedUnderstand.mock.calls[1][1]).toBe(true)
    // 而且必须先让用户看到"将删除什么"
    expect(confirmSpy).toHaveBeenCalled()
    expect(String(confirmSpy.mock.calls[0][0])).toContain('3 张知识卡片')
    confirmSpy.mockRestore()
  })

  it('用户在二次确认里点"取消"时不发第二次请求（不可撤销的操作不能默认执行）', async () => {
    mockedGetNote.mockResolvedValue(makeNote({ status: 'archived' }))
    mockedUnderstand.mockResolvedValue({ requires_confirm: true, impact: { cards: 1 } } as never)
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
    renderPage()
    await screen.findByText('锂离子电池的浮充与均充')

    await userEvent.click(screen.getByRole('button', { name: 'AI预处理' }))

    await waitFor(() => expect(mockedUnderstand).toHaveBeenCalledTimes(1))
    expect(mockedUnderstand.mock.calls[0][1]).toBe(false)
    confirmSpy.mockRestore()
  })
})
