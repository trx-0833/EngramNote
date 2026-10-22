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
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryWrapper } from '../test/render';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ConfirmProvider } from '../components/ConfirmProvider';
import NoteDetail from './NoteDetail';

// ── mock 掉整条 API 层：本文件测的是页面行为，不是接口契约 ──
vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client');
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
    exportNote: vi.fn(),
    startUnderstanding: vi.fn(),
    startCleaning: vi.fn(),
    stopCleaning: vi.fn(),
    restoreBlock: vi.fn(),
    deleteBlock: vi.fn(),
  };
});

vi.mock('../components/Toast', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));
// 子组件按需请求数据；本文件只关心页面本体，把它们换成桩
vi.mock('../components/NoteAskPanel', () => ({ default: () => <div data-testid="ask-panel" /> }));
vi.mock('../components/VersionHistory', () => ({
  default: () => <div data-testid="version-history" />,
}));
vi.mock('../components/DiffView', () => ({ default: () => <div data-testid="diff-view" /> }));
vi.mock('../api/tasks', async () => {
  const actual = await vi.importActual<typeof import('../api/tasks')>('../api/tasks');
  return { ...actual, listNoteTasks: vi.fn().mockResolvedValue({ items: [], total: 0 }) };
});

import {
  deleteNote,
  exportNote,
  getAnnotations,
  getKnowledgeCards,
  getNote,
  getNoteLinks,
  getQuestions,
  startUnderstanding,
  updateNoteContent,
  type NoteDetail as NoteDetailType,
} from '../api/client';

const mockedGetNote = vi.mocked(getNote);
const mockedCards = vi.mocked(getKnowledgeCards);
const mockedQuestions = vi.mocked(getQuestions);
const mockedAnnotations = vi.mocked(getAnnotations);
const mockedLinks = vi.mocked(getNoteLinks);
const mockedDelete = vi.mocked(deleteNote);
const mockedUnderstand = vi.mocked(startUnderstanding);
const mockedUpdateContent = vi.mocked(updateNoteContent);
const mockedExport = vi.mocked(exportNote);

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
  } as NoteDetailType;
}

/** 与 `NoteLinksResponse` 契约一致的响应（少一个字段会让页面整体崩掉，见文件末尾说明） */
function emptyLinks() {
  return {
    linked_materials: [],
    linked_personal_notes: [],
    dangling_links: [],
  } as never;
}

function renderPage() {
  return render(
    // 迁移 5.2 / C1：`NoteDetail` 的子树里有 `TaskProgress`，它现在用 `useQuery`
    // ⇒ 渲染树必须有 `QueryClientProvider`。
    //
    // ⚠️ 这里用的是 `QueryWrapper` 而不是 `renderWithProviders`：
    // 后者只给一个裸的 `MemoryRouter`，而这个页面用 `useParams` 取 `noteId`
    // —— 那要求 `MemoryRouter` 里有**匹配的 `<Route path="/notes/:noteId">`**，
    // 光有路由上下文不够（实测报错会指向"参数取不到"，与 Provider 无关）。
    <QueryWrapper>
      {/* 批次 D3 后半：`useNoteActions` / `useNoteAnnotations` 的确认改走 `useConfirm()`，
          渲染树里必须有这个宿主（层级与 main.tsx 一致） */}
      <ConfirmProvider>
        <MemoryRouter initialEntries={['/notes/note-1']}>
          <Routes>
            <Route path="/notes/:noteId" element={<NoteDetail />} />
          </Routes>
        </MemoryRouter>
      </ConfirmProvider>
    </QueryWrapper>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedCards.mockResolvedValue({ items: [], total: 0 } as never);
  mockedQuestions.mockResolvedValue({ items: [], total: 0 } as never);
  mockedAnnotations.mockResolvedValue([] as never);
  mockedLinks.mockResolvedValue(emptyLinks());
  mockedDelete.mockResolvedValue(undefined as never);
  mockedUpdateContent.mockResolvedValue({} as never);
});

