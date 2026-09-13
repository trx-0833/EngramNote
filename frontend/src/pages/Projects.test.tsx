/**
 * @file 项目页的表征测试（overhaul-plan 阶段 5.5 的前置条件）
 *
 * ## 为什么先写测试再拆页面
 *
 * `Projects.tsx` 有 759 行、**一行测试都没有**。它把四件互不相干的事塞在同一个组件里：
 * 项目标签的增删改查、笔记归属（展开/添加/移出）、收件箱扫描导入、以及一个大
 * `renderCard(p, index)` 渲染函数。5.5 要把它拆开，而"项目管理"是**破坏性操作最集中**
 * 的一页（删除项目、把笔记移出项目），拆错了用户丢的是数据而不是界面。
 *
 * 这里先把这些行为钉住：
 *
 * | 行为 | 拆坏了会怎样 |
 * |---|---|
 * | 加载/空/失败三种状态各有界面 | 白屏或"假空列表" |
 * | 删除项目、移出笔记都必须先 confirm | 误点一次就改了数据 |
 * | 新建/重命名名称为空时不发请求 | 后端多出一条无名项目 |
 * | 展开项目才拉详情，再点收起 | 每次列表刷新都白拉 N 个详情 |
 * | 添加笔记面板只列"尚未归属本项目"的笔记 | 同一篇笔记被反复添加 |
 * | 扫描导入后刷新列表（有新导入时） | 数字与内容对不上 |
 * | 点笔记行才跳转，点「移出」不跳转 | 想移出却被弹到别的页面 |
 *
 * ## 关于「★ 契约漂移」用例
 *
 * 本文件用真实 `ErrorBoundary`（与 App.tsx 一致）包住页面。文件末尾那一组**曾经**
 * 故意断言页面崩到错误边界（记录当时的真实行为，见附录 AZ）。缺陷修好后它们已全部
 * 翻成**正向断言**：喂残缺数据时页面必须给出明确的降级界面（空状态 / 空列表 /
 * 可读占位），而不是白屏。断言里的「没有崩到错误边界」只是必要条件 ——
 * 每条用例都另外钉死了用户实际看到的东西，删掉守卫就会变红（附录 BA 记录变异验证）。
 */
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useParams } from 'react-router-dom'
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
  type MockInstance,
} from 'vitest'

import type { Note, NoteInFolder, Project } from '../api/client'
import ErrorBoundary from '../components/ErrorBoundary'

// ── mock 掉整条 API 层：本文件测的是页面行为，不是接口契约 ──
vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client')
  return {
    ...actual,
    getProjects: vi.fn(),
    createProject: vi.fn(),
    updateProject: vi.fn(),
    deleteProject: vi.fn(),
    getProjectDetail: vi.fn(),
    scanProject: vi.fn(),
    getNotes: vi.fn(),
    addNotesToProject: vi.fn(),
    removeNoteFromProject: vi.fn(),
  }
})

import {
  addNotesToProject,
  createProject,
  deleteProject,
  getNotes,
  getProjectDetail,
  getProjects,
  removeNoteFromProject,
  scanProject,
  updateProject,
} from '../api/client'
import Projects from './Projects'

const mockedProjects = vi.mocked(getProjects)
const mockedCreate = vi.mocked(createProject)
const mockedUpdate = vi.mocked(updateProject)
const mockedDelete = vi.mocked(deleteProject)
const mockedDetail = vi.mocked(getProjectDetail)
const mockedScan = vi.mocked(scanProject)
const mockedNotes = vi.mocked(getNotes)
const mockedAddNotes = vi.mocked(addNotesToProject)
const mockedRemoveNote = vi.mocked(removeNoteFromProject)

// ── 夹具 ──
function makeProject(over: Partial<Project> = {}): Project {
  return {
    id: 'p-1',
    user_id: 'u-1',
    name: 'Transformer 论文精读',
    description: '把注意力机制相关的论文读一遍',
    note_count: 2,
    created_at: '2026-09-01T10:00:00Z',
    updated_at: '2026-09-02T10:00:00Z',
    ...over,
  }
}

function makeNote(over: Partial<Note> = {}): Note {
  return {
    id: 'n-1',
    user_id: 'u-1',
    title: 'Attention Is All You Need',
    source_type: 'pdf',
    status: 'cleaned',
    file_size: 1024,
    page_count: 15,
    error_message: null,
    trashed_at: null,
    created_at: '2026-09-01T10:00:00Z',
    updated_at: '2026-09-02T10:00:00Z',
    ...over,
  }
}

