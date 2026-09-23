/**
 * @file 问题集页的三条核心改动（visual-refactor-plan 批次 E5）
 *
 * 这一页此前**没有测试文件**。本批借鉴表 §C3「题库 / 问题集」行只抄 Anki 三件，
 * 测试就逐件盯住"它真的发生了"，而不是"代码里有这行字"：
 *
 * | # | 改动 | 这一条用例怎么证明它 |
 * |---|---|---|
 * | 1 | 可配置列 + 点列头排序 | 首屏**不排序**（行序 = 接口顺序，列头 `aria-sort="none"`）；点「题目」列头后**行序真的变了**；`aria-sort` 跟着翻；换一列回到该列的初始方向；列头里的按钮能 `focus()` 且**敲回车**也能排序（`<th onClick>` 做不到这件事） |
 * | 2 | 行背景三态高亮 | 三行各带一个状态类（hard/medium/easy），且每行**都有那枚中文难度徽章** —— 底色只是冗余编码，色觉障碍用户靠文字读状态 |
 * | 3 | s / d / r 做成筛选维度 | ⚠️ 数据层没有 s/d/r（依据见 `QuestionSets.tsx` 的 `FILTER_DIMENSIONS` 注释）。所以这里钉的是**机制**：chip 是 `<button aria-pressed>`、点下去**行真的少了**、维度表是数据驱动的 |
 *
 * `styles` 的用法依据 `css-convention.md` §6：Vitest 下 `.module.css` 的默认导出
 * 是一个 Proxy（`styles.row` → `_row_xxxxxx`），用来 `querySelector` 没问题，
 * 但**不能枚举**它。`KnowledgeCards.test.tsx` 已有同样的先例。
 */
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { getQuestions, type QuizItem } from '../api/client';
import QuestionSets from './QuestionSets';
import pageStyles from './QuestionSets.module.css';

vi.mock('../api/client', () => ({ getQuestions: vi.fn() }));

const mockedQuestions = vi.mocked(getQuestions);

/**
 * 三条题目的取值**刻意两两错开**，而且**数组顺序刻意不是任何一列的排序结果**
 * （C / A / B），让"排序真的生效"有可观测的差异：
 *
 * | id | 题面 | 创建时间 | 题型 | 难度 |
 * |---|---|---|---|---|
 * | q-1 | C 题（最新） | 09-03 | 简答 | 困难 |
 * | q-3 | A 题（最旧） | 09-01 | 选择 | 简单 |
 * | q-2 | B 题 | 09-02 | 填空 | 中等 |
 *
 * ⇒ 首屏（不排序）是 C / A / B；按题面升序是 A / B / C；按创建时间降序是 C / B / A；
 * 按难度升序是 A / B / C。**四种顺序互不相同**，否则"点了列头顺序变了"这条断言
 * 会在实现没生效时也通过。
 *
 * 数组顺序之所以要"乱"，是因为页面的初始态是**不排序**（见 `QuestionSets.tsx`
 * 里 `SortState` 的长注释：客户端一上来就重排会打翻 `e2e/a11y.spec.ts` 的
 * `question-sets` 场景）。若桩按 created_at 降序给，就分不清"没排"和"排了"。
 *
 * 题面前缀用 ASCII 的 A/B/C：`localeCompare` 对汉字的次序依赖运行环境的 ICU
 * 排序规则（甲乙丙在码位上就不是升序），拿它写断言等于把测试绑在 locale 上。
 */
function makeQuestion(over: Partial<QuizItem> & Pick<QuizItem, 'id'>): QuizItem {
  return {
    user_id: 'user-1',
    card_id: 'card-1',
    note_id: 'note-1',
    note_title: '锂离子电池的浮充与均充',
    question_type: 'short_answer',
    difficulty: 'hard',
    question: 'C 题：硫化的成因是什么？',
    answer: '极板上生成不可逆硫酸铅',
    options: null,
    explanation: '硫化会让容量下降。',
    metadata_: null,
    created_at: '2026-09-03T00:00:00+00:00',
    updated_at: '2026-09-03T00:00:00+00:00',
    ...over,
  };
}

