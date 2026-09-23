/**
 * @file 问题集列表页面（visual-refactor-plan 批次 E5「题库 / 问题集」）
 * @description 展示当前用户所有问答题，按所属笔记分组，每组可折叠/展开。
 *
 * ## 本批抄了 Anki 三件里的哪几件
 *
 * 依据：`docs/visual-symbol-research.md` §C3「题库 / 问题集」行
 * （"只抄三件：可配置列 + 点列头排序（默认 4–6 列）、行背景三态高亮、
 * 把 FSRS 的 s/d/r 做成筛选维度；筛选用可点击 chip"）＋ 计划 §6 的 E5 行。
 *
 * | # | 计划原话 | 落在哪 | 说明 |
 * |---|---|---|---|
 * | 1 | 可配置列 + 点列头排序（默认 4–6 列） | `COLUMNS` + `visibleColumns` + `sort` + `<th aria-sort>` 里的排序 `<button>` | 默认显示 5 列（题目 / 题型 / 难度 / 创建时间 / 答案），「更新时间」默认关掉；**首屏不排序**（行序 = 接口顺序，理由见 `SortState` 的长注释），排序从第一次点列头开始 |
 * | 2 | 行背景三态高亮 | `ROW_STATE_CLASS` + `QuestionSets.module.css` 的 `.rowEasy/.rowMedium/.rowHard` | 三态 = **题目难度三档**（见下方"三态为什么是难度"） |
 * | 3 | 把 s / d / r 做成筛选维度 | ⚠️ **数据层没有这三个量，本批不臆造** —— 见 `FILTER_DIMENSIONS` 上方的长注释 | chip 机制（可点击 + `aria-pressed`）已经做成数据驱动的，后端一暴露就能补 |
 *
 * **不抄的**（研究文档明确点名的）：Anki 的搜索语法（`deck:X (is:due or tag:Y)`）、
 * 14 列全量信息密度、"Cards / Notes 双模式"。
 *
 * ## 为什么整页是一张表 + 每组一个 `<tbody>`（而不是每组一张表）
 *
 * `visual-design-spec.md` §6.3 明确"手风琴分组（`QuestionSets`）**保留**"，
 * 而本批又必须给出"点列头排序"。两者同时成立的最省做法是
 * **一个 `<table>`、每组一个 `<tbody>`**：列头只有一份 ⇒ 键盘用户 Tab 过
 * 5 个排序按钮，而不是 `5 × 笔记数` 个。每组一张表会把 Tab 停靠点按组数放大，
 * 那是拿可访问性换排版方便。
 *
 * ## 三态为什么是难度
 *
 * Anki 的三态是「旗标色 / 黄=暂缓 / 紫=已标记」。这三样在本项目**都不存在**：
 * `quiz_items`（`backend/app/models/quiz_item.py`）没有旗标 / 暂缓 / 标记字段，
 * 页面上也没有设置它们的入口 —— 照搬只会做出三个永远不出现的状态。
 * 于是三态落在数据里**唯一真实的三值维度**上：题目难度（easy / medium / hard）。
 *
 * ⚠️ 由此有一条硬约束：**「难度」列不可隐藏**（`COLUMNS` 里 `hideable: false`）。
 * 那一格里的中文徽章是行底色的**文字载体** —— 底色一旦成为唯一线索，
 * 色觉障碍用户就读不出行状态（本批验收要求原话："行三态高亮不能只靠背景色"）。
 */
import { Fragment, useEffect, useState, useRef, useCallback } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { getQuestions, type QuizItem } from '../api/client';
import Icon from '../components/Icon';
import LoadingSpinner from '../components/LoadingSpinner';
import EmptyState from '../components/EmptyState';
import ErrorDisplay from '../components/ErrorDisplay';
// 页面标题（visual-refactor-plan 批次 C1）：字号本就 1.5rem，观感不变；
// 页头那一行（标题 + 题量计数）交给组件，窄屏换行随之进模块
import PageHeader from '../components/PageHeader';
import {
  questionTypeLabels,
  questionTypeColors,
  difficultyLabels,
  difficultyColors,
  FALLBACK_CATEGORY_COLOR,
} from '../utils/labels';
// 本页自己的外观（批次 E5）：表格 / 行三态 / 列头排序 / 「列」面板。
// 从模块取类名是因为它们只被这一个文件写出来（`css-convention.md` §4 判据 2）。
import styles from './QuestionSets.module.css';

/**
 * 解析选择题的选项（`options` 在契约里是 `string | null` 且**可缺省**，
 * 见 `QuizItemResponse.options?: string | null` —— 三种空都当作"没有选项"）。
 */