function makeNoteInFolder(over: Partial<NoteInFolder> = {}): NoteInFolder {
  return {
    id: 'n-1',
    title: 'Attention Is All You Need',
    source_type: 'pdf',
    status: 'cleaned',
    file_size: 2048,
    created_at: '2026-09-01T10:00:00Z',
    ...over,
  }
}

function makeScanResult(over: Partial<Record<string, unknown>> = {}) {
  return {
    project_id: 'p-1',
    project_name: 'Transformer 论文精读',
    scanned: 3,
    imported: 2,
    skipped: 1,
    unsupported: 0,
    imported_notes: [],
    skipped_details: [],
    unsupported_details: [],
    ...over,
  } as never
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((r) => {
    resolve = r
  })
  return { promise, resolve }
}

/** 笔记详情占位路由：用来证明"点笔记行真的跳过去了" */
function NoteDetailStub() {
  const { noteId } = useParams()
  return <div>笔记详情页:{noteId}</div>
}

function renderPage(initialEntry = '/projects') {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route
          path="/projects"
          element={
            // 与 App.tsx 一致：页面被路由级错误边界包住
            <ErrorBoundary resetKey="/projects">
              <Projects />
            </ErrorBoundary>
          }
        />
        <Route path="/notes/:noteId" element={<NoteDetailStub />} />
      </Routes>
    </MemoryRouter>,
  )
}

/**
 * 「添加（N）」按钮：文案里是全角括号，直接写进查询串容易抄错，
 * 这里按"以 添加 开头且带数字"的文本特征定位。
 */
function addConfirmButton(): HTMLElement {
  const button = screen
    .getAllByRole('button')
    .find((b) => /^添加\D*\d/.test(b.textContent ?? ''))
  if (!button) throw new Error('找不到「添加（N）」按钮')
  return button
}

/** 展开某个项目的笔记列表 */
async function expandProject(name = 'Transformer 论文精读') {
  await screen.findByText(name)
  await userEvent.click(screen.getAllByRole('button', { name: /查看笔记/ })[0])
}

let confirmSpy: MockInstance

beforeEach(() => {
  // 失败路径上页面会 console.error 记录原因；静音以免淹没测试输出（断言照做）
  vi.spyOn(console, 'error').mockImplementation(() => {})
  // 破坏性操作都要二次确认：默认"用户点了确定"，需要取消的用例自己改
  confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)

  mockedProjects.mockResolvedValue([makeProject()])
  mockedCreate.mockResolvedValue(makeProject({ id: 'p-9' }))
  mockedUpdate.mockResolvedValue(makeProject())
  mockedDelete.mockResolvedValue({ message: 'ok' })
  mockedDetail.mockResolvedValue({ ...makeProject(), notes: [makeNoteInFolder()] })
  mockedScan.mockResolvedValue(makeScanResult())
  mockedNotes.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 999 })
  mockedAddNotes.mockResolvedValue({ added: 1, not_found: 0 })
  mockedRemoveNote.mockResolvedValue({ message: 'ok' })
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('加载与状态展示', () => {
  it('加载中显示加载提示，加载完成后显示项目卡片', async () => {
    const pending = deferred<Project[]>()
    mockedProjects.mockImplementation(() => pending.promise)
    renderPage()

    // 请求还没回来：必须是"正在加载"，而不是让人以为一个项目都没有
    expect(screen.getByText(/正在加载项目/)).toBeInTheDocument()
    expect(screen.queryByText('还没有项目')).not.toBeInTheDocument()

    await act(async () => {
      pending.resolve([makeProject()])
    })

    expect(await screen.findByText('Transformer 论文精读')).toBeInTheDocument()
    expect(screen.queryByText(/正在加载项目/)).not.toBeInTheDocument()
  })

  it('★ 加载成功后显示项目名、笔记数与描述', async () => {
    renderPage()

    expect(await screen.findByText('Transformer 论文精读')).toBeInTheDocument()
    expect(screen.getByText('2 篇笔记')).toBeInTheDocument()
    expect(screen.getByText('把注意力机制相关的论文读一遍')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /新建项目/ })).toBeInTheDocument()
  })

  it('★ 一个项目都没有时给出空状态与创建入口，而不是空白页', async () => {
    mockedProjects.mockResolvedValue([])
    renderPage()

    expect(await screen.findByText('还没有项目')).toBeInTheDocument()
    expect(screen.getByText(/点击上方「新建项目」创建第一个项目/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /新建项目/ })).toBeInTheDocument()
    // 使用说明常驻，不随列表状态消失
    expect(screen.getByText(/使用说明/)).toBeInTheDocument()
  })

  it('★ 加载失败时显示错误提示（可关闭），页面不会变成白屏', async () => {
    mockedProjects.mockRejectedValue(new Error('网络不通'))
    renderPage()

    expect(await screen.findByText('加载项目列表失败，请稍后重试')).toBeInTheDocument()
    // 页面骨架仍在：头部、创建入口、说明都在
    expect(screen.getByRole('heading', { name: '项目' })).toBeInTheDocument()
    expect(screen.getByText(/项目作为标签归属笔记/)).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: '✕' }))

    expect(screen.queryByText('加载项目列表失败，请稍后重试')).not.toBeInTheDocument()
  })
})