// ⚠️ 数组顺序 = C / A / B，**刻意不等于任何一列的排序结果**（理由见上方表格）
const QUIZ_ITEMS: QuizItem[] = [
  makeQuestion({ id: 'q-1' }),
  makeQuestion({
    id: 'q-3',
    question: 'A 题：浮充与均充的主要区别是什么？',
    question_type: 'choice',
    difficulty: 'easy',
    answer: '浮充长期恒压补偿自放电，均充短时升压校正',
    options: JSON.stringify(['浮充电压高于均充电压', '两者只是叫法不同']),
    created_at: '2026-09-01T00:00:00+00:00',
    updated_at: '2026-09-01T00:00:00+00:00',
  }),
  makeQuestion({
    id: 'q-2',
    question: 'B 题：均充的作用是什么？',
    question_type: 'fill_blank',
    difficulty: 'medium',
    answer: '均充',
    created_at: '2026-09-02T00:00:00+00:00',
    updated_at: '2026-09-02T00:00:00+00:00',
  }),
];

/** `page_size` / `page` 是响应契约的一部分；`total` 必须等于 `items.length`，
 *  否则 `fetchQuestions` 的翻页循环会一直请求到 100 页的硬上限。 */
function mockQuestions(items: QuizItem[] = QUIZ_ITEMS) {
  mockedQuestions.mockResolvedValue({ items, page: 1, page_size: 100, total: items.length });
}

function renderPage() {
  return render(
    <MemoryRouter>
      <QuestionSets />
    </MemoryRouter>,
  );
}

/** 表头行之外的数据行（`.row` 只挂在题目行上，分组头行挂的是 `.groupHeaderRow`） */
function dataRows(container: HTMLElement): HTMLElement[] {
  return [...container.querySelectorAll(`tr.${pageStyles.row}`)] as HTMLElement[];
}

/** 当前渲染顺序下的题面（按 DOM 顺序读「题目」格里的那一段） */
function shownQuestions(container: HTMLElement): string[] {
  return [...container.querySelectorAll(`.${pageStyles.questionText}`)].map((el) => el.textContent);
}

function columnHeader(name: RegExp): HTMLElement {
  return screen.getByRole('columnheader', { name });
}

