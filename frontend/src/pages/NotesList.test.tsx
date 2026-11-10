/**
 * 「导出全部（ZIP）」按钮（B4，2026-10-22）
 *
 * ## 为什么只测这一个按钮，而不是给整页补一套用例
 *
 * `NotesList.tsx` 此前**没有**测试文件。为了一次改动就给整页补一套渲染/分页/
 * 筛选的用例，是"顺手扩大改动面"——那些行为这轮没碰，而新增的用例里
 * 大部分会是在描述现状而不是在守改动。
 *
 * 所以这里只钉**这次新增的那三条判据**：
 *
 * | 判据 | 错了会怎样 |
 * |---|---|
 * | 搜索状态下**不显示** | 用户会以为导的是"搜出来的那些"，而实际导出整个库 |
 * | 点击真的调用 `exportNotesZip('clean')` | 按钮看起来响应了但什么都没做 |
 * | 失败时透传**后端文案** | 后端那句"没有可导出的正文 —— …请换一个版本或先清洗"说清了原因与出路，前端自造文案给不出这个信息 |
 */
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from '../test/render';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api/client', () => ({
  getNotes: vi.fn(),
  getArchivedNotes: vi.fn(),
  deleteNote: vi.fn(),
  retryConvert: vi.fn(),
  exportNotesZip: vi.fn(),
}));

const toast = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  info: vi.fn(),
  warning: vi.fn(),
}));
vi.mock('../components/Toast', () => ({ useToast: () => toast }));

import { exportNotesZip, getNotes } from '../api/client';
import NotesList from './NotesList';

const mockedGetNotes = vi.mocked(getNotes);
const mockedExportZip = vi.mocked(exportNotesZip);

function note() {
  return {
    id: 'n-1',
    user_id: 'u-1',
    title: '一篇笔记',
    source_type: 'pdf',
    status: 'cleaned',
    file_size: 1024,
    created_at: '2026-10-01T00:00:00Z',
    updated_at: '2026-10-02T00:00:00Z',
  } as never;
}

/**
 * 迁移 5.2 / C1：页面现在用 useQuery，所以渲染必须带取数层。
 * 
enderWithProviders 同时给 Provider 与 MemoryRouter（页面里用了 useNavigate），
 * 所以原来手写的 <MemoryRouter> 一起并进去了。
 */
function renderPage() {
  return renderWithProviders(<NotesList />, { route: '/notes' });
}

/** jsdom 没有 URL.createObjectURL —— 每次都要补，否则下载路径会抛 */
beforeEach(() => {
  vi.clearAllMocks();
  mockedGetNotes.mockResolvedValue({ items: [note()], total: 1, page: 1, page_size: 20 } as never);
  mockedExportZip.mockResolvedValue({ blob: new Blob(['zip']), filename: 'engramnote-export.zip' });
  URL.createObjectURL = vi.fn(() => 'blob:fake');
  URL.revokeObjectURL = vi.fn();
});

describe('导出全部', () => {
  it('★ 没有搜索关键词时显示「导出全部（ZIP）」', async () => {
    renderPage();
    expect(await screen.findByRole('button', { name: /导出全部（ZIP）/ })).toBeInTheDocument();
  });

  it('★★ 搜索状态下**不显示** —— 否则用户会以为导的是搜出来的那些', async () => {
    // 判据是"这个按钮导的是**整个库**"。搜索时按钮若还在，
    // 用户合理地把"全部"理解成"当前结果"，而实际会拿到整库。
    renderPage();
    await screen.findByRole('button', { name: /导出全部（ZIP）/ });

    await userEvent.type(screen.getByPlaceholderText('搜索笔记标题...'), '关键词');

    await waitFor(() =>
      expect(screen.queryByRole('button', { name: /导出全部（ZIP）/ })).not.toBeInTheDocument(),
    );
  });

  it('★ 点击调用 exportNotesZip("clean") 并触发下载', async () => {
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: /导出全部（ZIP）/ }));

    await waitFor(() => expect(mockedExportZip).toHaveBeenCalledWith('clean'));
    // 下载用的 object URL 必须释放（不释放会漏一份文件内容在内存里）
    await waitFor(() => expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:fake'));
    expect(toast.success).toHaveBeenCalled();
  });

  it('★ 失败时透传后端文案（而不是自造一句）', async () => {
    // 后端那句说清了"为什么"与"怎么办"；前端自造的"导出失败"给不出这些。
    mockedExportZip.mockRejectedValue(
      new Error('没有可导出的正文 —— 这些笔记都没有该版本的正文，请换一个版本或先清洗'),
    );
    renderPage();

    await userEvent.click(await screen.findByRole('button', { name: /导出全部（ZIP）/ }));

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        '没有可导出的正文 —— 这些笔记都没有该版本的正文，请换一个版本或先清洗',
      ),
    );
  });
});