describe('项目的新建 / 重命名 / 删除', () => {
  it('★ 新建项目：名称为空只提示不发请求；填好后创建并刷新列表', async () => {
    let calls = 0
    mockedProjects.mockImplementation(async () => {
      calls += 1
      return calls === 1 ? [] : [makeProject({ id: 'p-9', name: '因果推断入门', note_count: 0 })]
    })
    renderPage()
    await screen.findByText('还没有项目')

    await userEvent.click(screen.getByRole('button', { name: /新建项目/ }))
    await userEvent.click(screen.getByRole('button', { name: '创建项目' }))

    // 空名称必须在**前端**拦住，不能真的建出一个无名项目
    expect(screen.getByText('请输入项目名称')).toBeInTheDocument()
    expect(mockedCreate).not.toHaveBeenCalled()

    await userEvent.type(screen.getByPlaceholderText(/Transformer 论文精读/), '因果推断入门')
    await userEvent.type(screen.getByPlaceholderText(/这个项目是做什么的/), '从 Pearl 的三层阶梯读起')
    await userEvent.click(screen.getByRole('button', { name: '创建项目' }))

    await waitFor(() =>
      expect(mockedCreate).toHaveBeenCalledWith('因果推断入门', '从 Pearl 的三层阶梯读起'),
    )
    // 创建后必须重新拉列表，否则页面上看不到新项目
    await waitFor(() => expect(mockedProjects).toHaveBeenCalledTimes(2))
    expect(await screen.findByText('因果推断入门')).toBeInTheDocument()
  })

  it('★ 重命名：空名称不发请求；改好后调用 updateProject 并退出编辑', async () => {
    renderPage()
    await screen.findByText('Transformer 论文精读')

    await userEvent.click(screen.getByRole('button', { name: '重命名' }))

    // 行内编辑区与顶部同名输入框同时存在（都绑同一份状态），取编辑区的那个
    const nameInputs = screen.getAllByPlaceholderText('项目名称')
    await userEvent.clear(nameInputs[1])
    await userEvent.click(screen.getByRole('button', { name: '保存' }))

    expect(screen.getByText('项目名称不能为空')).toBeInTheDocument()
    expect(mockedUpdate).not.toHaveBeenCalled()

    await userEvent.type(nameInputs[1], '注意力机制精读')
    await userEvent.click(screen.getByRole('button', { name: '保存' }))

    // 只改名称时，原有描述必须原样带上（否则重命名会顺手清空描述）
    await waitFor(() =>
      expect(mockedUpdate).toHaveBeenCalledWith(
        'p-1',
        '注意力机制精读',
        '把注意力机制相关的论文读一遍',
      ),
    )
    await waitFor(() => expect(screen.queryByRole('button', { name: '保存' })).not.toBeInTheDocument())
    expect(screen.getByRole('button', { name: '重命名' })).toBeInTheDocument()
  })

  it('重命名点「取消」不调用接口', async () => {
    renderPage()
    await screen.findByText('Transformer 论文精读')

    await userEvent.click(screen.getByRole('button', { name: '重命名' }))
    await userEvent.click(screen.getByRole('button', { name: '取消' }))

    expect(mockedUpdate).not.toHaveBeenCalled()
    expect(screen.getByText('Transformer 论文精读')).toBeInTheDocument()
  })

  it('★ 删除项目必须先确认：取消不调接口，确认后才删除并刷新', async () => {
    confirmSpy.mockReturnValue(false)
    renderPage()
    await screen.findByText('Transformer 论文精读')

    await userEvent.click(screen.getByRole('button', { name: '删除' }))

    expect(mockedDelete).not.toHaveBeenCalled()
    expect(confirmSpy).toHaveBeenCalled()
    // 确认文案要说清楚"只删标签、笔记与文件保留"
    expect(String(confirmSpy.mock.calls[0][0])).toContain('Transformer 论文精读')
    expect(String(confirmSpy.mock.calls[0][0])).toContain('笔记与文件都会保留')

    confirmSpy.mockReturnValue(true)
    await userEvent.click(screen.getByRole('button', { name: '删除' }))

    await waitFor(() => expect(mockedDelete).toHaveBeenCalledWith('p-1'))
    await waitFor(() => expect(mockedProjects).toHaveBeenCalledTimes(2))
  })
})