describe('加载与状态展示', () => {
  it('加载成功后显示笔记标题', async () => {
    mockedGetNote.mockResolvedValue(makeNote());
    renderPage();
    expect(await screen.findByText('锂离子电池的浮充与均充')).toBeInTheDocument();
  });

  it('★ 已清洗的笔记默认显示清洗版（原始版只是其中一个视图）', async () => {
    mockedGetNote.mockResolvedValue(makeNote());
    renderPage();
    // 清洗版正文出现，原始版正文不出现 —— 用户上次看到的就是清洗结果
    expect(await screen.findByText(/浮充：恒压运行方式/)).toBeInTheDocument();
    expect(screen.queryByText(/浮充是蓄电池的一种运行方式/)).not.toBeInTheDocument();
  });

  it('加载失败时显示错误与重试入口，而不是白屏', async () => {
    mockedGetNote.mockRejectedValue(new Error('笔记不存在'));
    renderPage();
    expect(await screen.findByText(/笔记不存在/)).toBeInTheDocument();
  });

  it('★ 转换中的笔记显示任务进度组件（阶段 5.11 的成果，不能被拆回静态文案）', async () => {
    mockedGetNote.mockResolvedValue(makeNote({ status: 'converting', clean_md_content: null }));
    renderPage();
    // 没有 task_runs 记录 → 组件退回静态兜底文案
    expect(await screen.findByTestId('task-progress-fallback')).toHaveTextContent('正在转换中');
  });
});

/**
 * 「还没建索引」提示（B2 的"可见"那半，2026-10-22）
 *
 * ## 为什么这一组必须存在
 *
 * 清洗路径刻意不写向量，补嵌入由 Beat 的 `backfill_embeddings` 周期补齐。
 * 在那之前这篇笔记**只被 BM25 检索到** —— 而用户此前**不提问就看不到**这件事
 * （只有问答时的 SSE `meta.retrieval_status` 会提示）。
 *
 * ## 三条判据各一条用例
 *
 * 关键是第三条：**`0` 与 `null` 都不能显示**。
 * `0` 是常态（索引都建好了），常驻一条"0 个待建"只会变成噪音；
 * `null` 是"还没统计"（笔记还没清洗完）—— "没测到"不能长得像"有问题"。
 * 没有这条反向断言，把判据写成 `!= null` 也能让正向那条绿。
 */
describe('索引未建好的提示', () => {
  const PENDING_TEXT = /还有 5 段内容在建立索引/;

  it('★ 有未建索引的段落时显示提示，并说清"会自愈"', async () => {
    mockedGetNote.mockResolvedValue(makeNote({ pending_embedding_chunks: 5 }));
    renderPage();

    const hint = await screen.findByText(PENDING_TEXT);
    expect(hint).toBeInTheDocument();
    // 用户需要知道"要不要我做什么" —— 答案是不用，那就直说
    expect(hint).toHaveTextContent(/每半小时自动补齐/);
  });

  it('★ 全部已建索引（0）时**不显示** —— 0 是常态，不是问题', async () => {
    mockedGetNote.mockResolvedValue(makeNote({ pending_embedding_chunks: 0 }));
    renderPage();
    await screen.findByText('锂离子电池的浮充与均充');

    expect(screen.queryByText(PENDING_TEXT)).not.toBeInTheDocument();
  });

  it('★ 还没统计（null）时也不显示 —— "没测到"不能长得像"有问题"', async () => {
    mockedGetNote.mockResolvedValue(makeNote({ pending_embedding_chunks: null }));
    renderPage();
    await screen.findByText('锂离子电池的浮充与均充');

    expect(screen.queryByText(PENDING_TEXT)).not.toBeInTheDocument();
  });

  it('老接口没这个字段（undefined）时同样不显示（向后兼容）', async () => {
    const note = makeNote();
    delete (note as Record<string, unknown>).pending_embedding_chunks;
    mockedGetNote.mockResolvedValue(note);
    renderPage();
    await screen.findByText('锂离子电池的浮充与均充');

    expect(screen.queryByText(PENDING_TEXT)).not.toBeInTheDocument();
  });
});

describe('编辑与保存', () => {
  it('★ 进入编辑、修改、保存走的是 updateNoteContent', async () => {
    mockedGetNote.mockResolvedValue(makeNote());
    renderPage();
    await screen.findByText('锂离子电池的浮充与均充');

    await userEvent.click(screen.getByRole('button', { name: /编辑/ }));
    const textarea = await screen.findByRole('textbox');
    await userEvent.clear(textarea);
    await userEvent.type(textarea, '改过的内容');
    await userEvent.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() => expect(mockedUpdateContent).toHaveBeenCalled());
    const [noteId, content] = mockedUpdateContent.mock.calls[0];
    expect(noteId).toBe('note-1');
    expect(content).toContain('改过的内容');
  });

  it('取消编辑不调用保存接口（改了又反悔不该留下痕迹）', async () => {
    mockedGetNote.mockResolvedValue(makeNote());
    renderPage();
    await screen.findByText('锂离子电池的浮充与均充');

    await userEvent.click(screen.getByRole('button', { name: /编辑/ }));
    await screen.findByRole('textbox');
    await userEvent.click(screen.getByRole('button', { name: '取消' }));

    expect(mockedUpdateContent).not.toHaveBeenCalled();
    expect(await screen.findByText('锂离子电池的浮充与均充')).toBeInTheDocument();
  });
});

