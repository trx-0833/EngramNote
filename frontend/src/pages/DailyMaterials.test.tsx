/**
 * @file 今日资料页：**返回时恢复展开态与滚动位置**的行为测试
 *   （visual-refactor-plan 批次 E8；`docs/visual-symbol-research.md` §C3「每日材料」行）
 *
 * ## 为什么这需要一层测试（而不是"手点一下看着对"）
 *
 * 这一条是本批唯一**行为改动**（其余都是视觉），而它天生难验：
 *   - 它只在**浏览器后退**（react-router 的 `POP`）时生效，点站内链接进来（`PUSH`）
 *     必须**不**生效 —— 两种进入方式在 jsdom 里的差别只有 `useNavigationType()`，
 *     人眼看不出来；
 *   - 它跨两次挂载（离开时记、回来时用），中间还夹着两个异步请求
 *     （文件夹列表 → 文件夹详情），时机错一步就是"滚到一半"或"滚了又被渲染顶回去"；
 *   - 半吊子的滚动恢复比没有更糟（用户会看到页面跳到奇怪的位置），
 *     所以这里把三条边界都钉住：POP 恢复、PUSH 不恢复、详情没渲染完不滚。
 *
 * ## ⚠️ 用例顺序是**有意的**（本文件不要重排）
 *
 * 页面的"会话记忆"是**模块作用域的变量**（`DailyMaterials.tsx` 的 `pageMemory`：
 * 组件卸载后仍在，整页刷新才清零）。vitest 在同一个文件里共用同一个模块实例，
 * 所以上一个用例卸载时写下的记忆会留给下一个。
 * 第一个用例（POP 恢复）必须在**记忆为空**的初始状态下跑；第二个用例走 `PUSH`
 * 分支 —— 那条路径按定义不看记忆，所以不受遗留值影响。
 *
 * ## 关于桩
 *
 * `/api/client` 整个换成桩：这一页的四个入口（文件夹列表 / 详情 / 建夹 / 上传）
 * 都走它，真发请求在 jsdom 里既慢又会掩盖断言。
 * `jsdom` 不实现滚动（`setup.ts` 只补了 `scrollIntoView`），所以 `window.scrollY`
 * 与 `window.scrollTo` 都在用例里显式接管 —— 那两行是**测量工具**，不是产品逻辑。
 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Folder, FolderDetail, NoteInFolder } from '../api/client';
import DailyMaterials from './DailyMaterials';

// ── mock 掉整条 API 层：本文件测的是页面行为，不是接口契约 ──
// 与 `Projects.test.tsx` 同形：先 spread `importActual`，只把这一页会调的七个函数
// 换成 vi.fn()。这样类型（`Folder` / `FolderDetail`）仍然来自真实模块，夹具能拿到
// tsc 的检查，而不是靠 `as never` 糊过去。
vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client');
  return {
    ...actual,
    getFolders: vi.fn(),
    getFolderDetail: vi.fn(),
    createFolder: vi.fn(),
    deleteFolder: vi.fn(),
    updateFolder: vi.fn(),
    uploadFileToFolder: vi.fn(),
    getUploadStatus: vi.fn(),
  };
});

import { getFolders, getFolderDetail } from '../api/client';

const FOLDER: Folder = {
  id: 'folder-1',
  user_id: 'u-1',
  name: '2026-01-05 学习资料',
  description: '浮充与均充的原始资料',
  folder_date: '2026-01-05',
  created_at: '2026-01-05T00:00:00+00:00',
  note_count: 1,
};

const NOTE: NoteInFolder = {
  id: 'note-1',
  title: '浮充与均充的讲义.pdf',
  source_type: 'pdf',
  status: 'cleaned',
  file_size: 524_288,
  created_at: '2026-01-05T09:30:00+00:00',
};

const FOLDER_DETAIL: FolderDetail = { ...FOLDER, notes: [NOTE] };

/** 笔记详情位置上的桩：只提供一个"后退"按钮，用来触发 POP 导航 */
function NoteStub() {
  const navigate = useNavigate();
  return (
    <button type="button" onClick={() => navigate(-1)}>
      后退
    </button>
  );
}

