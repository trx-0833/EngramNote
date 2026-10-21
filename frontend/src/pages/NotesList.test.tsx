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
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
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

function renderPage() {
  return render(
    <MemoryRouter>
      <NotesList />
    </MemoryRouter>,
  );
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