/**
 * 整卡可点（2026-11-06）
 *
 * ## 为什么这两条判据值得单独写
 *
 * "点卡片任何地方都能进详情"是**拉伸链接**做的（标题 `<Link>` 的 `::after`
 * 铺满整张卡），而 jsdom 没有布局、没有伪元素命中测试 —— 真正的那一下只能由
 * `e2e/notes-list.spec.ts` 在真浏览器里点。这一层守的是它**赖以成立的两条前提**：
 *
 * | 前提 | 破坏了会怎样 |
 * |---|---|
 * | 卡片里**只有一个**链接，且卡片不是控件 | 多一个链接 = 多一块"点进去的是别的笔记"的区域；卡片变回 `role="button"` = F-17（nested-interactive）复发 |
 * | 右箭头是**装饰性图标**、并且在按钮组之外 | 退回 `→` 字符时跨平台字形不一；跟着按钮一起 hover 才出现，就等于"可点性提示"时有时无 |
 */
describe('整卡可点（拉伸链接）', () => {
  it('★★ 卡片里只有标题这一个链接，卡片本身不是控件（F-17 不复发）', async () => {
    const { container } = renderPage();

    const link = await screen.findByRole('link', { name: '一篇笔记' });
    expect(link).toHaveAttribute('href', '/notes/n-1');

    const article = container.querySelector('article');
    expect(article).not.toBeNull();
    // F-17 的形状是 `article[role="button"][tabindex="0"]` + 手写 Enter：
    // 那两个属性是"能点却键盘到不了 / 读屏念成按钮"的入口，一个都不能回来。
    expect(article).not.toHaveAttribute('role');
    expect(article).not.toHaveAttribute('tabindex');
    // 拉伸链接的前提：整张卡只有这一个链接，点哪儿都落到它
    expect(article!.querySelectorAll('a')).toHaveLength(1);
  });

  it('★ 右侧箭头是装饰性图标，且不再是 `→` 字符', async () => {
    renderPage();

    const chevron = await screen.findByTestId('note-chevron');
    expect(chevron).toHaveAttribute('aria-hidden', 'true');
    expect(chevron.querySelector('svg')).not.toBeNull();
    // 旧实现是一个 `→` 文本节点：字体不同则宽高不同，而且它当时挂在
    // `.noteListActions` 里（hover 才可见）—— 作为"整卡可点"的提示太弱。
    expect(screen.queryByText('→')).not.toBeInTheDocument();
  });
});

/**
 * 笔记列表：筛选条件从 URL 读回来（2026-11-10）
 *
 * ## 这条替代了什么
 *
 * 用户从列表点进一篇笔记再返回时，原先列表**重新挂载成默认视图**：
 * 角色回到「学习资料」、页码回到 1、关键词清空 —— 得重新定位一遍。
 * 现在这些都在 URL 上，后退回来就是同一个 URL，页面自然渲染同一个视图。
 *
 * 断言方式：**直接带着参数挂载页面**（等于"后退回到了那个 URL"），
 * 看它是否按参数取数、Tab 是否高亮、关键词是否回填。
 * 这比去戳内部 setState 更接近用户看到的东西。
 */
describe('笔记列表 · 筛选条件由 URL 决定', () => {
  it('★ 带 role/p/q 进来：按该角色与页码取数、Tab 高亮、关键词回填', async () => {
    renderWithProviders(<NotesList />, {
      route: '/notes?role=personal_note&p=2&q=%E7%94%B5%E6%B1%A0',
    });

    await waitFor(() =>
      expect(mockedGetNotes).toHaveBeenCalledWith(
        2,
        20,
        '电池',
        'personal_note',
        expect.anything(),
      ),
    );
    // Tab 高亮：读的是 URL 里的 role，而不是默认值
    expect(await screen.findByRole('button', { name: '我的笔记' })).toHaveClass(
      'filter-pill-active',
    );
    // 关键词回填到搜索框（用户能看到"我现在筛的是什么"）
    expect(screen.getByRole('searchbox')).toHaveValue('电池');
  });

  it('没有参数时：默认学习资料 + 第 1 页 + 无关键词', async () => {
    renderWithProviders(<NotesList />, { route: '/notes' });

    await waitFor(() =>
      expect(mockedGetNotes).toHaveBeenCalledWith(1, 20, undefined, 'material', expect.anything()),
    );
    expect(await screen.findByRole('button', { name: '学习资料' })).toHaveClass(
      'filter-pill-active',
    );
  });
});