describe('项目下的笔记', () => {
  it('★ 展开才拉详情：显示笔记标题/状态/大小，再点收起即消失', async () => {
    mockedDetail.mockResolvedValue({
      ...makeProject(),
      notes: [
        makeNoteInFolder({ id: 'n-1', title: 'Attention Is All You Need', file_size: 2048 }),
        makeNoteInFolder({
          id: 'n-2',
          title: 'BERT 预训练',
          source_type: 'docx',
          status: 'learning',
          file_size: 3 * 1024 * 1024,
        }),
      ],
    })
    renderPage()
    await expandProject()

    expect(await screen.findByText('Attention Is All You Need')).toBeInTheDocument()
    expect(mockedDetail).toHaveBeenCalledWith('p-1')
    expect(screen.getByText('BERT 预训练')).toBeInTheDocument()
    expect(screen.getByText('2.0 KB')).toBeInTheDocument()
    expect(screen.getByText('3.0 MB')).toBeInTheDocument()
    expect(screen.getByText('learning')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /收起笔记/ }))

    expect(screen.queryByText('Attention Is All You Need')).not.toBeInTheDocument()
  })

  it('安全：详情缺 notes 字段时显示"项目暂无笔记"，不整页崩', async () => {
    mockedDetail.mockResolvedValue({ ...makeProject() } as never)
    renderPage()
    await expandProject()

    expect(await screen.findByText(/项目暂无笔记/)).toBeInTheDocument()
    expect(screen.queryByText('这个页面出错了')).not.toBeInTheDocument()
  })

  it('安全：笔记行缺 file_size / source_type / status 时照常渲染', async () => {
    mockedDetail.mockResolvedValue({
      ...makeProject(),
      notes: [
        {
          id: 'n-2',
          title: 'BERT 预训练',
          created_at: '2026-09-01T10:00:00Z',
        } as never,
      ],
    })
    renderPage()
    await expandProject()
    await screen.findByText('BERT 预训练')

    // 未知状态落到 status-unknown 兜底（utils/labels.ts 的单一出口），而不是拼一个不存在的类名
    const row = screen.getByText('BERT 预训练').closest('.note-select-card') as HTMLElement
    expect(row.querySelector('.status-unknown')).not.toBeNull()
    // 来源类型未知时用 markdown 徽章兜底
    expect(row.querySelector('.badge-markdown')).not.toBeNull()
    expect(screen.queryByText('这个页面出错了')).not.toBeInTheDocument()
  })

  it('安全：候选笔记缺 project_ids 时全部列出（视为尚未归属本项目）', async () => {
    mockedNotes.mockResolvedValue({
      items: [
        makeNote({ id: 'n-1', title: 'Attention Is All You Need' }),
        makeNote({ id: 'n-2', title: 'BERT 预训练' }),
      ],
      total: 2,
      page: 1,
      page_size: 999,
    })
    renderPage()
    await screen.findByText('Transformer 论文精读')

    await userEvent.click(screen.getByRole('button', { name: '添加笔记' }))

    // project_ids 是可选字段：缺失时不能把"全部笔记"误判成"都已归属"而清空候选
    expect(await screen.findByText('Attention Is All You Need')).toBeInTheDocument()
    expect(screen.getByText('BERT 预训练')).toBeInTheDocument()
    expect(screen.queryByText('这个页面出错了')).not.toBeInTheDocument()
  })

  it('★ 添加笔记：只列尚未归属本项目的笔记，搜索过滤后勾选提交', async () => {
    mockedNotes.mockResolvedValue({
      items: [
        makeNote({ id: 'n-1', title: 'Attention Is All You Need', project_ids: ['p-1'] }),
        makeNote({ id: 'n-2', title: 'BERT 预训练' }),
        makeNote({ id: 'n-3', title: 'GPT 的缩放定律', project_ids: ['p-2'] }),
      ],
      total: 3,
      page: 1,
      page_size: 999,
    })
    renderPage()
    await screen.findByText('Transformer 论文精读')

    await userEvent.click(screen.getByRole('button', { name: '添加笔记' }))

    expect(await screen.findByText('BERT 预训练')).toBeInTheDocument()
    // 已经属于本项目的笔记不能再出现在候选里（否则会被反复"添加"）
    expect(screen.queryByText('Attention Is All You Need')).not.toBeInTheDocument()
    expect(mockedNotes).toHaveBeenCalledWith(1, 999, undefined, undefined)
    // 没勾选时确认按钮禁用，避免空提交
    expect(addConfirmButton()).toBeDisabled()

    await userEvent.type(screen.getByPlaceholderText(/按标题搜索候选笔记/), '缩放')

    expect(screen.queryByText('BERT 预训练')).not.toBeInTheDocument()
    expect(screen.getByText('GPT 的缩放定律')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('checkbox', { name: /GPT 的缩放定律/ }))
    expect(addConfirmButton()).toBeEnabled()
    await userEvent.click(addConfirmButton())

    await waitFor(() => expect(mockedAddNotes).toHaveBeenCalledWith('p-1', ['n-3']))
    // 添加成功后关闭面板并刷新列表
    await waitFor(() => expect(screen.queryByText('GPT 的缩放定律')).not.toBeInTheDocument())
    expect(mockedProjects).toHaveBeenCalledTimes(2)
  })

  it('★ 点笔记行跳转到笔记详情；点「移出」不跳转', async () => {
    mockedDetail.mockResolvedValue({
      ...makeProject(),
      notes: [makeNoteInFolder({ id: 'n-2', title: 'BERT 预训练' })],
    })
    renderPage()
    await expandProject()
    await screen.findByText('BERT 预训练')

    const row = screen.getByText('BERT 预训练').closest('.note-select-card') as HTMLElement

    // 「移出」按钮必须阻止冒泡，否则用户想去掉归属却被弹到笔记页
    confirmSpy.mockReturnValue(false)
    await userEvent.click(within(row).getByRole('button', { name: '移出' }))
    expect(mockedRemoveNote).not.toHaveBeenCalled()
    expect(screen.queryByText('笔记详情页:n-2')).not.toBeInTheDocument()

    await userEvent.click(screen.getByText('BERT 预训练'))
    expect(await screen.findByText('笔记详情页:n-2')).toBeInTheDocument()
  })

  it('★ 移出笔记要先确认，确认后调用 removeNoteFromProject 并刷新', async () => {
    mockedDetail.mockResolvedValue({
      ...makeProject(),
      notes: [makeNoteInFolder({ id: 'n-2', title: 'BERT 预训练' })],
    })
    renderPage()
    await expandProject()
    await screen.findByText('BERT 预训练')

    const row = screen.getByText('BERT 预训练').closest('.note-select-card') as HTMLElement
    await userEvent.click(within(row).getByRole('button', { name: '移出' }))

    expect(String(confirmSpy.mock.calls[0][0])).toContain('BERT 预训练')
    await waitFor(() => expect(mockedRemoveNote).toHaveBeenCalledWith('p-1', 'n-2'))
    await waitFor(() => expect(mockedProjects).toHaveBeenCalledTimes(2))
    // 展开状态下的笔记列表要跟着刷新，否则被移出的笔记还挂在页面上
    await waitFor(() => expect(mockedDetail).toHaveBeenCalledTimes(2))
  })
})