function parseOptions(optionsStr: string | null | undefined): string[] {
  if (!optionsStr) return [];
  try {
    const parsed = JSON.parse(optionsStr);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

interface NoteGroup {
  note_id: string;
  note_title: string;
  questions: QuizItem[];
}

/** 将题目按所属笔记分组（纯函数，模块级便于复用与测试） */
function groupByNote(questions: QuizItem[]): NoteGroup[] {
  const map = new Map<string, QuizItem[]>();
  for (const q of questions) {
    const list = map.get(q.note_id) || [];
    list.push(q);
    map.set(q.note_id, list);
  }
  return Array.from(map.entries())
    .map(([noteId, questions]) => ({
      note_id: noteId,
      note_title: questions[0].note_title || '未命名笔记',
      questions,
    }))
    .sort((a, b) => {
      const aTime = a.questions[0]?.created_at ?? '';
      const bTime = b.questions[0]?.created_at ?? '';
      return bTime.localeCompare(aTime);
    });
}

/** 集合里加/删一个元素的纯函数（三个折叠/勾选状态共用一份） */
function toggleInSet<T>(set: Set<T>, value: T): Set<T> {
  const next = new Set(set);
  if (next.has(value)) {
    next.delete(value);
  } else {
    next.add(value);
  }
  return next;
}

// ═══════════════════════════════════════════════════════════════════════════
// 核心改动 ①：可配置列 + 点列头排序
// ═══════════════════════════════════════════════════════════════════════════

/** 可排序的列（= 排序键）。「答案」是操作列，没有可排的序，不在其中。 */
type SortKey = 'question' | 'question_type' | 'difficulty' | 'created_at' | 'updated_at';

/** 全部列 id */
type ColumnId = SortKey | 'detail';

/**
 * 列定义 —— **列清单的唯一数据源**。
 *
 * 可辨识联合（`sortable: true` 与 `sortable: false` 两支）：渲染层拿
 * `col.sortable` 一判，`col.id` 就窄化成 `SortKey`，排序函数收不到 `'detail'`。
 *
 * `hideable` / `defaultVisible` 是两件事：
 * - `hideable: false`（题目、难度）—— 见文件头"三态为什么是难度"；
 *   题目列不可隐藏是因为它就是这一行的身份，没有它整张表只剩徽章。
 * - 默认显示 5 列，落在计划要求的"默认 4–6 列"区间里。
 */
type ColumnDef =
  | { id: SortKey; label: string; sortable: true; hideable: boolean; defaultVisible: boolean }
  | { id: 'detail'; label: string; sortable: false; hideable: boolean; defaultVisible: boolean };

const COLUMNS: ColumnDef[] = [
  { id: 'question', label: '题目', sortable: true, hideable: false, defaultVisible: true },
  { id: 'question_type', label: '题型', sortable: true, hideable: true, defaultVisible: true },
  { id: 'difficulty', label: '难度', sortable: true, hideable: false, defaultVisible: true },
  { id: 'created_at', label: '创建时间', sortable: true, hideable: true, defaultVisible: true },
  // 默认关着：它是"列真的可配置"的证据，也避免默认视图塞进 6 列
  { id: 'updated_at', label: '更新时间', sortable: true, hideable: true, defaultVisible: false },
  { id: 'detail', label: '答案', sortable: false, hideable: true, defaultVisible: true },
];

type SortDirection = 'asc' | 'desc';

/**
 * 排序状态。`null` = **客户端没有排序**，行序就是接口给它的顺序。
 *
 * ## 为什么默认是 `null`，而不是"按创建时间降序"（接口自己就是那样排的）
 *
 * 本批执行前逐条核过 `e2e/a11y.spec.ts` 的 `question-sets` 场景（1883-1920 行）：
 * 它点 `getByRole('button', { name: '显示答案' }).first()`，然后断言
 * `答案：浮充长期恒压补偿自放电，均充短时升压校正` 可见 —— 那句话是**第二条**桩数据
 * （`qi-1`）的答案。
 *
 * 而桩（`e2e/a11y-fixtures.ts` 的 `QUIZ_ITEMS`，T0 = 01-05 / T1 = 01-06）是**按数组
 * 原序返回**的、并不重排（真实后端才有 `order_by(created_at desc)`，
 * 见 `backend/app/api/understanding.py:835`）。改动前这一页不做任何客户端排序，
 * 于是 `.first()` 命中 `qi-1`。**客户端一上来就按 created_at 降序重排，`.first()`
 * 会指到 `qi-2`（答案是"均充"）** —— 那条既有断言会变成"找不到那句话"而变红，
 * 且失败信息指向的是答案文字，不是排序，极难归因。
 *
 * 默认不排序同时让"首屏行序与改动前逐行一致"这件事**不依赖桩怎么构造**：
 * 换个后端、换个桩顺序，行序照样跟着接口走。排序从**第一次点列头**开始生效。
 */
type SortState = { key: SortKey; direction: SortDirection } | null;

/**
 * **非空**的排序状态。
 *
 * 比较器只在"确实要排序"时才被调用（`sortQuestions` 在 `null` 时原样返回），
 * 所以它收的是这个类型而不是 `SortState` —— 否则函数体里每取一次 `sort.key`
 * 都要先窄化一遍。调用点那一步 `const active = sort` 已经把 `null` 排除掉了。
 *
 * ⚠️ 这个别名是补出来的：`npm run build`（`tsc`）报了两处
 * `TS18047: 'sort' is possibly 'null'`。**单测与 lint 都看不见它** ——
 * `vitest` 走 esbuild 只剥类型不做检查，`eslint` 也不做收窄分析。
 * 也就是说"改了前端要跑 build"这条门禁在这里是真的兜住了东西。
 */
type ActiveSort = NonNullable<SortState>;

/** 初始排序状态：不排序（理由见 `SortState` 的长注释） */
const INITIAL_SORT: SortState = null;

/** 换一列时的初始方向：时间列"最新在前"，其余升序（显式查表，写错 tsc 报错） */
const INITIAL_DIRECTION: Record<SortKey, SortDirection> = {
  question: 'asc',
  question_type: 'asc',
  difficulty: 'asc',
  created_at: 'desc',
  updated_at: 'desc',
};

/** 排序方向 → `aria-sort` 的取值（读屏靠它播报"升序还是降序"） */
const ARIA_SORT: Record<SortDirection, 'ascending' | 'descending'> = {
  asc: 'ascending',
  desc: 'descending',
};

/** 排序指示箭头的模块类（`chevron` 复用；`idle` = 该列没被选中） */
const SORT_ICON_CLASS: Record<'idle' | SortDirection, string> = {
  idle: styles.sortIcon,
  asc: `${styles.sortIcon} ${styles.sortIconActiveAsc}`,
  desc: `${styles.sortIcon} ${styles.sortIconActive}`,
};

/** 题型/难度的枚举次序（排序用）。未知值排到最后，不参与"谁大谁小"的猜测。 */
const QUESTION_TYPE_RANK: Record<string, number> = {
  choice: 0,
  fill_blank: 1,
  short_answer: 2,
};

const DIFFICULTY_RANK: Record<string, number> = {
  easy: 0,
  medium: 1,
  hard: 2,
};

const UNKNOWN_RANK = Number.MAX_SAFE_INTEGER;

function rankOf(ranks: Record<string, number>, value: string): number {
  return ranks[value] ?? UNKNOWN_RANK;
}

/** ISO 时间串 → 毫秒；解析不出来当 0（这一列仍然可排，只是排在一起） */
function timeOf(value: string): number {
  const ts = Date.parse(value);
  return Number.isNaN(ts) ? 0 : ts;
}

/**
 * 题目比较器。
 *
 * ⚠️ 平局用 `id` 兜底（**升序、不随排序方向翻转**）：`Array.prototype.sort`
 * 虽然稳定，但"稳定"只承诺不改动**输入顺序**，而输入来自分页循环
 * （见下面 `fetchQuestions` 的说明）—— 跨页的先后本身没有承诺。
 * 没有这层兜底，"点两次列头回到原顺序"这句话就不成立。
 */
function compareQuestions(a: QuizItem, b: QuizItem, sort: ActiveSort): number {
  const direction = sort.direction === 'asc' ? 1 : -1;
  let diff = 0;
  switch (sort.key) {
    case 'question':
      diff = a.question.localeCompare(b.question);
      break;
    case 'question_type':
      diff =
        rankOf(QUESTION_TYPE_RANK, a.question_type) - rankOf(QUESTION_TYPE_RANK, b.question_type);
      break;
    case 'difficulty':
      diff = rankOf(DIFFICULTY_RANK, a.difficulty) - rankOf(DIFFICULTY_RANK, b.difficulty);
      break;
    case 'updated_at':
      diff = timeOf(a.updated_at) - timeOf(b.updated_at);
      break;
    case 'created_at':
      diff = timeOf(a.created_at) - timeOf(b.created_at);
      break;
  }
  if (diff === 0) return a.id.localeCompare(b.id);
  return diff * direction;
}

/** 排序后的副本（不改动 `groups` 里的原数组）；`sort === null` 时**原样返回** */
function sortQuestions(questions: QuizItem[], sort: SortState): QuizItem[] {
  if (sort === null) return questions;
  // 先落成 const 再进闭包：TS 不会把"参数非空"的窄化带进回调
  const active = sort;
  return [...questions].sort((a, b) => compareQuestions(a, b, active));
}

/** 排序按钮的箭头状态（`aria-sort` 负责含义，箭头只是外观） */
function sortIconState(key: SortKey, sort: SortState): 'idle' | SortDirection {
  if (sort === null || sort.key !== key) return 'idle';
  return sort.direction;
}

/**
 * 列头当前的 `aria-sort`。
 *
 * 不可排序的列返回 `undefined` ⇒ React 不写这个属性（"答案"是操作列）；
 * 可排序但当前没排到它 ⇒ `'none'`（ARIA 的语义就是"这一列不是排序依据"，
 * 不是"这一列不能排"）。
 */
function ariaSortOf(
  sortKey: SortKey | null,
  sort: SortState,
): 'ascending' | 'descending' | 'none' | undefined {
  if (sortKey === null) return undefined;
  if (sort === null || sort.key !== sortKey) return 'none';
  return ARIA_SORT[sort.direction];
}

/**
 * 排序按钮的 `aria-label`。
 *
 * 要求原话是"要能听到**当前按哪一列排序、升序还是降序**"。`<th aria-sort>`
 * 是标准机制，但用户 Tab 到的是里面的 `<button>`，浏览器不保证这时会念
 * 外层 `th` 的属性，所以把状态再写进按钮的可访问名里。
 *
 * ⚠️ 名字必须**以可见文字开头**（WCAG 2.5.3 Label in Name：语音输入的
 * 用户念的是屏幕上那几个字）。所以是 `${label}，当前升序…` 而不是反过来。
 */
function sortButtonLabel(key: SortKey, label: string, sort: SortState): string {
  if (sort === null || sort.key !== key) return `${label}，点击按此列排序`;
  return sort.direction === 'asc'
    ? `${label}，当前升序，点击改为降序`
    : `${label}，当前降序，点击改为升序`;
}

// ═══════════════════════════════════════════════════════════════════════════
// 核心改动 ②：行背景三态高亮
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 难度 → 行状态类（**显式查表**：动态拼类名在 CSS Modules 下必然静默失效）。
 *
 * 底色一律用 `base.css` 已有的 `--color-*-light`（10% 叠白），
 * 与同一行里那枚难度徽章同色系 —— 底色是**冗余编码**，徽章才是文字载体。
 */
const ROW_STATE_CLASS: Record<string, string> = {
  easy: styles.rowEasy,
  medium: styles.rowMedium,
  hard: styles.rowHard,
};

// ═══════════════════════════════════════════════════════════════════════════
// 核心改动 ③：筛选维度（chip）
// ═══════════════════════════════════════════════════════════════════════════

type FilterId = 'question_type' | 'difficulty';

type FilterState = Record<FilterId, string>;

const DEFAULT_FILTERS: FilterState = { question_type: 'all', difficulty: 'all' };

interface FilterDimension {
  id: FilterId;
  label: string;
  options: { value: string; label: string }[];
  /** 取这一维度在题目上的值（`'all'` 由 `filters[id] === 'all'` 单独判定） */
  valueOf: (item: QuizItem) => string;
}

const ALL_OPTION = { value: 'all', label: '全部' };

/**
 * 筛选维度表 —— **渲染层是数据驱动的**：一个 `options` 列表 + 一个 `valueOf`，
 * chip 的渲染、`aria-pressed`、过滤逻辑都只读这张表。
 *
 * ## ⚠️ 计划里的第三条（s / d / r）为什么**不在**这张表里
 *
 * 逐层核过（2026-09-24），三个量在**这一页的数据链路上一个都没有**：
 *
 * | 想抄的东西 | 实测 |
 * |---|---|
 * | 前端类型 `QuizItem` | `api/qa.ts:28` = `Schema<'QuizItemResponse'>`；字段表见 `api/generated/schema.ts:5858-5896`，只有 `question_type` / `difficulty`（`DifficultyLevel`：easy/medium/hard）/ `created_at` / `updated_at` / `note_id` / `note_title` / `card_id` / `answer` / `explanation` / `options` / `metadata_` |
 * | 后端响应模型 | `backend/app/schemas/knowledge.py:60-77` 同样只有这些字段；`GET /api/understanding/questions` 是 `QuizItemResponse.model_validate(quiz)` 直出（`backend/app/api/understanding.py:806-854`） |
 * | FSRS 的 s / d | 确实存在，但在**另一张表**上：`review_states.stability` / `review_states.difficulty`（`backend/app/models/review_state.py:119-120`），键是**卡片**不是题目，且没有任何题目列表响应暴露它 |
 * | FSRS 的 r（可提取性） | 服务端按 S/D 实时算的量；全前端 grep `retrievability` 只有 1 处注释（`api/review.ts:235`），**没有任何响应字段** |
 * | 题目自己的调度列 | `quiz_items` 有 `interval` / `repetition` / `easiness_factor` / `next_review_at` / `last_reviewed_at` / `review_count`（`backend/app/models/quiz_item.py:107-123`），但**全都不在** `QuizItemResponse` 里 ⇒ 前端拿不到 |
 *
 * 所以本批**不臆造**这三个维度：编一个"记忆强度 ≥ 3 天"的筛选项、底下接的是
 * 题目难度或空数组，比不做更糟 —— 用户会以为自己筛的是记忆状态。
 *
 * **处置**：机制做真、维度做假不得。后端一旦在 `QuizItemResponse` 上暴露
 * s / d / r（或本页改用一个带调度状态的列表接口），往下面这张表里补三项即可，
 * 渲染层（chip + `aria-pressed` + `filterQuestions`）一行都不用动。
 *
 * ⚠️ 另一条容易混的：本表里的 `difficulty` 是**题目本身的难度**（出题时由模型
 * 给的三档），**不是** FSRS 的 D（1–10 浮点，存在 `review_states` 上）。
 * 两者同名不同物，不能互相顶替 —— 这也是"d 已经有了"这句话不成立的原因。
 */
const FILTER_DIMENSIONS: FilterDimension[] = [
  {
    id: 'question_type',
    label: '题型',
    options: [
      ALL_OPTION,
      { value: 'choice', label: '选择' },
      { value: 'fill_blank', label: '填空' },
      { value: 'short_answer', label: '简答' },
    ],
    valueOf: (q) => q.question_type,
  },
  {
    id: 'difficulty',
    label: '难度',
    options: [
      ALL_OPTION,
      { value: 'easy', label: '简单' },
      { value: 'medium', label: '中等' },
      { value: 'hard', label: '困难' },
    ],
    valueOf: (q) => q.difficulty,
  },
];

/** 「列」面板的 id —— `aria-controls` 要指向一个**真的存在**的元素（见下方 `hidden`） */
const COLUMN_PANEL_ID = 'questions-column-panel';

/** 时间列的显示格式（手写而不是 `toLocaleString`：不受运行环境 ICU / 时区数据影响） */
function formatTime(value: string): string {
  const ts = Date.parse(value);
  if (Number.isNaN(ts)) return '—';
  const d = new Date(ts);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export default function QuestionSets() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [groups, setGroups] = useState<NoteGroup[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [expandedNotes, setExpandedNotes] = useState<Set<string>>(new Set());
  const [searchKeyword, setSearchKeyword] = useState('');
  // ── 批次 E5 新增的四个状态 ──
  const [filters, setFilters] = useState<FilterState>(DEFAULT_FILTERS);
  const [sort, setSort] = useState<SortState>(INITIAL_SORT);
  const [visibleColumns, setVisibleColumns] = useState<Set<ColumnId>>(
    () => new Set(COLUMNS.filter((col) => col.defaultVisible).map((col) => col.id)),
  );
  const [columnsOpen, setColumnsOpen] = useState(false);
  // 答案展开态从「每张卡片自己一个 useState」提到页面级：展开后的答案是
  // 表格里**独立的一行**（`<tr>` 不能藏在另一行里面），它必须由页面决定渲染与否。
  const [expandedAnswers, setExpandedAnswers] = useState<Set<string>>(new Set());
  const searchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const noteId = searchParams.get('note_id') || undefined;

  const fetchQuestions = useCallback(
    async (keyword?: string) => {
      setLoading(true);
      try {
        // 后端 page_size 上限为 100，需分页加载全部题目。
        //
        // 终止条件必须有多重兜底（见 docs/overhaul-plan.md §2.8 F-3）：
        // 原实现只写 `while (allItems.length < totalCount)`，一旦后端返回的
        // total 与可返回条数不一致（分页越界、软删过滤口径不同、并发写入等），
        // 这个循环**永远不会结束**，会持续向后端发请求并把页面卡在 loading。
        // 现在加上"最大页数"与"空页即停"两道硬兜底。
        const MAX_PAGES = 100;
        const allItems: QuizItem[] = [];
        let page = 1;
        const pageSize = 100;
        let totalCount = 0;
        let truncated = false;

        while (page <= MAX_PAGES) {
          const data = await getQuestions(page, pageSize, noteId, keyword);
          const items = data.items || [];
          allItems.push(...items);
          totalCount = data.total ?? allItems.length;

          // 空页说明已经取完（后端 total 可能不准），立即停止
          if (items.length === 0) break;
          // 已取够 total 声明的数量
          if (allItems.length >= totalCount) break;

          page++;
        }

        if (page > MAX_PAGES && allItems.length < totalCount) {
          truncated = true;
          console.warn(
            `[QuestionSets] 分页达到上限 ${MAX_PAGES} 页，已加载 ${allItems.length}/${totalCount} 条`,
          );
        }

        const grouped = groupByNote(allItems);
        setGroups(grouped);
        setTotal(truncated ? allItems.length : totalCount);
        setExpandedNotes(new Set(grouped.map((g) => g.note_id)));
      } catch (err) {
        setError(err instanceof Error ? err.message : '加载失败');
      } finally {
        setLoading(false);
      }
    },
    [noteId],
  );

  // 挂载/参数变化时加载数据（数据获取型 effect，同步 setState 豁免）
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    fetchQuestions(searchKeyword || undefined);
  }, [noteId, fetchQuestions, searchKeyword]);

  function handleSearchChange(value: string) {
    if (searchTimerRef.current) clearTimeout(searchTimerRef.current);
    searchTimerRef.current = setTimeout(() => {
      setSearchKeyword(value);
    }, 300);
  }

  function toggleGroup(noteId: string) {
    setExpandedNotes((prev) => toggleInSet(prev, noteId));
  }

  function toggleAnswer(questionId: string) {
    setExpandedAnswers((prev) => toggleInSet(prev, questionId));
  }

  function toggleColumn(columnId: ColumnId) {
    setVisibleColumns((prev) => toggleInSet(prev, columnId));
  }

  /**
   * 设置某一筛选维度的取值。
   *
   * 写成"先拷一份再按下标写回"而不是 `{ ...prev, [dimensionId]: value }`：
   * 后者是**联合类型的计算属性名**，TS 对它的推断会退化成索引签名
   * （拿到的类型不再是 `FilterState`）；按下标写回则由 `FilterState` 兜住，
   * 哪天 `FilterId` 多一维而 `FilterState` 忘了加，tsc 当场报错。
   */
  function setFilter(dimensionId: FilterId, value: string) {
    setFilters((prev) => {
      const next: FilterState = { ...prev };
      next[dimensionId] = value;
      return next;
    });
  }

  /**
   * 点列头：同一列再点就翻方向；换一列用 `INITIAL_DIRECTION` 的初始方向。
   *
   * 没有"第三次点回不排序"那一档：初始态已经是不排序（见 `SortState`），
   * 再引入一个三态循环只会让"这一列现在是什么状态"多一种要解释的情况。
   */
  function toggleSort(key: SortKey) {
    setSort((prev) =>
      prev !== null && prev.key === key
        ? { key, direction: prev.direction === 'asc' ? 'desc' : 'asc' }
        : { key, direction: INITIAL_DIRECTION[key] },
    );
  }

  function filterQuestions(questions: QuizItem[]): QuizItem[] {
    return questions.filter((q) =>
      FILTER_DIMENSIONS.every((dimension) => {
        const active = filters[dimension.id];
        return active === 'all' || dimension.valueOf(q) === active;
      }),
    );
  }

  const visibleColumnList = COLUMNS.filter((col) => visibleColumns.has(col.id));
  const columnCount = visibleColumnList.length;
  const filterActive = FILTER_DIMENSIONS.some((dimension) => filters[dimension.id] !== 'all');
  const totalFiltered = groups.reduce((sum, g) => sum + filterQuestions(g.questions).length, 0);

  return (
    <div className="page-enter">
      <PageHeader
        title="问题集"
        spacing="md"
        actions={
          <span className={styles.totalText}>
            共 {total} 道题
            {filterActive ? `，筛选后 ${totalFiltered} 道` : ''}
          </span>
        }
      />

      {/* 搜索栏 —— 批次 B3：补放大镜（此前只有 NotesList 那一处有图形） */}
      <div style={{ position: 'relative', marginBottom: 'var(--space-md)' }}>
        {/* 放大镜把左内边距吃掉 34px：图标 16px + 左 10px + 与文字留 8px。
            绝对定位 + `pointer-events: none`，所以它不会挡住输入框的点击 */}
        <Icon
          name="search"
          size={16}
          style={{
            position: 'absolute',
            left: 10,
            top: '50%',
            transform: 'translateY(-50%)',
            color: 'var(--color-text-tertiary)',
            pointerEvents: 'none',
          }}
        />
        <input
          type="text"
          placeholder="搜索题目内容..."
          onChange={(e) => handleSearchChange(e.target.value)}
          style={{
            width: '100%',
            padding: '8px 12px',
            paddingLeft: 34,
            border: '1px solid var(--color-border)',
            borderRadius: '8px',
            fontSize: 'var(--text-base)',
            background: 'var(--color-bg)',
            color: 'var(--color-text)',
            boxSizing: 'border-box',
          }}
        />
      </div>

      {/* 筛选栏 —— 批次 E5：两串药丸改成**数据驱动的筛选维度**。
          每个 chip 是 `<button aria-pressed>`（此前只有颜色差别：`filter-pill-active`
          给的是底色 + 金色下划线，读屏用户听不出"选中了没有"）。 */}
      <div
        style={{
          display: 'flex',
          gap: 'var(--space-sm)',
          marginBottom: 'var(--space-lg)',
          flexWrap: 'wrap',
          alignItems: 'center',
        }}
      >
        {/* 批次 B3：筛选区补漏斗图标 —— 这一栏此前是两串纯文字药丸，
            没有任何"这是在筛选"的视觉线索 */}
        <Icon
          name="filter"
          size={16}
          style={{ color: 'var(--color-text-secondary)', flexShrink: 0 }}
        />
        {FILTER_DIMENSIONS.map((dimension) => (
          <div
            key={dimension.id}
            role="group"
            aria-label={`按${dimension.label}筛选`}
            style={{ display: 'flex', alignItems: 'center', gap: '4px' }}
          >
            <span style={{ fontSize: 'var(--text-sm)', color: 'var(--color-text-secondary)' }}>
              {dimension.label}：
            </span>
            {dimension.options.map((opt) => {
              const pressed = filters[dimension.id] === opt.value;
              return (
                <button
                  key={opt.value}
                  type="button"
                  className={`filter-pill ${pressed ? 'filter-pill-active' : ''}`}
                  aria-pressed={pressed}
                  onClick={() => setFilter(dimension.id, opt.value)}
                >
                  {opt.label}
                </button>
              );
            })}
          </div>
        ))}
      </div>

      {loading ? (
        <LoadingSpinner />
      ) : error ? (
        <ErrorDisplay message={error} onRetry={fetchQuestions} />
      ) : groups.length === 0 ? (
        <EmptyState message="暂无题目" description="请先上传笔记并触发理解管道生成题目" />
      ) : totalFiltered === 0 ? (
        // 此前这一支渲染的是"一个只剩筛选栏的空页面"（分组全被过滤掉、逐个 return null）。
        // 表格化之后那个空壳更刺眼：列头还在、下面一行都没有。
        <EmptyState message="没有符合筛选条件的题目" description="试试放宽题型或难度筛选" />
      ) : (
        <>
          {/* 工具条：「列」面板的开关 + 排序可发现性提示。
              开关用全局 `.filter-pill`（本页已有的控件语言），不新造全局类。 */}
          <div className={styles.toolbar}>
            <button
              type="button"
              className="filter-pill"
              aria-expanded={columnsOpen}
              aria-controls={COLUMN_PANEL_ID}
              onClick={() => setColumnsOpen((open) => !open)}
            >
              列（{columnCount}/{COLUMNS.length}）
            </button>
            <span className={styles.toolbarHint}>点列头可按该列排序</span>
          </div>

          {/* 「列」面板：**常驻 DOM、用 `hidden` 属性开合**。
              不用条件渲染是因为 `aria-controls` 指向一个不存在的 id 时，
              axe 的 `aria-valid-attr-value` 会报"IDREF 指向的节点不在文档里"。
              `hidden` 在 CSS 里要显式压掉 `display: flex`（见模块里 `.columnsPanel[hidden]`）。 */}
          <div id={COLUMN_PANEL_ID} className={styles.columnsPanel} hidden={!columnsOpen}>
            <span className={styles.columnsPanelTitle}>显示列</span>
            {COLUMNS.filter((col) => col.hideable).map((col) => (
              <label key={col.id} className={styles.columnOption}>
                <input
                  type="checkbox"
                  checked={visibleColumns.has(col.id)}
                  onChange={() => toggleColumn(col.id)}
                />
                {col.label}
              </label>
            ))}
            <p className={styles.columnsHint}>
              「题目」「难度」固定显示：难度那一格里的中文是行底色的文字说明，隐藏它，色觉障碍用户就读不出行状态了。
            </p>
          </div>

          <div className={styles.tableScroll}>
            <table className={styles.table} aria-label="题目列表（按所属笔记分组）">
              <thead>
                <tr>
                  {visibleColumnList.map((col) => {
                    // `col` 是可辨识联合，但 TS 的窄化**不会**带进 `onClick` 的闭包
                    // （参数是可变量），所以先用 const 承接一次再进渲染分支。
                    const sortKey: SortKey | null = col.sortable ? col.id : null;
                    const ariaSort = ariaSortOf(sortKey, sort);
                    return (
                      <th key={col.id} scope="col" className={styles.th} aria-sort={ariaSort}>
                        {sortKey ? (
                          // 可排序的列头是**真按钮**：键盘能 Tab 到、能回车触发。
                          // 带 `onClick` 的 `<th>` 键盘到不了（本项目 F-09/F-17/F-30/F-34
                          // 四处先例都是这一类）。
                          <button
                            type="button"
                            className={styles.sortButton}
                            onClick={() => toggleSort(sortKey)}
                            aria-label={sortButtonLabel(sortKey, col.label, sort)}
                          >
                            {col.label}
                            {/* 箭头是外观（`Icon` 默认 aria-hidden），含义在 `aria-sort` 里 */}
                            <span className={SORT_ICON_CLASS[sortIconState(sortKey, sort)]}>
                              <Icon name="chevron" size={16} />
                            </span>
                          </button>
                        ) : (
                          <span className={styles.thPlain}>{col.label}</span>
                        )}
                      </th>
                    );
                  })}
                </tr>
              </thead>

              {/* 分组仍然保留（`visual-design-spec.md` §6.3），落地为**每组一个 `<tbody>`**：
                  列头因此只有一份，Tab 停靠点不随笔记数放大。 */}
              {groups.map((group) => {
                const filtered = filterQuestions(group.questions);
                if (filtered.length === 0) return null;
                const isOpen = expandedNotes.has(group.note_id);
                const rows = sortQuestions(filtered, sort);
                return (
                  <tbody key={group.note_id}>
                    <tr className={styles.groupHeaderRow}>
                      <td colSpan={columnCount}>
                        <div className={styles.groupHeaderInner}>
                          <div className={styles.groupTitleCell}>
                            {/* 折叠/展开是**一个真控件**。
                                ⚠️ 这里原来是 `div.card[onClick]`（没有 role/tabIndex，键盘到不了），
                                里面还嵌着「查看笔记」真按钮。改法与 F-30 / KnowledgeCards 分组头同形：
                                外壳回到"盒子"，折叠行为落进真 `<button aria-expanded>`，
                                「查看笔记」是它的**兄弟**（不再是被点区域的后代）。
                                `stopPropagation` 留着（外层已经没有 onClick 了）：它同时对
                                "点空白处"这类调用有意义，删掉属于顺手重构。 */}
                            <button
                              type="button"
                              onClick={() => toggleGroup(group.note_id)}
                              aria-expanded={isOpen}
                              style={groupToggleStyle}
                            >
                              {/* 箭头只表达外观：展开状态由 `aria-expanded` 承担。
                                  批次 B3：`▶` 换 `<Icon name="chevron" />`，旋转过渡仍由
                                  `.collapse-arrow` / `.collapse-arrow-open` 提供 */}
                              <span
                                className={`collapse-arrow ${isOpen ? 'collapse-arrow-open' : ''}`}
                                aria-hidden="true"
                              >
                                <Icon name="chevron" size={16} />
                              </span>
                              <strong>{group.note_title}</strong>
                            </button>
                            <span className={styles.groupCount}>({filtered.length} 道题)</span>
                          </div>
                          <button
                            className="btn btn-secondary"
                            style={{ fontSize: 'var(--text-xs)', padding: '2px 8px' }}
                            onClick={(e) => {
                              e.stopPropagation();
                              navigate(`/notes/${group.note_id}`);
                            }}
                          >
                            查看笔记
                          </button>
                        </div>
                      </td>
                    </tr>

                    {isOpen &&
                      rows.map((q) => {
                        const answerOpen = expandedAnswers.has(q.id);
                        const options = q.question_type === 'choice' ? parseOptions(q.options) : [];
                        return (
                          <Fragment key={q.id}>
                            <tr className={`${styles.row} ${ROW_STATE_CLASS[q.difficulty] ?? ''}`}>
                              {visibleColumnList.map((col) => (
                                <td key={col.id} className={styles.td}>
                                  {col.id === 'question' && (
                                    <p className={styles.questionText}>{q.question}</p>
                                  )}
                                  {/* 题型徽章：与改动前的取值逐字一致
                                      （`questionTypeColors` / `questionTypeLabels`，
                                      白色 ≥4.5:1，见 utils/labels.ts 的取值口径） */}
                                  {col.id === 'question_type' && (
                                    <span
                                      className={styles.pill}
                                      style={{
                                        background:
                                          questionTypeColors[q.question_type] ||
                                          FALLBACK_CATEGORY_COLOR,
                                      }}
                                    >
                                      {questionTypeLabels[q.question_type] || q.question_type}
                                    </span>
                                  )}
                                  {/* 难度徽章：**行三态底色的文字载体**，所以这一列不可隐藏 */}
                                  {col.id === 'difficulty' && (
                                    <span
                                      className={styles.pill}
                                      style={{
                                        background:
                                          difficultyColors[q.difficulty] || FALLBACK_CATEGORY_COLOR,
                                      }}
                                    >
                                      {difficultyLabels[q.difficulty] || q.difficulty}
                                    </span>
                                  )}
                                  {col.id === 'created_at' && (
                                    <span className={styles.timeText}>
                                      {formatTime(q.created_at)}
                                    </span>
                                  )}
                                  {col.id === 'updated_at' && (
                                    <span className={styles.timeText}>
                                      {formatTime(q.updated_at)}
                                    </span>
                                  )}
                                  {col.id === 'detail' && (
                                    // `aria-expanded` 是新增的：改动前这个按钮只换文案，
                                    // 读屏用户不知道点下去会发生什么、也不知道现在是开是关。
                                    // 刻意**不写 `aria-controls`**：被控制的那一行在收起时
                                    // 根本不渲染，悬空的 IDREF 会被 axe 的
                                    // `aria-valid-attr-value` 判为违规。
                                    <button
                                      type="button"
                                      className="btn btn-secondary"
                                      style={{ fontSize: 'var(--text-xs)', padding: '2px 10px' }}
                                      aria-expanded={answerOpen}
                                      onClick={() => toggleAnswer(q.id)}
                                    >
                                      {answerOpen ? '隐藏答案' : '显示答案'}
                                    </button>
                                  )}
                                </td>
                              ))}
                            </tr>

                            {/* 答案不是"卡片里的一段"，而是表格里独立的一行（`colSpan` 铺满）。
                                选择题的选项也搬进这里：行内只留题面，扫读时一行就是一题。 */}
                            {answerOpen && (
                              <tr className={styles.detailRow}>
                                <td colSpan={columnCount}>
                                  <div className={styles.answerBlock}>
                                    {/* 选择题的选项：改动前常驻在卡片里。表格化之后搬进
                                        展开区 —— 一行一题才扫得动，而选项本来是"看题面"
                                        的一部分，跟着答案一起展开也不违背遮挡语义。 */}
                                    {options.length > 0 && (
                                      <div className={styles.optionList}>
                                        {options.map((opt, i) => (
                                          <div key={i} className={styles.optionItem}>
                                            {opt}
                                          </div>
                                        ))}
                                      </div>
                                    )}
                                    {/* 答案文字：原值 `#10b981` 白底只有 2.54:1（0.875rem，要求 4.5:1）——
                                        与 a11y-audit 的 F-36（今日学习的「低」优先级徽章，同一个色值）
                                        是同一次"亮绿压浅底"的洞。`#25714a` = `--color-success`，白底 5.93:1。
                                        批次 E5：这个字面量换成模块里的 `var(--color-success)`。
                                        这行答案此前从未被任何一层判过（要点击「显示答案」才渲染），
                                        本轮的 `question-sets` 场景会点开它，所以它现在有门禁。 */}
                                    <p className={styles.answerText}>
                                      <strong>答案：</strong>
                                      {q.answer}
                                    </p>
                                    {q.explanation && (
                                      <p className={styles.explanationText}>
                                        <strong>解析：</strong>
                                        {q.explanation}
                                      </p>
                                    )}
                                  </div>
                                </td>
                              </tr>
                            )}
                          </Fragment>
                        );
                      })}
                  </tbody>
                );
              })}
            </table>
          </div>
        </>
      )}
    </div>
  );
}

/**
 * 分组头里那个折叠按钮的外观复位。
 *
 * `<button>` 有自己的 UA 样式（系统字体、灰底、2px 边框、居中文字、内边距），
 * 不复位的话"把 div 换成真按钮"就变成了一次改版。下面的取值逐项对应
 * **改动前那一行 div 的实际外观**：字号/字重/颜色继承外层（`<strong>` 的
 * 700 与正文的 1em 都是继承来的），背景与边框本来就没有。
 */
const groupToggleStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: 'var(--space-sm)',
  margin: 0,
  padding: 0,
  border: 'none',
  background: 'none',
  font: 'inherit',
  color: 'inherit',
  textAlign: 'left',
  cursor: 'pointer',
};
