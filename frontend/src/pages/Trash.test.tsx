/**
 * @file 回收站页的**结构语义**测试（visual-refactor-plan 批次 E8）
 *
 * ## 为什么这一页值得一条测试
 *
 * 本批把它从"一条笔记一张 `.card`"改成**真 `<table>`**（计划 §6 E8 行 +
 * `docs/visual-symbol-research.md` §C3「回收站」行）。表格化的收益**全部**在语义上：
 * 读屏用户靠 `table` / `row` / `columnheader` 三个角色才能得到"第 3 行第 2 列是
 * 删除时间"这类信息 —— 而这恰好是最容易被"顺手优化"掉的东西：
 * 窄屏要竖排？`display: grid` 一写，三个角色就**一起消失**，页面看上去一模一样，
 * axe 也报不出来（它看到的是一个没有角色的普通容器，不是违规）。
 *
 * 所以这条测试钉的是：
 *   ① 真的存在 `table` 与四个 `columnheader`（列名逐个点名）；
 *   ② 笔记标题仍然是 `heading`（`e2e/a11y.spec.ts` 的 `trash` 场景按
 *      `getByRole('heading', { name: '已删除：…' })` 断言它，改成 `<th>` 就没了
 *      —— `<th>` 的角色是 `rowheader`/`columnheader`，不是 `heading`）；
 *   ③ 危险色的**两级**：入口描边（`.btn-danger-outline`）、确认框里实底
 *      （`.btn-danger`）。这一条只能按类名断言 —— "描边还是实底"是纯样式，
 *      没有任何语义出口（角色、可访问名、文本都一样）。
 */
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { TrashNoteItem } from '../api/client';
import Trash from './Trash';

// ── mock 掉整条 API 层：本文件测的是页面结构，不是接口契约 ──
vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client');
  return {
    ...actual,
    getTrashedNotes: vi.fn(),
    restoreNote: vi.fn(),
    purgeNote: vi.fn(),
    purgeAllTrash: vi.fn(),
  };
});

import { getTrashedNotes } from '../api/client';

/** 一条已删除的笔记（含五项附属统计 —— 它们是"恢复能还原什么"的展示依据） */
const TRASHED: TrashNoteItem = {
  note: {
    id: 'note-trashed',
    user_id: 'u-1',
    title: '已删除：蓄电池寿命与温度的关系',
    source_type: 'docx',
    status: 'archived',
    file_size: 2048,
    page_count: null,
    error_message: null,
    trashed_at: '2026-01-02T03:04:05+00:00',
    created_at: '2026-01-01T00:00:00+00:00',
    updated_at: '2026-01-02T03:04:05+00:00',
    note_role: 'material',
    project_ids: [],
    project_names: [],
  },
  card_count: 4,
  quiz_count: 3,
  annotation_count: 2,
  version_count: 5,
  link_count: 1,
};

beforeEach(() => {
  vi.mocked(getTrashedNotes).mockReset();
  vi.mocked(getTrashedNotes).mockResolvedValue({ items: [TRASHED], total: 1 });
});

describe('回收站 · 表格化与危险按钮的两级（批次 E8）', () => {
  it('★ 一行一条笔记，列语义由真 <table> + <th scope="col"> 承担', async () => {
    render(<Trash />);

    // ① 真表格：三个角色齐在（`display: grid` 会让它们一起消失）
    const table = await screen.findByRole('table');
    expect(
      screen.getAllByRole('columnheader').map((th) => th.textContent),
      '列名就是给读屏用的"这一列是什么"，改名等于改语义',
    ).toEqual(['笔记', '来源与删除时间', '包含内容', '操作']);

    // ② 笔记标题仍然是 heading（不是 rowheader）—— e2e 的 trash 场景按 heading 找它
    expect(
      within(table).getByRole('heading', { name: '已删除：蓄电池寿命与温度的关系' }),
    ).toBeInTheDocument();

    // ③ 行内的两件事：五项统计全在（`getByText('4 张卡片')` 是 e2e 的场景标记），
    //    以及"恢复 / 彻底删除"两个动作
    expect(within(table).getByText('4 张卡片')).toBeInTheDocument();
    expect(within(table).getByText('删除于', { exact: false })).toBeInTheDocument();
    expect(within(table).getByRole('button', { name: '恢复' })).toBeInTheDocument();
    expect(within(table).getByRole('button', { name: '彻底删除' })).toBeInTheDocument();
  });

  it('★ 红色分两级：入口是描边（btn-danger-outline），确认框里才是实底（btn-danger）', async () => {
    const user = userEvent.setup();
    render(<Trash />);

    // 页头的「清空回收站」：不可恢复，但它打开的是确认框 —— 用描边
    const clearEntry = await screen.findByRole('button', { name: '清空回收站' });
    expect(clearEntry).toHaveClass('btn-danger-outline');
    expect(clearEntry).not.toHaveClass('btn-danger');

    // 行内的「彻底删除」同理（它也只是打开 PurgeNoteDialog）
    expect(screen.getByRole('button', { name: '彻底删除' })).toHaveClass('btn-danger-outline');

    // 打开确认框：**这一下**才是不可恢复的动作，用实底
    await user.click(clearEntry);
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByRole('button', { name: '清空' })).toHaveClass('btn-danger');
    expect(within(dialog).getByRole('button', { name: '清空' })).not.toHaveClass(
      'btn-danger-outline',
    );
  });
});