describe('扫描导入', () => {
  it('★ 扫描后展示统计，有新导入时刷新列表', async () => {
    renderPage()
    await screen.findByText('Transformer 论文精读')

    await userEvent.click(screen.getByRole('button', { name: '扫描导入' }))

    expect(await screen.findByText('扫描结果')).toBeInTheDocument()
    expect(mockedScan).toHaveBeenCalledWith('p-1')
    expect(screen.getByText('新增 2')).toBeInTheDocument()
    expect(screen.getByText('跳过 1')).toBeInTheDocument()
    expect(screen.getByText('不支持 0')).toBeInTheDocument()
    await waitFor(() => expect(mockedProjects).toHaveBeenCalledTimes(2))
  })

  it('★ 收件箱里没有新文件时告诉用户文件该放哪里', async () => {
    mockedScan.mockResolvedValue(makeScanResult({ scanned: 0, imported: 0, skipped: 0 }))
    renderPage()
    await screen.findByText('Transformer 论文精读')

    await userEvent.click(screen.getByRole('button', { name: '扫描导入' }))

    expect(await screen.findByText(/目录发现新文件/)).toBeInTheDocument()
    // 没有新增就不必再拉一次列表
    expect(mockedProjects).toHaveBeenCalledTimes(1)
  })

  it('扫描失败时显示错误提示，不假装成功', async () => {
    mockedScan.mockRejectedValue(new Error('后端未启动'))
    renderPage()
    await screen.findByText('Transformer 论文精读')

    await userEvent.click(screen.getByRole('button', { name: '扫描导入' }))

    expect(
      await screen.findByText('扫描导入失败，请确认后端服务可用后重试'),
    ).toBeInTheDocument()
    expect(screen.queryByText('扫描结果')).not.toBeInTheDocument()
  })
})