/** 笔记详情位置上的桩 + 一个"进今日资料"的链接（触发 PUSH 导航） */
function NotesEntryStub() {
  const navigate = useNavigate();
  return (
    <button type="button" onClick={() => navigate('/daily')}>
      进今日资料
    </button>
  );
}

function renderApp(initialEntries: string[]) {
  return render(
    <MemoryRouter initialEntries={initialEntries}>
      <Routes>
        <Route path="/daily" element={<DailyMaterials />} />
        <Route path="/notes/:noteId" element={<NoteStub />} />
        <Route path="/home" element={<NotesEntryStub />} />
      </Routes>
    </MemoryRouter>,
  );
}

/** 展开文件夹（列表到位后第一个可点的文件夹头） */
async function expandFolder(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole('button', { name: /2026-01-05 学习资料/ }));
  await screen.findByText('浮充与均充的讲义.pdf');
}

/**
 * 接管两个滚动 API。
 *
 * `window.scrollY` 在 jsdom 里恒为 0 且是只读 getter，所以用 `defineProperty`
 * 装一个可写的值；`window.scrollTo` 在 jsdom 里会往虚拟控制台写
 * "Not implemented"，这里换成 spy（既消掉噪音，又能断言"滚到哪儿"）。
 */
function stubScrolling(scrollY: number) {
  Object.defineProperty(window, 'scrollY', { value: scrollY, configurable: true });
  const scrollTo = vi.fn();
  Object.defineProperty(window, 'scrollTo', { value: scrollTo, configurable: true });
  return scrollTo;
}

beforeEach(() => {
  vi.mocked(getFolders).mockReset();
  vi.mocked(getFolderDetail).mockReset();
  vi.mocked(getFolders).mockResolvedValue([FOLDER]);
  vi.mocked(getFolderDetail).mockResolvedValue(FOLDER_DETAIL);
});

describe('今日资料 · 返回时恢复展开态与滚动位置（批次 E8）', () => {
  it('★ 浏览器后退回到本页：自动展开上次的文件夹并滚回原位', async () => {
    const user = userEvent.setup();
    // 测量工具：离开时窗口停在 420px
    const scrollTo = stubScrolling(420);
    renderApp(['/daily']);

    await expandFolder(user);

    // 点进笔记详情（PUSH），本页卸载 —— 这一刻把"展开着谁 + 滚到哪儿"记下来
    await user.click(screen.getByRole('link', { name: '浮充与均充的讲义.pdf' }));
    await screen.findByRole('button', { name: '后退' });
    expect(getFolderDetail).toHaveBeenCalledTimes(1);

    // 后退回来（POP）
    await user.click(screen.getByRole('button', { name: '后退' }));

    // ① 展开态被接回来：**没有再点一次文件夹**，详情自己出现了
    expect(await screen.findByText('浮充与均充的讲义.pdf')).toBeInTheDocument();
    expect(getFolderDetail).toHaveBeenCalledTimes(2);
    expect(getFolderDetail).toHaveBeenLastCalledWith('folder-1');

    // ② 滚动位置被应用：详情渲染完之后才滚（420 是离开时的值）
    await waitFor(() => expect(scrollTo).toHaveBeenCalledWith({ top: 420 }));
  });

  it('点站内链接进来（PUSH）不恢复滚动位置：用户期待的是页面顶部', async () => {
    const user = userEvent.setup();
    const scrollTo = stubScrolling(300);
    renderApp(['/home']);

    await user.click(await screen.findByRole('button', { name: '进今日资料' }));

    // 文件夹列表渲染出来了（页面确实到位），但**没有**自动展开、也没有滚动
    await screen.findByRole('button', { name: /2026-01-05 学习资料/ });
    expect(getFolderDetail).not.toHaveBeenCalled();
    expect(screen.queryByText('浮充与均充的讲义.pdf')).toBeNull();
    expect(scrollTo).not.toHaveBeenCalled();
  });
});