describe('问题集：可配置列 + 点列头排序（批次 E5 · 核心改动 ①）', () => {
  beforeEach(() => {
    mockedQuestions.mockReset();
    mockQuestions();
  });

  it('★ 首屏**不排序**：行序 = 接口给的顺序，三个可排序列头都是 aria-sort=none', async () => {
    const { container } = renderPage();
    await screen.findByText('C 题：硫化的成因是什么？');

    // 桩的数组顺序是 C / A / B（既不是题面序也不是时间序）—— 页面原样呈现，
    // 与改动前 `filtered.map(...)` 的行为逐行一致（理由见 `SortState` 的长注释）
    expect(shownQuestions(container)).toEqual([
      'C 题：硫化的成因是什么？',
      'A 题：浮充与均充的主要区别是什么？',
      'B 题：均充的作用是什么？',
    ]);
    for (const name of [/题目/, /题型/, /难度/, /创建时间/]) {
      expect(columnHeader(name)).toHaveAttribute('aria-sort', 'none');
    }
    // 不可排序的「答案」列不写这个属性
    expect(columnHeader(/答案/)).not.toHaveAttribute('aria-sort');
  });

  it('★ 点「创建时间」列头：时间列的初始方向是降序（最新在前）', async () => {
    const user = userEvent.setup();
    const { container } = renderPage();
    await screen.findByText('C 题：硫化的成因是什么？');

    await user.click(within(columnHeader(/创建时间/)).getByRole('button'));

    expect(shownQuestions(container)).toEqual([
      'C 题：硫化的成因是什么？', // 09-03
      'B 题：均充的作用是什么？', // 09-02
      'A 题：浮充与均充的主要区别是什么？', // 09-01
    ]);
    expect(columnHeader(/创建时间/)).toHaveAttribute('aria-sort', 'descending');

    await user.click(within(columnHeader(/创建时间/)).getByRole('button'));
    expect(columnHeader(/创建时间/)).toHaveAttribute('aria-sort', 'ascending');
    expect(shownQuestions(container)).toEqual([
      'A 题：浮充与均充的主要区别是什么？',
      'B 题：均充的作用是什么？',
      'C 题：硫化的成因是什么？',
    ]);
  });

  it('★ 点「题目」列头后**行序真的变了**，再点一次翻回降序', async () => {
    const user = userEvent.setup();
    const { container } = renderPage();
    await screen.findByText('C 题：硫化的成因是什么？');

    await user.click(within(columnHeader(/题目/)).getByRole('button'));

    // 换列 ⇒ 用该列的初始方向（文本列 = 升序）
    expect(shownQuestions(container)).toEqual([
      'A 题：浮充与均充的主要区别是什么？',
      'B 题：均充的作用是什么？',
      'C 题：硫化的成因是什么？',
    ]);
    expect(columnHeader(/题目/)).toHaveAttribute('aria-sort', 'ascending');
    // 同一时刻只有一个列头带方向：没被排到的那一列退回 none
    expect(columnHeader(/创建时间/)).toHaveAttribute('aria-sort', 'none');

    await user.click(within(columnHeader(/题目/)).getByRole('button'));

    expect(shownQuestions(container)).toEqual([
      'C 题：硫化的成因是什么？',
      'B 题：均充的作用是什么？',
      'A 题：浮充与均充的主要区别是什么？',
    ]);
    expect(columnHeader(/题目/)).toHaveAttribute('aria-sort', 'descending');
  });

  it('按难度排序走的是语义次序（简单 → 中等 → 困难），不是字符串次序', async () => {
    const user = userEvent.setup();
    const { container } = renderPage();
    await screen.findByText('C 题：硫化的成因是什么？');

    await user.click(within(columnHeader(/难度/)).getByRole('button'));

    expect(shownQuestions(container)).toEqual([
      'A 题：浮充与均充的主要区别是什么？', // easy
      'B 题：均充的作用是什么？', // medium
      'C 题：硫化的成因是什么？', // hard
    ]);
  });

  it('★ 可排序的列头是**真按钮**：能聚焦、能被回车触发（`<th onClick>` 做不到）', async () => {
    const user = userEvent.setup();
    const { container } = renderPage();
    await screen.findByText('C 题：硫化的成因是什么？');

    const button = within(columnHeader(/题目/)).getByRole('button');
    expect(button.tagName).toBe('BUTTON');
    expect(button).toHaveAttribute('type', 'button');
    // 原生 button 默认就在顺序焦点链里（`tabindex="-1"` 会把它踢出去）
    expect(button.tabIndex).toBe(0);
    expect(button).not.toHaveAttribute('tabindex', '-1');

    button.focus();
    expect(button).toHaveFocus();

    const before = shownQuestions(container);
    await user.keyboard('{Enter}');
    expect(shownQuestions(container)).not.toEqual(before);
  });

  it('排序按钮的可访问名以可见文字开头，并报出当前方向（WCAG 2.5.3 + 验收要求）', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('C 题：硫化的成因是什么？');

    // 首屏不排序 ⇒ 每个可排序按钮都只说"点了会怎样"
    expect(within(columnHeader(/创建时间/)).getByRole('button')).toHaveAccessibleName(
      '创建时间，点击按此列排序',
    );
    expect(within(columnHeader(/题目/)).getByRole('button')).toHaveAccessibleName(
      '题目，点击按此列排序',
    );

    // 点过之后才报"当前是什么方向、下一次会变成什么"
    await user.click(within(columnHeader(/创建时间/)).getByRole('button'));
    expect(within(columnHeader(/创建时间/)).getByRole('button')).toHaveAccessibleName(
      '创建时间，当前降序，点击改为升序',
    );
    expect(within(columnHeader(/题目/)).getByRole('button')).toHaveAccessibleName(
      '题目，点击按此列排序',
    );

    await user.click(within(columnHeader(/题目/)).getByRole('button'));
    expect(within(columnHeader(/题目/)).getByRole('button')).toHaveAccessibleName(
      '题目，当前升序，点击改为降序',
    );
  });

  it('★ 列可配置：默认 5 列，「更新时间」关着；取消勾选「创建时间」后那一列消失', async () => {
    const user = userEvent.setup();
    const { container } = renderPage();
    await screen.findByText('C 题：硫化的成因是什么？');

    // 默认可见 = 题目 / 题型 / 难度 / 创建时间 / 答案（落在计划要求的"默认 4–6 列"）
    expect(screen.getAllByRole('columnheader').map((th) => th.textContent)).toEqual([
      '题目',
      '题型',
      '难度',
      '创建时间',
      '答案',
    ]);
    expect(screen.queryByRole('columnheader', { name: /更新时间/ })).toBeNull();

    const toggle = screen.getByRole('button', { name: /^列（/ });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(toggle).toHaveAccessibleName('列（5/6）');
    // 面板常驻 DOM（`hidden` 开合）：`aria-controls` 必须指向一个真的存在的节点
    expect(document.getElementById(toggle.getAttribute('aria-controls')!)).not.toBeNull();

    await user.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');

    await user.click(screen.getByRole('checkbox', { name: '创建时间' }));

    expect(screen.queryByRole('columnheader', { name: /创建时间/ })).toBeNull();
    expect(screen.getByRole('button', { name: /^列（/ })).toHaveAccessibleName('列（4/6）');
    // 数据行少了一格，但题目那一列还在 —— 隐藏的是列不是数据
    expect(within(dataRows(container)[0]).getByText('C 题：硫化的成因是什么？')).toBeVisible();

    // 「难度」列固定显示（它是行底色的文字载体），面板里没有它的开关
    expect(screen.queryByRole('checkbox', { name: '难度' })).toBeNull();
    expect(screen.queryByRole('checkbox', { name: '题目' })).toBeNull();
  });
});

