/**
 * @file 学习目标页：**进度条的语义 + 达成态**测试（visual-refactor-plan 批次 E8）
 *
 * ## 本批在这一页做的两件事，各有一条断言
 *
 * ① **右侧百分比**：原来是一行"学习进度 …… 42%"浮在进度条上方，`42%` 只是一个
 *    纯装饰的 `<span>` —— 读屏读到的是两句互不相干的文本。现在数字落在条的右侧，
 *    进度本身是 `role="progressbar"` + `aria-valuenow`，`aria-label` 给它名字
 *    （缺名的 progressbar 会被 axe 的 `aria-progressbar-name` 判违规）。
 *    jsdom 不算布局，所以"在右侧"用 **DOM 顺序**（条的 `nextElementSibling`）钉住 ——
 *    LTR 的 flex 行里，后一个兄弟就是右边那个。
 *
 * ② **达成态加金色描边**（§C2 第 11 行）：`progress >= target_mastery` 时卡片多一个
 *    模块类画金环。这条只能按类名断言（描边是纯样式），所以 `import styles`
 *    取模块导出的真实类名（`docs/css-convention.md` §6 允许的第二种做法）。
 *    同时钉住 `target_mastery === 0` 的边界：后端的校验允许目标是 0%
 *    （创建表单是 0-100），而 `progress >= 0` 恒真 —— 不守卫的话，
 *    "目标 0%"的卡片一建出来就自带金框。
 */
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { LearningGoal } from '../api/client';
import LearningGoals from './LearningGoals';
// 达成态的类名（哈希后只在模块里对得上）—— 见文件头
import styles from './LearningGoals.module.css';

vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client');
  return {
    ...actual,
    getGoals: vi.fn(),
    createGoal: vi.fn(),
    archiveGoal: vi.fn(),
    deleteGoal: vi.fn(),
  };
});

import { getGoals } from '../api/client';

function makeGoal(over: Partial<LearningGoal> = {}): LearningGoal {
  return {
    id: 'goal-1',
    user_id: 'u-1',
    name: '掌握蓄电池基础概念',
    type: 'daily',
    scope_notes: ['note-1'],
    scope_folders: [],
    target_mastery: 80,
    deadline: '2026-02-01T00:00:00+00:00',
    status: 'active',
    progress_cache: 0.42,
    last_progress_refresh: '2026-01-02T00:00:00+00:00',
    progress_percentage: 42,
    created_at: '2026-01-01T00:00:00+00:00',
    updated_at: '2026-01-02T00:00:00+00:00',
    ...over,
  };
}

/** 进行中的三个目标：未达成 / 已达成 / 目标 0%（边界） */
const IN_PROGRESS = [
  makeGoal(),
  makeGoal({
    id: 'goal-2',
    name: '读完《蓄电池维护手册》',
    target_mastery: 60,
    progress_percentage: 100,
  }),
  makeGoal({ id: 'goal-3', name: '目标为 0% 的边界', target_mastery: 0, progress_percentage: 0 }),
];

beforeEach(() => {
  vi.mocked(getGoals).mockReset();
  // `status` 在契约里是**可选**的（`getGoals(status?: string)`），桩的参数必须同样可选 ——
  // 写成必填会让 `tsc`（= `npm run build` 的第一步）报"不能赋给带可选参数的函数类型"，
  // 而 vitest 不做类型检查、看不见这一条。
  vi.mocked(getGoals).mockImplementation((status?: string) =>
    Promise.resolve({ goals: status === 'active' ? IN_PROGRESS : [], total: IN_PROGRESS.length }),
  );
});

function renderPage() {
  return render(
    <MemoryRouter>
      <LearningGoals />
    </MemoryRouter>,
  );
}

describe('学习目标 · 进度条与达成态（批次 E8）', () => {
  it('★ 进度是 role=progressbar（带可访问名与 aria-valuenow），百分比紧跟在条的右侧', async () => {
    renderPage();

    // 三张卡各一条进度条（可访问名都是「学习进度」，所以按 role 取全部）
    const bars = await screen.findAllByRole('progressbar', { name: '学习进度' });
    expect(bars).toHaveLength(3);

    const first = bars[0];
    expect(first).toHaveAttribute('aria-valuemin', '0');
    expect(first).toHaveAttribute('aria-valuemax', '100');
    expect(first).toHaveAttribute('aria-valuenow', '42');
    // 名字不是摆设：`aria-label` 是 progressbar 必需的那一项
    expect(first).toHaveAccessibleName('学习进度');

    // 「右侧百分比」= DOM 上的下一个兄弟（LTR 的 flex 行里那就是右边）
    expect(first.nextElementSibling).toHaveTextContent('42%');
  });

  it('★ 达成态（progress ≥ target）加金色描边；target 为 0 时不误判', async () => {
    renderPage();

    const achieved = (
      await screen.findByRole('heading', { name: '读完《蓄电池维护手册》' })
    ).closest('article');
    const notAchieved = screen
      .getByRole('heading', { name: '掌握蓄电池基础概念' })
      .closest('article');
    const zeroTarget = screen.getByRole('heading', { name: '目标为 0% 的边界' }).closest('article');

    expect(achieved).toHaveClass(styles.goalCardAchieved);
    expect(notAchieved).not.toHaveClass(styles.goalCardAchieved);
    // `progress >= target_mastery` 在 target=0 时恒真 —— 这条守卫不能少
    expect(zeroTarget).not.toHaveClass(styles.goalCardAchieved);
  });
});
