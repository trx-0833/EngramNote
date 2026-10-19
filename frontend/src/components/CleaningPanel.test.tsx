/**
 * @file 清洗面板的行为测试（2026-10-19 新增）
 *
 * ## 为什么现在才补、为什么必须补
 *
 * 这个组件此前**一行测试都没有**（全仓 grep `CleaningPanel` 只命中它自己、
 * 样式模块与 `NoteDetail.tsx` 的 import）。它过去只做"显示 + 调接口"，
 * 那些行为在 `NoteDetail.test.tsx` 的页面级用例里被顺带覆盖过一部分。
 *
 * 2026-10-19 给它加了**折叠**（用户要求：「重复块应该增加一个可以折叠的功能」），
 * 那是这个组件里第一处**纯前端状态** —— 页面级用例看不见它，
 * 而它有两层折叠容易搞混，所以按本仓库的既有顺序
 * **先补安全网、再动行为**（`overhaul-plan` 5.5 对三个巨型页面就是这么做的）。
 *
 * ## 两层折叠不是一回事（本文件把它们分开钉住）
 *
 * | 层 | 控件 | 折的是什么 |
 * |---|---|---|
 * | 段级 | `<h2>` 里的 `<button aria-expanded>` | **整段列表**（10 个重复块会占半屏） |
 * | 块级 | 每个块上的「查看内容 / 收起内容」 | **单个块的内容对比** |
 *
 * 段级折叠时**标题必须留着** —— 否则用户没有任何入口把它再打开。
 * 所以下面专门有一条用例断言"收起后按钮仍在、`aria-expanded` 变 false"。
 */
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { NoteDetail } from '../api/client';
import CleaningPanel from './CleaningPanel';
import { ConfirmProvider } from './ConfirmProvider';

// ── mock 掉 API 层：本文件测的是面板行为，不是接口契约 ──
vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client');
  return {
    ...actual,
    startCleaning: vi.fn(),
    stopCleaning: vi.fn(),
    restoreBlock: vi.fn(),
    deleteBlock: vi.fn(),
  };
});

import { deleteBlock, restoreBlock } from '../api/client';

const mockedDelete = vi.mocked(deleteBlock);
const mockedRestore = vi.mocked(restoreBlock);

/** 两个重复块，且都带内容（`content` 缺失时走的是另一条渲染路径） */
function makeNote(over: Partial<NoteDetail> = {}): NoteDetail {
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
    original_md_content: '# 原始',
    clean_md_content: '# 清洗后',
    error_message: null,
    created_at: '2026-09-01T10:00:00Z',
    updated_at: '2026-09-02T10:00:00Z',
    metadata_: {
      total_chunks: 20,
      duplicate_blocks: 2,
      duplicates_detail: [
        {
          block_index: 3,
          duplicate_of: 1,
          similarity: 0.95,
          content: '重复的块内容',
          original_content: '保留的块内容',
        },
        {
          block_index: 7,
          duplicate_of: 5,
          similarity: 0.88,
          content: '第二个重复块',
          original_content: '第二个保留块',
        },
      ],
    },
    ...over,
  } as NoteDetail;
}

function renderPanel(note: NoteDetail = makeNote()) {
  return render(
    // 面板里的两处确认走 `useConfirm()` / `ConfirmDialog`，渲染树里必须有这个宿主
    // （层级与 main.tsx 一致 —— 与 NoteDetail.test.tsx 的 renderPage 同款）
    <ConfirmProvider>
      <CleaningPanel note={note} onStatusChange={vi.fn()} />
    </ConfirmProvider>,
  );
}