describe('问题集：行背景三态高亮（批次 E5 · 核心改动 ②）', () => {
  beforeEach(() => {
    mockedQuestions.mockReset();
    mockQuestions();
  });

  it('★ 三行各带一个难度状态类，且每行都有那枚中文难度徽章（不能只靠底色）', async () => {
    const { container } = renderPage();
    await screen.findByText('C 题：硫化的成因是什么？');

    const rows = dataRows(container);
    expect(rows).toHaveLength(3);

    // 首屏不排序 ⇒ 行序 = 桩的数组顺序 C / A / B，即 困难 / 简单 / 中等
    expect(rows[0]).toHaveClass(pageStyles.rowHard);
    expect(rows[1]).toHaveClass(pageStyles.rowEasy);
    expect(rows[2]).toHaveClass(pageStyles.rowMedium);
    // 每一行**只**带自己那一档的状态类
    expect(rows[0]).not.toHaveClass(pageStyles.rowEasy);
    expect(rows[2]).not.toHaveClass(pageStyles.rowHard);

    // 底色是冗余编码：文字（徽章）才是色觉障碍用户读得到的载体
    expect(within(rows[0]).getByText('困难')).toBeVisible();
    expect(within(rows[1]).getByText('简单')).toBeVisible();
    expect(within(rows[2]).getByText('中等')).toBeVisible();
  });
});