describe('契约漂移时的健壮性', () => {
  it('★ 关联资料响应缺字段时不整页崩掉（本轮实测过：undefined.length = 白屏）', async () => {
    mockedGetNote.mockResolvedValue(makeNote());
    // 模拟"后端少返回一个数组字段"（契约漂移 / 缓存里的旧结构）
    mockedLinks.mockResolvedValue({ linked_materials: [] } as never);
    renderPage();

    // 页面主体必须照常渲染：字段缺失最坏只能是"这一块不显示"
    expect(await screen.findByText('锂离子电池的浮充与均充')).toBeInTheDocument();
    expect(screen.queryByText('被以下笔记引用')).not.toBeInTheDocument();
    expect(screen.queryByText('关联的学习资料')).not.toBeInTheDocument();
  });
});

describe('删除与重新理解', () => {
  it('★ 删除必须先确认：点"删除"只弹窗、不调接口', async () => {
    mockedGetNote.mockResolvedValue(makeNote());
    renderPage();
    await screen.findByText('锂离子电池的浮充与均充');

    // 按钮文案是"删除"，"移入回收站"是弹窗标题
    await userEvent.click(screen.getByRole('button', { name: '删除' }));
    expect(mockedDelete).not.toHaveBeenCalled();
    expect(await screen.findByText('移入回收站')).toBeInTheDocument();
  });

  it('★ archived 笔记重新学习要二次确认：第一次不带 confirm，确认后才带 confirm=true', async () => {
    mockedGetNote.mockResolvedValue(makeNote({ status: 'archived' }));
    mockedUnderstand.mockResolvedValue({
      requires_confirm: true,
      impact: { cards: 3, quizzes: 0, review_logs: 0, relations: 0 },
    } as never);
    renderPage();
    await screen.findByText('锂离子电池的浮充与均充');

    await userEvent.click(screen.getByRole('button', { name: 'AI预处理' }));

    // 第一次探测影响时**只**弹确认框，第二次请求还没发出去
    await screen.findByRole('dialog');
    expect(mockedUnderstand).toHaveBeenCalledTimes(1);
    // 而且必须先让用户看到"将删除什么"
    expect(screen.getByText(/将删除：3 张知识卡片/)).toBeInTheDocument();

    // 用户点了确认，才真的清空并重跑
    await userEvent.click(screen.getByRole('button', { name: '继续' }));

    await waitFor(() => expect(mockedUnderstand).toHaveBeenCalledTimes(2));
    // 第一次探测影响（不带 confirm），第二次才是真的清空并重跑
    expect(mockedUnderstand.mock.calls[0][1]).toBe(false);
    expect(mockedUnderstand.mock.calls[1][1]).toBe(true);
  });

  it('用户在二次确认里点"取消"时不发第二次请求（不可撤销的操作不能默认执行）', async () => {
    mockedGetNote.mockResolvedValue(makeNote({ status: 'archived' }));
    mockedUnderstand.mockResolvedValue({ requires_confirm: true, impact: { cards: 1 } } as never);
    renderPage();
    await screen.findByText('锂离子电池的浮充与均充');

    await userEvent.click(screen.getByRole('button', { name: 'AI预处理' }));

    // 真实的确认框：点"取消"（不是靠 confirm 间谍返回 false）
    await screen.findByRole('dialog');
    await userEvent.click(screen.getByRole('button', { name: '取消' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(mockedUnderstand).toHaveBeenCalledTimes(1);
    expect(mockedUnderstand.mock.calls[0][1]).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// 导出（想法 4「设计导出功能」）
// ---------------------------------------------------------------------------

describe('导出笔记正文', () => {
  let createdUrls: string[];
  let revokedUrls: string[];
  /** 被"点击"过的下载锚点（在真实浏览器里这一步会触发下载） */
  let clickedAnchors: HTMLAnchorElement[];

  beforeEach(() => {
    createdUrls = [];
    revokedUrls = [];
    clickedAnchors = [];

    // jsdom 不实现 createObjectURL / revokeObjectURL（它们属于 blob URL 存储）
    vi.stubGlobal('URL', {
      ...URL,
      createObjectURL: vi.fn(() => {
        const url = `blob:mock/${createdUrls.length}`;
        createdUrls.push(url);
        return url;
      }),
      revokeObjectURL: vi.fn((url: string) => {
        revokedUrls.push(url);
      }),
    });

    // jsdom 里 `a.click()` 会尝试导航并打印 "Not implemented: navigation"。
    // 这里拦住它并记下锚点 —— 我们要断言的正是"锚点被造出来、带上正确的
    // href 与 download、并被点了一下"，而这三件事都在这一步之前完成。
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      clickedAnchors.push(this);
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('★ 点「导出」调接口，并用后端给的文件名触发下载', async () => {
    mockedGetNote.mockResolvedValue(makeNote());
    mockedExport.mockResolvedValue({ blob: new Blob(['# 正文']), filename: '笔记标题.md' });
    renderPage();
    await screen.findByText('锂离子电池的浮充与均充');

    await userEvent.click(screen.getByRole('button', { name: '导出' }));

    await waitFor(() => expect(mockedExport).toHaveBeenCalledWith('note-1', 'clean'));
    // 锚点真的被点了一下，且带上了后端给的文件名与刚造出来的 blob URL
    expect(clickedAnchors).toHaveLength(1);
    expect(clickedAnchors[0].download).toBe('笔记标题.md');
    expect(clickedAnchors[0].href).toBe(createdUrls[0]);
  });

  it('★ blob URL 被释放（不释放会把整篇正文钉在内存里）', async () => {
    mockedGetNote.mockResolvedValue(makeNote());
    mockedExport.mockResolvedValue({ blob: new Blob(['x']), filename: 'a.md' });
    renderPage();
    await screen.findByText('锂离子电池的浮充与均充');

    await userEvent.click(screen.getByRole('button', { name: '导出' }));

    await waitFor(() => expect(revokedUrls).toEqual(createdUrls));
  });

  it('★ 导出哪一版跟着当前视图走：切到原始版后导的是 original', async () => {
    mockedGetNote.mockResolvedValue(makeNote());
    mockedExport.mockResolvedValue({ blob: new Blob(['x']), filename: 'a.md' });
    renderPage();
    await screen.findByText('锂离子电池的浮充与均充');

    await userEvent.click(screen.getByRole('button', { name: '原始版' }));
    await userEvent.click(screen.getByRole('button', { name: '导出' }));

    await waitFor(() => expect(mockedExport).toHaveBeenCalledWith('note-1', 'original'));
  });

  it('★ 后端没给文件名时回退到 `${标题}.md`（下载不该因为头缺失而失败）', async () => {
    mockedGetNote.mockResolvedValue(makeNote());
    mockedExport.mockResolvedValue({ blob: new Blob(['x']), filename: null });
    renderPage();
    await screen.findByText('锂离子电池的浮充与均充');

    await userEvent.click(screen.getByRole('button', { name: '导出' }));

    await waitFor(() => expect(clickedAnchors[0].download).toBe('锂离子电池的浮充与均充.md'));
  });

  it('★ 对比视图下禁用导出（"对比"不是某一版，没有"这一版"可导）', async () => {
    mockedGetNote.mockResolvedValue(makeNote());
    renderPage();
    await screen.findByText('锂离子电池的浮充与均充');

    await userEvent.click(screen.getByRole('button', { name: '对比视图' }));

    expect(screen.getByRole('button', { name: '导出' })).toBeDisabled();
    expect(mockedExport).not.toHaveBeenCalled();
  });

  it('★ 处理中禁用导出（正文还没生成或正在改写，导出来是半成品）', async () => {
    mockedGetNote.mockResolvedValue(makeNote({ status: 'cleaning' }));
    renderPage();
    await screen.findByText('锂离子电池的浮充与均充');

    expect(screen.getByRole('button', { name: '导出' })).toBeDisabled();
  });

  it('导出失败时提示后端文案，且不留下 blob URL（不泄漏）', async () => {
    // 用 `status: 'converted'` 表达"还没清洗"（`NoteDetail` 类型里**没有**
    // `clean_md_path` —— 那是后端存储路径，不出现在页面用的详情类型上）。
    // 失败本身由 mock 抛错驱动，所以这个 override 只是让场景说得通。
    mockedGetNote.mockResolvedValue(makeNote({ status: 'converted' }));
    mockedExport.mockRejectedValue(new Error('这篇笔记还没有清洗版，请先清洗或改导原始版'));
    renderPage();
    await screen.findByText('锂离子电池的浮充与均充');

    await userEvent.click(screen.getByRole('button', { name: '导出' }));

    // 失败路径上不该造出任何 URL（`createObjectURL` 在 `await` 之后才调用）
    await waitFor(() => expect(mockedExport).toHaveBeenCalled());
    expect(createdUrls).toHaveLength(0);
    expect(revokedUrls).toHaveLength(0);
  });
});