/** 段级折叠按钮：可访问名是「重复块（N 个）」 */
function sectionToggle() {
  return screen.getByRole('button', { name: /重复块（\d+ 个）/ });
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('段级折叠（2026-10-19 新增）', () => {
  it('★ 默认展开：重复块列表一进来就看得见', () => {
    renderPanel();

    // 默认展开是有意的 —— 清洗完最该看到的就是"有几处重复"，
    // 一进来就是收起态等于把结果藏起来
    expect(sectionToggle()).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText(/块 3/)).toBeInTheDocument();
    expect(screen.getByText(/块 7/)).toBeInTheDocument();
  });

  it('★ 点一下收起：列表消失，但**标题按钮仍在**（否则没法再打开）', async () => {
    renderPanel();

    await userEvent.click(sectionToggle());

    expect(sectionToggle()).toHaveAttribute('aria-expanded', 'false');
    // 两个块的头部都消失
    expect(screen.queryByText(/块 3/)).not.toBeInTheDocument();
    expect(screen.queryByText(/块 7/)).not.toBeInTheDocument();
    // 但入口还在 —— 这一条是折叠功能最容易写错的地方
    expect(sectionToggle()).toBeInTheDocument();
  });

  it('★ 再点一下展开：列表回来（折叠必须可逆）', async () => {
    renderPanel();

    await userEvent.click(sectionToggle());
    await userEvent.click(sectionToggle());

    expect(sectionToggle()).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText(/块 3/)).toBeInTheDocument();
    expect(screen.getByText(/块 7/)).toBeInTheDocument();
  });

  it('★ 计数留在按钮里：收起之后仍然知道有几处重复', async () => {
    renderPanel();

    await userEvent.click(sectionToggle());

    // 收起后这一行不能只剩「重复块」三个字 ——
    // 那样用户得再展开一次才知道里面有多少
    expect(sectionToggle()).toHaveTextContent('重复块（2 个）');
  });

  it('★ 折叠按钮是 h2 里的真控件（WAI-ARIA 手风琴写法，键盘可达）', () => {
    renderPanel();

    // `getByRole('heading')` 拿到 h2，按钮在它**里面**
    const heading = screen.getByRole('heading', { name: /重复块/ });
    expect(heading.tagName).toBe('H2');
    expect(heading).toContainElement(sectionToggle());
  });

  it('aria-controls 指向被折叠的那个容器，且该容器真的存在', () => {
    renderPanel();

    const id = sectionToggle().getAttribute('aria-controls');
    expect(id).toBeTruthy();
    expect(document.getElementById(id as string)).toBeInTheDocument();
  });

  it('没有重复块时不渲染这一段（连折叠按钮都不该出现）', () => {
    renderPanel(
      makeNote({
        metadata_: { total_chunks: 20, duplicate_blocks: 0, duplicates_detail: [] },
      } as Partial<NoteDetail>),
    );

    expect(screen.queryByRole('button', { name: /重复块/ })).not.toBeInTheDocument();
    expect(screen.getByText('未检测到重复内容')).toBeInTheDocument();
  });
});

describe('块级折叠：与段级折叠互不干扰', () => {
  it('★ 段级收起再展开后，块级展开态保留（两层是两份独立 state）', async () => {
    renderPanel();

    // 先展开第 3 块的内容对比
    const viewButtons = screen.getAllByRole('button', { name: '查看内容' });
    await userEvent.click(viewButtons[0]);
    expect(screen.getByText('保留的块内容')).toBeInTheDocument();

    // 段级收起 → 再展开
    await userEvent.click(sectionToggle());
    await userEvent.click(sectionToggle());

    // 块级展开态**没有被重置** —— 两层折叠是独立的
    expect(screen.getByText('保留的块内容')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '收起内容' })).toBeInTheDocument();
  });

  it('内容缺失时给出"重新清洗"的说明，而不是空白', async () => {
    renderPanel(
      makeNote({
        metadata_: {
          total_chunks: 20,
          duplicate_blocks: 1,
          duplicates_detail: [{ block_index: 3, duplicate_of: 1, similarity: 0.9 }],
        },
      } as Partial<NoteDetail>),
    );

    await userEvent.click(screen.getByRole('button', { name: '查看内容' }));

    expect(screen.getByText(/未保存块内容/)).toBeInTheDocument();
  });
});

describe('折叠状态不影响数据操作', () => {
  it('收起状态下「恢复」按钮不可达（它在被折叠的列表里）—— 这是折叠的应有之义', async () => {
    renderPanel();

    await userEvent.click(sectionToggle());

    expect(screen.queryByRole('button', { name: '恢复' })).not.toBeInTheDocument();
    expect(mockedRestore).not.toHaveBeenCalled();
  });

  it('展开状态下点「恢复」仍然调接口（折叠没改坏原有行为）', async () => {
    mockedRestore.mockResolvedValue(undefined as never);
    renderPanel();

    await userEvent.click(screen.getAllByRole('button', { name: '恢复' })[0]);

    expect(mockedRestore).toHaveBeenCalledWith('note-1', 3);
  });

  it('★ 点「删除」只弹确认框、不调接口（折叠没绕过那道确认）', async () => {
    renderPanel();

    await userEvent.click(screen.getAllByRole('button', { name: /删除/ })[0]);

    expect(mockedDelete).not.toHaveBeenCalled();
    expect(await screen.findByText('确定删除块 3？')).toBeInTheDocument();
  });
});