describe('问题集：筛选 chip（批次 E5 · 核心改动 ③）', () => {
  beforeEach(() => {
    mockedQuestions.mockReset();
    mockQuestions();
  });

  it('★ chip 是真的筛选：点「选择」后只剩选择题那一行', async () => {
    const user = userEvent.setup();
    const { container } = renderPage();
    await screen.findByText('C 题：硫化的成因是什么？');
    expect(dataRows(container)).toHaveLength(3);

    await user.click(screen.getByRole('button', { name: '选择' }));

    expect(shownQuestions(container)).toEqual(['A 题：浮充与均充的主要区别是什么？']);
    expect(dataRows(container)).toHaveLength(1);
    expect(screen.getByText('共 3 道题，筛选后 1 道')).toBeVisible();

    // 回到「全部」再把行放出来。⚠️ 两个维度各有一个「全部」，
    // 所以按维度分组取，不能直接 `screen.getByRole('button', { name: '全部' })`
    const typeGroup = screen.getByRole('group', { name: '按题型筛选' });
    await user.click(within(typeGroup).getByRole('button', { name: '全部' }));
    expect(dataRows(container)).toHaveLength(3);
  });

  it('难度 chip 同样真的过滤，并且与题型是**与**关系', async () => {
    const user = userEvent.setup();
    const { container } = renderPage();
    await screen.findByText('C 题：硫化的成因是什么？');

    await user.click(screen.getByRole('button', { name: '简单' }));
    expect(shownQuestions(container)).toEqual(['A 题：浮充与均充的主要区别是什么？']);

    // 简单 ∧ 填空 = 空 ⇒ 走"筛选后无结果"的空态，而不是留一个空表
    await user.click(screen.getByRole('button', { name: '填空' }));
    expect(dataRows(container)).toHaveLength(0);
    expect(screen.getByText('没有符合筛选条件的题目')).toBeVisible();
  });

  it('★ chip 是 `<button aria-pressed>`，选中状态不靠颜色单独表达', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('C 题：硫化的成因是什么？');

    // 维度表是数据驱动的：今天登记了「题型」「难度」两维
    const typeGroup = screen.getByRole('group', { name: '按题型筛选' });
    const difficultyGroup = screen.getByRole('group', { name: '按难度筛选' });
    expect(within(typeGroup).getAllByRole('button')).toHaveLength(4);
    expect(within(difficultyGroup).getAllByRole('button')).toHaveLength(4);

    // 每个 chip 都必须带 aria-pressed（读屏用户靠它区分"选没选中"）
    for (const chip of [
      ...within(typeGroup).getAllByRole('button'),
      ...within(difficultyGroup).getAllByRole('button'),
    ]) {
      expect(chip.tagName).toBe('BUTTON');
      expect(chip).toHaveAttribute('aria-pressed');
    }

    expect(within(typeGroup).getByRole('button', { name: '全部' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(within(typeGroup).getByRole('button', { name: '选择' })).toHaveAttribute(
      'aria-pressed',
      'false',
    );

    await user.click(within(typeGroup).getByRole('button', { name: '选择' }));

    expect(within(typeGroup).getByRole('button', { name: '选择' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(within(typeGroup).getByRole('button', { name: '全部' })).toHaveAttribute(
      'aria-pressed',
      'false',
    );
  });

  it('页头计数在筛选生效时给出"筛选后 N 道"', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText('C 题：硫化的成因是什么？');

    expect(screen.getByText('共 3 道题')).toBeVisible();

    await user.click(screen.getByRole('button', { name: '选择' }));

    expect(screen.getByText('共 3 道题，筛选后 1 道')).toBeVisible();
  });
});

describe('问题集：答案仍然默认折叠（分组与遮挡语义不变）', () => {
  beforeEach(() => {
    mockedQuestions.mockReset();
    mockQuestions();
  });

  it('点「显示答案」才渲染答案 / 选项 / 解析，并带上 aria-expanded', async () => {
    const user = userEvent.setup();
    const { container } = renderPage();
    await screen.findByText('C 题：硫化的成因是什么？');

    expect(screen.queryByText(/答案：/)).toBeNull();

    const buttons = screen.getAllByRole('button', { name: '显示答案' });
    expect(buttons).toHaveLength(3);
    expect(buttons[0]).toHaveAttribute('aria-expanded', 'false');

    // ★ 回归守卫：**第一个**「显示答案」必须落在接口返回的**第一条**上。
    // `e2e/a11y.spec.ts` 的 question-sets 场景正是这么用的
    // （`getByRole('button', {name:'显示答案'}).first()` 之后断言 qi-1 的答案）。
    // 页面若默认就按某一列重排，`.first()` 会指到别的行上、断言会以"找不到那句话"
    // 的形式变红 —— 这是本批真的核出来过的坑，见 `QuestionSets.tsx` 里 `SortState`
    // 上方的长注释。这条用例把它钉住：**默认不排序，行序 = 接口顺序**。
    const rows = dataRows(container);
    expect(within(rows[0]).getByText('C 题：硫化的成因是什么？')).toBeVisible();
    expect(buttons[0]).toBe(within(rows[0]).getByRole('button', { name: '显示答案' }));

    // 第二行才是带选项的选择题（A 题）
    const choiceToggle = within(rows[1]).getByRole('button', { name: '显示答案' });
    await user.click(choiceToggle);

    expect(choiceToggle).toHaveAttribute('aria-expanded', 'true');
    expect(choiceToggle).toHaveAccessibleName('隐藏答案');
    // ⚠️ Testing Library 的 `getByText` 只看**直接文本子节点**（`<strong>答案：</strong>`
    // 里那三个字不算 `<p>` 的），所以标签与正文分开断言。
    // Playwright 那条 a11y 断言用的是 textContent 语义（"答案：…" 整串），两者不冲突。
    expect(screen.getByText('答案：')).toBeVisible();
    expect(screen.getByText('浮充长期恒压补偿自放电，均充短时升压校正')).toBeVisible();
    expect(screen.getByText('浮充电压高于均充电压')).toBeVisible();
    expect(screen.getByText('解析：')).toBeVisible();
    expect(screen.getByText('硫化会让容量下降。')).toBeVisible();
  });

  it('分组头仍是真控件（`aria-expanded` + 「查看笔记」），折叠后该组的题目行消失', async () => {
    const user = userEvent.setup();
    const { container } = renderPage();
    await screen.findByText('C 题：硫化的成因是什么？');

    const groupToggle = screen.getByRole('button', { name: '锂离子电池的浮充与均充' });
    expect(groupToggle).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('(3 道题)')).toBeVisible();
    expect(screen.getByRole('button', { name: '查看笔记' })).toBeVisible();

    await user.click(groupToggle);

    expect(groupToggle).toHaveAttribute('aria-expanded', 'false');
    expect(dataRows(container)).toHaveLength(0);
  });
});