/**
 * 崩溃高发路径：本项目已经因为「一个可选数组字段是 undefined 却直接迭代」白屏过一次
 * （NoteDetail 的 noteLinks，见 NoteDetail.test.tsx）。这里把 Projects 侧同类的
 * 字段缺失/结构漂移逐条喂进去，钉死**降级行为**而不是"不抛异常就行"。
 */
describe('契约漂移时的健壮性（缺字段一律降级，不再崩到错误边界）', () => {
  it('/projects 返回 {items:[...]} 包装对象时取内层数组渲染，不崩也不丢数据', async () => {
    mockedProjects.mockResolvedValue({ items: [makeProject()] } as never)
    renderPage()

    // 包装对象只是形状漂移：取内层数组照常渲染（用户的项目还在，不是"一个都没有"）
    expect(await screen.findByText('Transformer 论文精读')).toBeInTheDocument()
    expect(screen.getByText('2 篇笔记')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /查看笔记（2）/ })).toBeInTheDocument()
    expect(screen.queryByText('还没有项目')).not.toBeInTheDocument()
    expect(screen.queryByText('这个页面出错了')).not.toBeInTheDocument()
    // 关键：不能再把 undefined 丢给渲染层
    expect(screen.queryByText(/undefined/)).not.toBeInTheDocument()
  })

  it('/projects 返回空响应（undefined）时按空列表降级为空状态，不崩', async () => {
    mockedProjects.mockResolvedValue(undefined as never)
    renderPage()

    expect(await screen.findByText('还没有项目')).toBeInTheDocument()
    // 页面骨架仍在：头部、创建入口、说明都在，不是白屏
    expect(screen.getByRole('heading', { name: '项目' })).toBeInTheDocument()
    expect(screen.getByText(/项目作为标签归属笔记/)).toBeInTheDocument()
    expect(screen.queryByText('这个页面出错了')).not.toBeInTheDocument()
  })

  it('安全：项目缺 description / note_count 时照常渲染，计数显示 0 而不是字面量 undefined', async () => {
    mockedProjects.mockResolvedValue([
      { id: 'p-1', user_id: 'u-1', name: '没有描述的项目' } as never,
    ])
    renderPage()

    expect(await screen.findByText('没有描述的项目')).toBeInTheDocument()
    expect(screen.getByText('0 篇笔记')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /查看笔记（0）/ })).toBeInTheDocument()
    expect(screen.queryByText(/undefined/)).not.toBeInTheDocument()
    expect(screen.queryByText('这个页面出错了')).not.toBeInTheDocument()
  })

  it('候选笔记 title 为 null 时按标题搜索照常工作（null 视为空串），不崩', async () => {
    mockedNotes.mockResolvedValue({
      items: [
        makeNote({ id: 'n-1', title: null as never }),
        makeNote({ id: 'n-2', title: 'BERT 预训练' }),
      ],
      total: 2,
      page: 1,
      page_size: 999,
    })
    renderPage()
    await screen.findByText('Transformer 论文精读')

    await userEvent.click(screen.getByRole('button', { name: '添加笔记' }))
    await screen.findByText('BERT 预训练')

    // 不搜索时两条都在（title 缺失只渲染成空标签，不影响其它条目）
    expect(screen.getAllByRole('checkbox')).toHaveLength(2)

    await userEvent.type(screen.getByPlaceholderText(/按标题搜索候选笔记/), 'B')

    // 输入搜索词后仍然是安全的：命中标题含 "B" 的那条，缺标题的那条不参与匹配
    expect(screen.getByText('BERT 预训练')).toBeInTheDocument()
    expect(screen.getAllByRole('checkbox')).toHaveLength(1)
    expect(screen.queryByText('这个页面出错了')).not.toBeInTheDocument()
  })
})
