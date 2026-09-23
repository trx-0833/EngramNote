/**
 * 可访问性审计（overhaul-plan 5.9）用的 `/api` 桩
 *
 * ## 为什么不复用 `support.ts` 的 `stubApi`
 *
 * 那个桩是给"登录链路"用的，所有列表接口一律回**空**。空列表在 a11y 审计里
 * 是最坏的一种桩：页面只剩空状态，卡片、标签、图例、分页、进度条这些
 * **真正会被 axe 判定的 DOM** 一个都不存在 —— 扫出来"0 违规"，
 * 而这 0 是"没东西可查"，不是"页面没问题"。
 *
 * 所以这里另外给每个被审页面喂**非空**的业务数据（笔记、卡片、图谱节点、项目），
 * 让审计跑在"有内容"的渲染结果上。
 *
 * ## 为什么未匹配的请求要显式失败
 *
 * 审计的价值取决于"扫的到底是哪个页面"。如果未知接口被静默兜成空响应，
 * 页面可能停在骨架/错误态，而测试照样绿 —— 那正是本仓库反复踩到的
 * "配置了却从未真正执行"。这里的策略是：**未知路径一律 501 + 明确文案**，
 * 页面上会出现可见的错误提示，同时测试输出里能看到这条 URL。
 * 迁移接口后忘记补桩，会以"页面渲染不出来"的形式暴露，而不是静默降级。
 *
 * ## 为什么桩里写的是"前端期望的形状"
 *
 * 与 `support.ts` 一样：本层证明的是**前端在真实浏览器里的渲染结果**，
 * 不证明后端会这样响应。接口契约由 `backend/tests/` 负责。
 */
import type { Page } from '@playwright/test';

import { blockThirdParty, collectPageErrors, isApiUrl } from './support';

// ── 固定的时间戳：让渲染结果与"今天"无关，审计可重复 ──
const T0 = '2026-01-05T08:00:00Z';
const T1 = '2026-01-06T09:30:00Z';

/** 笔记列表里的一条（`GET /notes`） */
function note(over: Record<string, unknown> = {}) {
  return {
    id: 'note-1',
    user_id: 'e2e-user',
    title: '锂离子电池的浮充与均充',
    source_type: 'pdf',
    note_role: 'material',
    project_ids: ['proj-1'],
    project_names: ['蓄电池基础'],
    status: 'cleaned',
    file_size: 524_288,
    page_count: 12,
    error_message: null,
    trashed_at: null,
    created_at: T0,
    updated_at: T1,
    ...over,
  };
}

/** 笔记详情（`GET /notes/{id}`） */
function noteDetail() {
  const clean = [
    '# 浮充与均充',
    '',
    '浮充是蓄电池的一种**长期恒压**运行方式，用于补偿自放电。',
    '均充则是**短时提高电压**的补充充电，用于消除硫化。',
    '',
    '## 两者的区别',
    '',
    '- 电压：浮充低于均充',
    '- 时长：浮充长期，均充数小时',
    '',
    '详见 [相关说明](https://example.com/battery) 与 `float_voltage` 参数。',
    '',
  ].join('\n');
  return {
    ...note(),
    original_md_content: `# 原始内容\n\n${clean}`,
    clean_md_content: clean,
    metadata_: null,
    original_file_path: '/u/note-1.pdf',
    original_md_path: '/u/note-1.md',
    clean_md_path: '/u/note-1.clean.md',
  };
}

/**
 * 覆盖某场景的 `/api/notes` 列表（形状与默认桩一致）。
 *
 * 存在的理由：**逐场景喂不同的笔记**。默认那份列表是 `notes-list` / `dashboard`
 * 两个已存在场景的渲染输入，往里面加一条会连带改变它们的 DOM 与违规计数；
 * 而"把 status=converting/cleaning 的笔记渲染出来"必须在**某一个**场景里做。
 * 用覆盖而不是改默认值，等于把"新状态的可见性"与"已扫场景的基线"解耦。
 */
export function notesList(items: Record<string, unknown>[]): Record<string, unknown> {
  return { items, total: items.length, page: 1, page_size: 20 };
}

/**
 * 两条**处理中**状态的笔记（`converting` / `cleaning`）—— 专供
 * `notes-status-processing` 场景（见 a11y.spec.ts）。
 *
 * ## 为什么这两个状态值得单独一个场景
 *
 * 类名由 `utils/labels.ts` 的 `statusClass()` 从 `note.status` 映射而来
 * （`converting → .status-converting`、`cleaning → .status-cleaning`，
 * 单一数据源），而这两个类**都用 `--color-warning`（#c4860a）**。
 * 默认桩里只有 `cleaned` / `converted`，所以这条规则的颜色在审计里
 * **一次都没有被渲染过** —— 不是"通过了"，是"没扫到"
 * （见 docs/a11y-audit.md §8.3 第 2 条）。
 *
 * 标题刻意写成"只有本场景才有"的字符串：`ready` 标记靠它判断
 * "这一份列表真的渲染出来了"，而不是"某个通用文案出现过"。
 */
export const PROCESSING_NOTES = [
  note({
    id: 'note-converting',
    title: '待转换：蓄电池巡检记录.docx',
    source_type: 'docx',
    status: 'converting',
    project_names: [],
    page_count: null,
  }),
  note({
    id: 'note-cleaning',
    title: '待清洗：浮充与均充对照表.pdf',
    source_type: 'pdf',
    status: 'cleaning',
    project_names: [],
  }),
];

/** 到期卡片（`GET /review/cards/due`） */
const DUE_CARDS = [
  {
    card_id: 'card-1',
    title: '浮充的定义',
    content: '蓄电池的一种运行方式，端电压保持恒定。',
    summary: '浮充 = 恒压运行',
    card_type: 'concept',
    chapter_title: '第一章 蓄电池',
    note_id: 'note-1',
    mastery_level: 57.4,
    interval_days: 6,
    repetition: 2,
    easiness_factor: 2.5,
    next_review_at: null,
    review_count: 3,
    lapses: 1,
  },
  {
    card_id: 'card-2',
    title: '均充的适用场景',
    content: '长期浮充后单体电压偏差变大时，需要短时均充校正。',
    summary: '均充 = 校正电压偏差',
    card_type: 'qa',
    chapter_title: '第一章 蓄电池',
    note_id: 'note-1',
    mastery_level: 31.2,
    interval_days: 2,
    repetition: 1,
    easiness_factor: 2.36,
    next_review_at: null,
    review_count: 1,
    lapses: 0,
  },
];

/**
 * 到期**题目**（`GET /review/due`）—— 答题复习页（`Review`）的输入。
 *
 * 与 `DUE_CARDS` 是两份不同的数据：卡片复习读 `/review/cards/due`，
 * 答题复习读 `/review/due`。审计到本轮为止**只桩了前者**，所以打开 `/review`
 * 时 `getDueQuizzes` 拿到 501 → 页面停在 `error && quizzes.length === 0`
 * 的**错误分支**（一行报错 + 一个「重试」按钮）。那不是"覆盖率低"，
 * 是"扫的根本不是这一页"。
 *
 * 两道题的字段都刻意带上会渲染到界面上的东西：
 *   - 第一题 `choice` + `difficulty: 'medium'` → 难度徽章（白字压
 *     `difficultyColors.medium`）；
 *   - 第二题 `fill_blank` + `difficulty: 'easy'` → 另一种作答控件与另一个色值。
 */
const DUE_QUIZZES = [
  {
    id: 'quiz-1',
    card_id: 'c-1',
    note_id: 'note-1',
    question_type: 'choice',
    difficulty: 'medium',
    question: '浮充与均充的主要区别是什么？',
    options: JSON.stringify([
      '浮充电压高于均充电压',
      '浮充长期恒压补偿自放电，均充短时升压校正',
      '两者只是叫法不同',
      '均充用于长期补偿自放电',
    ]),
    next_review_at: null,
    review_count: 3,
    interval: 6,
    easiness_factor: 2.5,
  },
  {
    id: 'quiz-2',
    card_id: 'c-2',
    note_id: 'note-1',
    question_type: 'fill_blank',
    difficulty: 'easy',
    question: '消除蓄电池硫化采用的是____充电。',
    options: null,
    next_review_at: null,
    review_count: 1,
    interval: 2,
    easiness_factor: 2.36,
  },
];

/**
 * 每日推荐任务（`GET /goals/daily-plan`）—— 今日学习页与仪表盘共用这个接口。
 *
 * ⚠️ 默认桩给的是 **400**（无活跃目标，后端真实行为），
 * 那是为了 `dashboard` 场景保持它登记时的渲染结果。这一份**只作为
 * `today-learn` 场景的覆盖**传入：`dailyPlan.total_count > 0` 才会渲染
 * 「每日推荐任务」整块（三个类别卡片 + 优先级徽章），而那正是这一页
 * 除了统计之外的主要 DOM。
 *
 * 三个优先级（1/2/3）各给一条：它们对应三个不同的徽章底色，
 * 少给一个就等于少量一个色值。
 */
export const DAILY_PLAN: Record<string, unknown> = {
  id: 'plan-1',
  goal_id: 'goal-1',
  plan_date: '2026-01-06',
  recommended_tasks: [
    {
      task_type: 'weak_point',
      quiz_id: 'quiz-2',
      card_id: 'c-2',
      note_id: null,
      priority: 1,
      title: '复习「均充的适用场景」',
    },
    {
      task_type: 'review',
      quiz_id: 'quiz-1',
      card_id: 'c-1',
      note_id: null,
      priority: 2,
      title: '复习「浮充的定义」',
    },
    {
      task_type: 'new_material',
      quiz_id: null,
      card_id: null,
      note_id: 'note-2',
      priority: 3,
      title: '阅读新资料「铅酸电池的硫化机理」',
    },
  ],
  completed_count: 1,
  total_count: 3,
};

/**
 * 薄弱点（`GET /report/weak-points`）—— **契约形状**（`WeakPoint` 的字段名）。
 *
 * ⚠️ 默认桩里那份写的是 `title` / `review_count`，而 `api/report.ts` 的
 * `WeakPoint` 要的是 `card_title` / `error_count`。后果不是"渲染失败"，
 * 而是**页面照常渲染、文字是 `undefined`、徽章是空的** —— 空文本没有
 * 对比度可判，于是 axe 对那一块**无话可说**。也就是说：默认桩让
 * `dashboard` / `dashboard-mobile` 两个场景里的「薄弱点」卡片
 * **看起来被扫过了，实际没有被判过**（见 docs/a11y-audit.md §9.2）。
 *
 * 本轮**不改默认桩**：改了会连带改变那两个已存在场景的渲染与计数，
 * 而本轮的任务是"先拿到信号"，不是"顺手挪动已扫场景的基线"。
 * 这一份只给 `today-learn` 场景用，让同一个卡片**在至少一个场景里
 * 渲染出真实内容**。
 */
export const WEAK_POINTS: Record<string, unknown> = {
  items: [
    {
      card_id: 'c-2',
      card_title: '均充的适用场景',
      card_type: 'qa',
      note_id: 'note-1',
      note_title: '锂离子电池的浮充与均充',
      error_count: 3,
      total_reviews: 5,
      accuracy: 0.4,
    },
    {
      card_id: 'c-3',
      card_title: '浮充电压公式',
      card_type: 'formula',
      note_id: 'note-1',
      note_title: '锂离子电池的浮充与均充',
      error_count: 2,
      total_reviews: 4,
      accuracy: 0.5,
    },
  ],
  total: 2,
};

/** 图谱节点（`GET /graph`）—— 有节点才画得出 SVG/canvas，也才有工具栏与统计 */
const GRAPH_NODES = [
  { id: 'c-1', title: '浮充的定义', card_type: 'concept', note_id: 'note-1', relation_count: 2 },
  { id: 'c-2', title: '均充的定义', card_type: 'concept', note_id: 'note-1', relation_count: 1 },
  { id: 'c-3', title: '浮充电压公式', card_type: 'formula', note_id: 'note-1', relation_count: 1 },
  { id: 'c-4', title: '浮充与均充的区别', card_type: 'qa', note_id: 'note-1', relation_count: 0 },
];

const GRAPH_EDGES = [
  {
    id: 'e-1',
    source: 'c-1',
    target: 'c-2',
    relation_type: 'related',
    status: 'suggested',
    similarity_score: 0.82,
  },
  {
    id: 'e-2',
    source: 'c-1',
    target: 'c-3',
    relation_type: 'prerequisite',
    status: 'confirmed',
    similarity_score: 0.55,
  },
];

/**
 * 知识卡片（`KnowledgeCard` 契约，见 api/qa.ts:8-36）。
 *
 * ## `id` 与 `title` 的关系是有意的：`card-1` 就是 `/cards/card-1` 那一张
 * 卡片详情页（`CardDetail`）取的那一张 —— 两页共用同一份数据，
 * 所以"列表里看到的就是详情里看到的"，不会出现两份互相矛盾的知识卡片。
 *
 * ## 四张卡片刻意覆盖四张颜色表的取值
 *
 * `card_type` 取遍 `concept` / `formula` / `qa` / `definition`（= `cardTypeColors`
 * 的四个键），`card_category` 取遍 `regular` / `blind_spot` / `extension`
 * （= `cardCategoryColors` 的三个键）。这些徽章是**白字压色块**，
 * 也就是 a11y-audit 的 F-33 那一类（上一次只修了一张表），
 * 少喂一个取值就少判一个色值。`mastery_level` 也刻意分档：
 * 一张 ≥80（会多渲染一行金色的「✨ 建议生成拓展知识点」——那一行的颜色
 * 与 F-19/F-33 同源，也是本轮修的），其余低于 80。
 */
function knowledgeCard(over: Record<string, unknown> = {}) {
  return {
    id: 'card-1',
    user_id: 'e2e-user',
    note_id: 'note-1',
    note_title: '锂离子电池的浮充与均充',
    card_type: 'concept',
    title: '浮充的定义',
    content: '浮充是蓄电池的一种长期恒压运行方式，用于补偿自放电，端电压保持恒定。',
    summary: '浮充 = 恒压运行',
    chapter_title: '第一章 蓄电池',
    source_text: '浮充电压一般保持在 2.23~2.27 V/单体，均充则升到 2.30~2.35 V/单体。',
    metadata_: null,
    card_category: 'regular',
    is_key_point: true,
    is_difficulty: false,
    mastery_level: 57.4,
    source_note_ids: null,
    parent_card_id: null,
    created_at: T0,
    updated_at: T1,
    ...over,
  };
}

const KNOWLEDGE_CARDS = [
  knowledgeCard(),
  knowledgeCard({
    id: 'card-2',
    card_type: 'qa',
    card_category: 'blind_spot',
    title: '均充的适用场景',
    content: '长期浮充后单体电压偏差变大时，需要短时均充校正。',
    summary: null,
    is_key_point: false,
    is_difficulty: true,
    // ≥80：多渲染一行「✨ 建议生成拓展知识点」（金色 0.7rem 文字，本轮修过色）
    mastery_level: 85.2,
  }),
  knowledgeCard({
    id: 'card-3',
    card_type: 'formula',
    card_category: 'extension',
    title: '浮充电压公式',
    content: 'U = 2.25 + 0.05 × (25 - T) V/单体。',
    summary: null,
    chapter_title: null,
    source_text: null,
    is_key_point: false,
    mastery_level: 31.8,
  }),
  knowledgeCard({
    id: 'card-4',
    card_type: 'definition',
    card_category: 'regular',
    title: '硫化的定义',
    content: '极板上生成不可逆硫酸铅，导致容量下降。',
    summary: null,
    chapter_title: null,
    source_text: null,
    is_key_point: false,
    mastery_level: 12.5,
  }),
];

/**
 * 题目（`QuizItem` 契约，见 api/qa.ts:39-62）—— `QuestionSets`（整个列表）
 * 与 `CardDetail`（按 `card_id` 过滤出「关联题目」）**共用同一个接口**。
 *
 * `total` 必须等于 `items.length`：`QuestionSets.fetchQuestions` 是**翻页循环**
 * （`QuestionSets.tsx:82-94`，直到 `items.length === 0` 或收满 `total`），
 * `total` 写大了它会一直翻到 100 页的硬上限。
 *
 * 两道的 `question_type` / `difficulty` 刻意错开，覆盖四张颜色表里更多的键：
 * `choice`+`medium`（金）、`fill_blank`+`easy`（绿）—— 这两个色值正是
 * F-33（`#c9a959` 2.25:1）与 F-19（`#2d8a56` 4.30:1）报出来的那两个。
 */
const QUIZ_ITEMS = [
  {
    id: 'qi-1',
    user_id: 'e2e-user',
    card_id: 'card-1',
    note_id: 'note-1',
    note_title: '锂离子电池的浮充与均充',
    question_type: 'choice',
    difficulty: 'medium',
    question: '浮充与均充的主要区别是什么？',
    answer: '浮充长期恒压补偿自放电，均充短时升压校正',
    options: JSON.stringify([
      '浮充电压高于均充电压',
      '浮充长期恒压补偿自放电，均充短时升压校正',
      '两者只是叫法不同',
    ]),
    explanation: '浮充是长期恒压运行，均充是短时提高电压的补充充电。',
    metadata_: null,
    created_at: T0,
    updated_at: T1,
  },
  {
    id: 'qi-2',
    user_id: 'e2e-user',
    card_id: 'card-2',
    note_id: 'note-1',
    note_title: '锂离子电池的浮充与均充',
    question_type: 'fill_blank',
    difficulty: 'easy',
    question: '消除蓄电池硫化采用的是____充电。',
    answer: '均充',
    options: null,
    explanation: '均充用于消除硫化，浮充只补偿自放电。',
    metadata_: null,
    created_at: T1,
    updated_at: T1,
  },
];

/** 回收站里的一条（`TrashNoteItem` 契约，见 api/notes.ts:118-137） */
const TRASH_ITEMS = [
  {
    note: {
      ...note({
        id: 'note-trashed',
        title: '已删除：蓄电池寿命与温度的关系',
        source_type: 'docx',
        status: 'archived',
        trashed_at: '2026-01-02T03:04:05+00:00',
        project_names: [],
        page_count: null,
      }),
    },
    card_count: 4,
    quiz_count: 3,
    annotation_count: 2,
    version_count: 5,
    link_count: 1,
  },
];

/**
 * 快速复习的一道题（`QuickReviewResponse` = `{ items: QuickQuiz[]; total }`，
 * `QuickQuiz = DueQuiz`，见 api/review.ts:8-20/125-131）。
 *
 * `difficulty: 'medium'` 是有意的：`QuickReview` 渲染的是共用的
 * `QuizAnswerCard`，而那块难度徽章用的就是 `difficultyColors.medium`
 * —— 也就是 F-33 报出来的 `#c9a959`。审计文档里"**QuickReview 与 QA 共用
 * `QuizAnswerCard`，所以 F-33 大概率也在那里**"当时只是**推断**；
 * 这个场景把它变成**实测**（顺带纠正一半：`QA` 是 SSE 聊天页，不共用它）。
 */
const QUICK_QUIZZES = [
  {
    id: 'qq-1',
    card_id: 'card-1',
    note_id: 'note-1',
    question_type: 'choice',
    difficulty: 'medium',
    question: '浮充与均充的主要区别是什么？',
    options: JSON.stringify([
      '浮充电压高于均充电压',
      '浮充长期恒压补偿自放电，均充短时升压校正',
      '两者只是叫法不同',
    ]),
    next_review_at: null,
    review_count: 2,
    interval: 4,
    easiness_factor: 2.5,
  },
];

/**
 * 学习目标（`GoalListResponse` 契约，见 api/goals.ts:7-29）。
 *
 * 两份分开给：`LearningGoals` 用**同一个路径 + 不同查询串**取"进行中"与
 * "已归档"两份（`/api/goals?status=active` / `?status=archived`）。
 * 只按路径名匹配时两份请求会拿到同一份响应，"已归档目标 (N)" 里显示的
 * 其实是进行中的目标 —— 页面照常渲染、断言照常绿，而那一块**没有被真正判过**。
 * 所以这里用**带查询串的桩键**（见 `queryKey` 的说明）。
 *
 * 每个字段都要填对：`goal.progress_percentage` 渲染成 `{n}%` 与进度条宽度，
 * `deadline` 决定有没有「剩余 N 天」那一行，`type` 决定徽章文案与底色。
 */
const ACTIVE_GOALS = [
  {
    id: 'goal-1',
    user_id: 'e2e-user',
    name: '掌握蓄电池基础概念',
    type: 'daily',
    scope_notes: ['note-1'],
    scope_folders: [],
    target_mastery: 80,
    deadline: '2026-02-01T00:00:00+00:00',
    status: 'active',
    progress_cache: 0.42,
    last_progress_refresh: T1,
    progress_percentage: 42,
    created_at: T0,
    updated_at: T1,
  },
];

const ARCHIVED_GOALS = [
  {
    ...ACTIVE_GOALS[0],
    id: 'goal-2',
    name: '读完《蓄电池维护手册》',
    type: 'weekly',
    target_mastery: 60,
    deadline: null,
    status: 'archived',
    progress_percentage: 100,
  },
];

/**
 * 问答流的一次回答（`POST /understanding/ask/stream` 的 SSE 响应体）。
 *
 * ## 为什么这个桩必须是**流**而不是 JSON
 *
 * `QA` 页在挂载时**一个请求都不发**（它只有一个输入框 + 空状态），
 * 主页面的内容**只有真的问过一次之后才存在**。所以 `qa` 场景必须先
 * 完成一次真实的"输入 → 提问"，并且桩要按 `text/event-stream` 回一段
 * 事件流（见 `rawBody`）—— 否则页面永远停在 `AI 正在思考...`，
 * 而那一句**既是加载态也是空答案态**（`QA.tsx:248/267`），
 * 拿它当"已经渲染出来了"的判据是假的。
 *
 * 事件形状取自 `api/client.ts:441-449`。`retrieval_status` 用 `hybrid`
 * （不是 `bm25_only`，那是"向量服务不可用"的降级横幅，是另一个渲染分支），
 * `provider` 用 `deepseek` —— 页脚会渲染成「由 DeepSeek 提供支持」。
 */
export const QA_ANSWER_SSE = [
  'event: meta',
  `data: ${JSON.stringify({ retrieval_status: 'hybrid', provider: 'deepseek' })}`,
  '',
  'event: token',
  `data: ${JSON.stringify({ content: '浮充是长期恒压运行，' })}`,
  '',
  'event: token',
  `data: ${JSON.stringify({ content: '用于补偿自放电；均充是短时升压校正。' })}`,
  '',
  'event: sources',
  `data: ${JSON.stringify({
    provider: 'deepseek',
    sources: [
      {
        note_id: 'note-1',
        note_title: '锂离子电池的浮充与均充',
        chapter_title: '第一章 蓄电池',
        relevant_text: '浮充是蓄电池的一种长期恒压运行方式……',
        chunk_id: 'chunk-1',
        chunk_index: 0,
        char_start: 0,
        char_end: 24,
        heading_path: '两者的区别',
        line_start: 1,
        line_end: 4,
      },
    ],
  })}`,
  '',
  'event: done',
  'data: {}',
  '',
  '',
].join('\n');

/** 项目（`GET /projects`）—— 后端这个接口回的是**裸数组**（见 api/projects.ts） */ const PROJECTS =
  [
    {
      id: 'proj-1',
      user_id: 'e2e-user',
      name: '蓄电池基础',
      description: '浮充、均充与寿命相关资料的合集',
      note_count: 3,
      created_at: T0,
      updated_at: T1,
    },
  ];

/**
 * 文件夹（`GET /folders`）—— 今日资料页（`DailyMaterials`）的列表。
 *
 * 后端这个接口回的是**裸数组**（同 `/projects`，见 api/projects.ts）。
 * 默认给**一个非空文件夹**：空数组会让这一页渲染 `EmptyState`
 * （"还没有文件夹" + 一个按钮），整页只剩标题与空状态 ——
 * 那种页面扫出来必然 0 违规，而它证明的是"没东西可扫"。
 *
 * 只给一个（而不是两个）：文件夹头部是 `div[role="button"]`，
 * 里面还有真按钮，多一个文件夹就多一个同类节点 ——
 * 违规计数应当由"这类问题存在"决定，而不是由"我塞了几个文件夹"决定。
 */
const FOLDERS = [
  {
    id: 'folder-1',
    user_id: 'e2e-user',
    name: '2026-01-05 学习资料',
    description: '浮充与均充的原始资料',
    folder_date: '2026-01-05',
    created_at: T0,
    note_count: 3,
  },
];

/**
 * 文件夹详情（`GET /folders/{id}`）—— 展开文件夹后渲染的那份笔记列表。
 *
 * 三条笔记刻意覆盖三种状态：`cleaned`（已完成）、`converting` / `cleaning`
 * （处理中，即 `--color-warning` 的那两个类）。第二条路径上的同一对类名
 * 与 `notes-status-processing` 场景互为交叉验证：如果两处都报、
 * 修令牌后两处一起消，那就证明它是**共享类名**的问题，而不是某一页的写法。
 */
const FOLDER_DETAIL = {
  ...FOLDERS[0],
  notes: [
    {
      id: 'note-1',
      title: '浮充与均充的讲义.pdf',
      source_type: 'pdf',
      status: 'cleaned',
      file_size: 524_288,
      created_at: T0,
    },
    {
      id: 'note-converting',
      title: '待转换：蓄电池巡检记录.docx',
      source_type: 'docx',
      status: 'converting',
      file_size: 131_072,
      created_at: T1,
    },
    {
      id: 'note-cleaning',
      title: '待清洗：浮充与均充对照表.pdf',
      source_type: 'pdf',
      status: 'cleaning',
      file_size: 262_144,
      created_at: T1,
    },
  ],
};

/**
 * 审计用到的 `/api` 桩：按**路径**匹配，忽略查询串
 * （判据与 `support.ts` 一致：`url.pathname` 以 `/api/` 开头，绝不用子串）。
 *
 * 忽略查询串是有意的：本层的目的是"让页面拿到足够的数据渲染出可审计的 DOM"，
 * 分页/筛选参数的具体取值不是审计对象（例如 `/api/notes?page=1&page_size=20`
 * 与 `/api/notes?keyword=x` 都该拿到同一份列表）。若哪天某个页面**只**在
 * 特定查询串下才渲染出关键 DOM，那属于该页面自己的测试要覆盖的事。
 */
function apiFixtures(): Record<string, unknown> {
  return {
    // ── 认证 ──
    '/api/auth/login': {
      access_token: 'e2e-access-token',
      refresh_token: 'e2e-refresh-token',
      token_type: 'bearer',
      user: {
        id: 'e2e-user',
        email: 'e2e@example.com',
        username: 'e2e',
        is_active: true,
        created_at: T0,
      },
    },
    '/api/auth/reminder-settings': { email_reminder_enabled: false },

    // ── 仪表盘 ──
    '/api/goals': { goals: [], total: 0 },
    // 无活跃目标时后端回 400，前端当作正常业务状态（见 Dashboard.tsx）
    '/api/goals/daily-plan': { __status: 400, detail: 'No active goals' },
    '/api/review/stats': { due_count: 12, today_done: 7, today_accuracy: 0.86, daily_limit: 50 },
    '/api/report/daily': {
      date: '2026-01-06',
      new_mastered: 4,
      total_review_time_ms: 512_000,
      total_reviews: 18,
      today_accuracy: 0.86,
      weak_point_count: 2,
      question_type_accuracy: [{ question_type: 'choice', accuracy: 0.9, total: 10 }],
    },
    '/api/report/weekly-trend': {
      items: [
        { date: '2026-01-02', review_count: 12, accuracy: 0.8 },
        { date: '2026-01-03', review_count: 18, accuracy: 0.86 },
        { date: '2026-01-04', review_count: 9, accuracy: 0.72 },
      ],
      total_reviews: 39,
      avg_accuracy: 0.79,
    },
    // ⚠️ 字段名必须与 `WeakPoint` 契约（api/report.ts）一致：`card_title` / `error_count`。
    // 这里原来写的是 `title` / `review_count` —— 页面**不会崩**，只是把 `undefined`
    // 渲染成文本、徽章渲染成空；而**空文本没有对比度可判**，于是 axe 对整块
    // 无话可说：`dashboard` / `dashboard-mobile` 里的「薄弱点」卡看起来被扫过了，
    // 实际一次都没被判定过（这也是 F-35 在仪表盘上完全不可见的原因）。
    // 本轮把它改对 —— 代价是这两个已扫场景的渲染结果会变（多出真实文本与徽章），
    // 这是**应该变**：原来的"稳定"是建立在"没判过"之上的。
    // 注意 `WeakPoint` 的字段名在 `/api/report/weak-points` 与今日学习页的
    // `WEAK_POINTS` 覆盖里是同一套（那份本来就是契约形状，可以互相对照）。
    '/api/report/weak-points': {
      items: [
        {
          card_id: 'c-2',
          card_title: '均充的适用场景',
          card_type: 'qa',
          note_id: 'note-1',
          note_title: '锂离子电池的浮充与均充',
          error_count: 3,
          total_reviews: 5,
          accuracy: 0.4,
        },
        {
          card_id: 'c-3',
          card_title: '浮充电压公式',
          card_type: 'formula',
          note_id: 'note-1',
          note_title: '锂离子电池的浮充与均充',
          error_count: 2,
          total_reviews: 4,
          accuracy: 0.5,
        },
      ],
      total: 2,
    },

    // ── 笔记 ──
    '/api/notes': {
      items: [
        note(),
        note({
          id: 'note-2',
          title: '铅酸电池的硫化机理',
          source_type: 'docx',
          status: 'converted',
          page_count: null,
          project_names: [],
        }),
      ],
      total: 2,
      page: 1,
      page_size: 20,
    },
    '/api/notes/note-1': noteDetail(),
    '/api/notes/note-1/annotations': [],
    '/api/notes/note-1/links': {
      linked_materials: [],
      linked_personal_notes: [],
      dangling_links: [],
    },
    '/api/notes/note-1/versions': { items: [], total: 0 },
    // 空列表形状要给对：给错形状会让页面渲染成**错误态**，
    // 而审计要的是正常态（错误态是另一件事，见 docs/a11y-audit.md §5）。
    // ⚠️ 这里原来还有一条 `'/api/cards'` —— 那是**死键**：应用从不请求它
    // （知识卡片走的是 `/api/understanding/cards`，见 api/qa.ts:179/186）。
    // 它已经被删掉，取而代之的是下面「覆盖轮（第二轮）」那段里的正确路径。
    // 卡片详情页此前扫不了的**真实原因**就是这个路径写错（请求落到 501 →
    // 页面渲染加载失败页），不是审计文档 §5 第 3 条当时猜的"桩是列表形状"。
    '/api/questions': { items: [], total: 0, page: 1, page_size: 20 },
    '/api/tasks': { items: [], total: 0 },

    // ── 卡片复习 ──
    '/api/review/cards/due': { items: DUE_CARDS, total: DUE_CARDS.length },
    // ── 答题复习（`Review`）──
    // 与上面那条是**两个不同的接口**：只桩 `/review/cards/due` 时，
    // 打开 `/review` 会停在错误分支（页面照常渲染，且"0 违规"）。
    '/api/review/due': { items: DUE_QUIZZES, total: DUE_QUIZZES.length },
    '/api/review/cards/card-1/submit': {
      card_id: 'card-1',
      quality: 4,
      is_correct: true,
      interval_days: 21,
      repetition: 3,
      easiness_factor: 2.46,
      next_review_at: '2026-01-27T04:00:00+00:00',
      mastery_level: 63.1,
      stability: 21.4,
      difficulty: 5.27,
      predicted_retention: 0.62,
    },

    // ── 知识图谱 ──
    '/api/graph': { nodes: GRAPH_NODES, edges: GRAPH_EDGES },
    '/api/graph/suggestions': [],
    '/api/graph/stats': {
      total_nodes: 4,
      total_edges: 2,
      confirmed_edges: 1,
      suggested_edges: 1,
      relation_type_distribution: [
        { relation_type: 'related', count: 1 },
        { relation_type: 'prerequisite', count: 1 },
      ],
      isolated_nodes: 1,
    },

    // ── 项目 ──
    '/api/projects': PROJECTS,
    '/api/projects/proj-1': { ...PROJECTS[0], notes: [] },

    // ── 今日资料（文件夹）──
    // 原来是 `[]`（当时没有场景扫这一页）。本轮 `daily-materials` 场景进来了，
    // 空数组会让它渲染空状态 —— 非空是本文件的核心约定（见文件头）。
    '/api/folders': FOLDERS,
    '/api/folders/folder-1': FOLDER_DETAIL,

    // ══════════════════════════════════════════════════════════════════════
    // 覆盖轮（第二轮）：把剩下 10 个页面接进来时新增的桩
    //
    // ⚠️ 这些键**没有一个**会移动上面任何一个已扫场景的基线：
    // 下面这些路径只有新场景（或它们的页面）会请求，逐条在注释里写明。
    // ══════════════════════════════════════════════════════════════════════

    // ── 知识卡片列表（`KnowledgeCards`，路由 /cards）──
    // ⚠️ 这里曾经有一条 `/api/cards` 的死键：应用**从不**请求那个路径
    // （`api/qa.ts:179` 用的是 `/understanding/cards`），
    // 它只是"看起来有个桩"。审计文档 §5 第 3 条把 `/cards/:cardId` 一直列为
    // "不能扫，因为桩是列表形状" —— 真实原因其实是**路径写错了**，
    // 于是请求落到 501 上、页面渲染成加载失败页。正确路径的桩补上之后，
    // 卡片详情页（`card-detail`）才第一次有真实内容。
    '/api/understanding/cards': {
      items: KNOWLEDGE_CARDS,
      total: KNOWLEDGE_CARDS.length,
      page: 1,
      page_size: 999,
    },
    // ── 卡片详情（`CardDetail`，路由 /cards/:cardId）──
    // 响应体是**单个对象**（不是列表形状）：`CardDetail.tsx:30` 直接 setCard(res)。
    '/api/understanding/cards/card-1': KNOWLEDGE_CARDS[0],
    // ── 题目列表：`QuestionSets`（/questions）与 `CardDetail` 的「关联题目」共用 ──
    '/api/understanding/questions': {
      items: QUIZ_ITEMS,
      total: QUIZ_ITEMS.length,
      page: 1,
      page_size: 20,
    },

    // ── 回收站（`Trash`，路由 /trash）──
    '/api/notes/trash': { items: TRASH_ITEMS, total: TRASH_ITEMS.length },

    // ── 快速复习（`QuickReview`，路由 /review/quick/:noteId）──
    // 路径参数是 noteId；本文件的匹配是**精确路径**（不含通配），
    // 所以场景用的 noteId 必须与这里的键一致（`/review/quick/note-1`）。
    '/api/review/quick/note-1': { items: QUICK_QUIZZES, total: QUICK_QUIZZES.length },

    // ── 学习目标（`LearningGoals`，路由 /goals）──
    // ⚠️ 这两份**刻意不放在默认桩里**：`Dashboard` 也在请求
    // `/api/goals?status=active`（Dashboard.tsx:66），把"有活跃目标"写进默认桩
    // 会同时改变**仪表盘**（已扫场景）的渲染结果 —— 那是移动基线。
    // 所以它们以场景级覆盖传入（见导出的 GOAL_STUBS）。

    // ── 「我的笔记」的关联资料（`LearningAssessment` 的已链接对比模式）──
    // `linked_materials` 是**运行时必需**的：`LearningAssessment.tsx:135`
    // 直接读 `.linked_materials.length`，缺了它这条笔记会被静默丢掉，
    // 页面于是显示"暂无已链接的笔记" —— 又一个"不报错、只是没被判过"的形状。
    '/api/notes/note-personal/links': {
      personal_note_id: 'note-personal',
      linked_materials: [{ id: 'note-1', title: '锂离子电池的浮充与均充', source_type: 'pdf' }],
      linked_personal_notes: [],
      dangling_material_count: 0,
    },

    // ── 智能问答的流式回答（`QA`，路由 /qa）──
    // 页面挂载时不发任何请求，只有真的提问才有内容 —— 见 rawBody 与 QA_ANSWER_SSE。
    '/api/understanding/ask/stream': rawBody(QA_ANSWER_SSE),
  };
}

/**
 * 原样回一段**非 JSON** 文本（目前只有 SSE 用得上）。
 *
 * 为什么需要它：审计的其余接口都是"回一份 JSON、页面渲染出 DOM"，
 * 而 `/api/understanding/ask/stream` 是 `text/event-stream` ——
 * 桩若按 JSON 回，页面会停在"AI 正在思考..."（那一句**既是加载态也是
 * 空答案态**，见 QA.tsx 的注释），扫出来的就是一堆加载骨架。
 * 于是 `qa` 场景要先真的收到一次流式回答才谈得上"有内容可审"。
 *
 * 事件名与数据形状取自 `api/client.ts:441-449` 的契约注释，
 * 解析器是 `utils/sse.ts`（`event:` + `data:`，事件间空行分隔）。
 */
export function rawBody(body: string, contentType = 'text/event-stream') {
  return { __raw: { body, contentType } };
}

/**
 * 学习目标的**场景级覆盖**（`learning-goals` 场景用）。
 *
 * 为什么不是默认桩：`Dashboard` 也请求 `/api/goals?status=active`
 * （Dashboard.tsx:66），把"有活跃目标"放进默认桩会连带改变**已扫的仪表盘场景**的
 * 渲染结果 —— 覆盖只作用于本次 `installA11yStubs` 调用，基线不动。
 *
 * 两个键**都带查询串**（见 `queryKey`）：`LearningGoals` 用同一路径取两份数据，
 * 只按路径名匹配会让"已归档目标"里显示进行中的目标 —— 页面照常渲染、
 * 断言照常绿，但那一块从未被真正判过。
 */
export const GOAL_STUBS: Record<string, unknown> = {
  '/api/goals?status=active': { goals: ACTIVE_GOALS, total: ACTIVE_GOALS.length },
  '/api/goals?status=archived': { goals: ARCHIVED_GOALS, total: ARCHIVED_GOALS.length },
};

/**
 * 学习评估页（`learning-assessment` 场景）用的 `/api/notes` 覆盖。
 *
 * `LearningAssessment` 请求**同一个路径两次**：一次全量、一次 `?note_role=personal_note`
 * （服务端过滤）。本文件的桩忽略查询串（见文件头），所以两次会拿到同一份 ——
 * 而页面在**客户端**还会再按 `note_role` 分一次（`LearningAssessment.tsx:152-153`），
 * 所以只要这份列表里同时有 material 与 personal_note，两条渲染路径都能拿到数据：
 *   - `compareLinkedPersonalNotes` 只保留**有 material 链接**的 personal_note，
 *     它的链接来自 `/api/notes/note-personal/links`（默认桩里已有）。
 *
 * 两条的 `status` 必须是可评估状态之一（converted / cleaned / archived /
 * learning / learning_failed），否则会被 `LearningAssessment.tsx:82-83` 过滤掉，
 * 页面渲染成"暂无已链接的笔记"，而那是**空状态**、不是被审过的页面。
 */
export const ASSESSMENT_NOTES = notesList([
  note(),
  note({
    id: 'note-personal',
    title: '复盘：浮充的三个月',
    source_type: 'markdown',
    note_role: 'personal_note',
    project_names: [],
    page_count: null,
  }),
]);

/** 审计期间观察到的网络事实（按引用读取，随页面活动增长） */
export interface A11yStubLog {
  /** 页面未捕获异常（`pageerror`） */
  pageErrors: string[];
  /**
   * 桩没有定义、因而被回 501 的 `/api` 路径。
   *
   * 为什么必须收集：这些请求在页面里**大概率被 `catch` 吞掉**
   * （前端对统计类接口普遍写 `.catch(() => null)`），页面会带着"部分数据为空"
   * 继续渲染 —— 审计照样绿，但扫的不是完整页面。把它断言为空，
   * 才能保证"审计覆盖了这些区域"这句话是真的。
   */
  unmatched: string[];
  /** 桩处理过的每个 `/api` 请求（路径 + 回的状态码），用于排查"页面卡在加载中" */
  served: string[];
  /** 浏览器控制台的 error/warning（排查渲染卡住时的第一手线索） */
  consoleErrors: string[];
}

/**
 * 排查开关：默认关闭，需要时把下面的 `false` 改成 `true` 再跑一次
 * （`npm run a11y`），桩处理的每个请求会打到测试输出。
 *
 * 只在**排查"页面卡在加载中"**时用：它直接回答"请求有没有到桩、回的哪个接口"，
 * 比从页面症状反推快得多（本项目真的在 8 worker 并发下卡过一次）。
 *
 * 为什么不做成环境变量：`tsconfig.json` 的 `include` 里有 `e2e`，而
 * `lib` 只有 ES2020 + DOM —— 没有 `@types/node`，写 `process` 会让
 * `npm run build`（= `tsc`）报 TS2580。为一行排查代码去加一个类型依赖不划算。
 */
const TRACE = false;

/**
 * 把查询串规范化成"可作 key"的形式：参数按名字排序，值原样保留。
 *
 * 用来支持**带查询串的桩键**（`'/api/goals?status=active'`）。为什么需要：
 * 有几个页面用**同一个路径 + 不同查询串**取两份不同的数据 ——
 * `LearningGoals` 的 `/api/goals?status=active` 与 `?status=archived`、
 * `LearningAssessment` 的 `/api/notes` 与 `/api/notes?note_role=personal_note`。
 * 只按路径名匹配时两份请求会拿到同一份响应，页面照常渲染、断言照常绿，
 * 但"已归档目标"里显示的是进行中的目标 —— 那正是本文件反复强调的那类
 * **看起来扫过了、其实扫的不是那一块**。排序是为了让键与参数书写顺序无关。
 */
function queryKey(search: string): string {
  if (!search || search === '?') return '';
  const params = [...new URLSearchParams(search).entries()].sort(([a], [b]) =>
    a < b ? -1 : a > b ? 1 : 0,
  );
  return `?${params.map(([k, v]) => `${k}=${v}`).join('&')}`;
}

/**
 * 装好审计需要的全部桩：第三方外链 abort + `/api` 覆写。
 *
 * 必须在 `page.goto` **之前**调用（外链的首个请求已经发出去就拦不住了，
 * 理由见 `support.ts` 文件头）。
 *
 * @param overrides - 按路径覆盖个别响应。审计**页面状态**（例如"登录失败态"）
 *   时需要它：那些状态由某个接口的特定响应触发，而默认桩为了能进到应用里，
 *   给的都是成功响应。覆盖只作用于本次调用，不会污染其他场景。
 */
export async function installA11yStubs(
  page: Page,
  overrides: Record<string, unknown> = {},
): Promise<A11yStubLog> {
  const log: A11yStubLog = {
    pageErrors: collectPageErrors(page),
    unmatched: [],
    served: [],
    consoleErrors: [],
  };
  const fixtures = { ...apiFixtures(), ...overrides };

  page.on('console', (message) => {
    if (message.type() === 'error' || message.type() === 'warning') {
      log.consoleErrors.push(`[${message.type()}] ${message.text()}`);
    }
  });

  // 外链一律 abort：`index.html` 的 Google Fonts 会阻塞首屏渲染，
  // 断网时表现为"元素一直不出来"，与真实原因无关（support.ts 文件头）
  await blockThirdParty(page);

  await page.route(
    (url) => isApiUrl(url),
    async (route) => {
      const { pathname, search } = new URL(route.request().url());
      // 先试"路径 + 规范化查询串"，再退回"只按路径"（见 queryKey 的说明）：
      // 既有的桩键全部只有路径，行为与以前逐字相同。
      const body = fixtures[`${pathname}${queryKey(search)}`] ?? fixtures[pathname];

      if (body === undefined) {
        // 见文件头：未知路径显式失败，不静默兜空。
        // 记的是**带查询串**的完整路径：同一个 pathname 配不同查询串时，
        // 只记 pathname 反而看不出少的是哪一份（例如 /goals?status=archived）。
        const missing = `${pathname}${search}`;
        log.unmatched.push(missing);
        log.served.push(`501 ${missing}`);
        if (TRACE) console.log(`   [stub] 501 ${missing}（未定义）`);
        await route.fulfill({
          status: 501,
          contentType: 'application/json',
          body: JSON.stringify({
            detail: `e2e a11y 桩没有为 ${missing} 定义响应（请补进 e2e/a11y-fixtures.ts）`,
          }),
        });
        return;
      }

      // 原样回文本（SSE）：见 rawBody 的说明
      if (typeof body === 'object' && body !== null && '__raw' in body) {
        const raw = (body as { __raw: { body: string; contentType: string } }).__raw;
        log.served.push(`200 ${pathname}`);
        if (TRACE) console.log(`   [stub] 200 ${pathname}（原样 ${raw.contentType}）`);
        await route.fulfill({ status: 200, contentType: raw.contentType, body: raw.body });
        return;
      }

      const status =
        typeof body === 'object' && body !== null && '__status' in body
          ? Number((body as { __status: number }).__status)
          : 200;

      log.served.push(`${status} ${pathname}`);
      if (TRACE) console.log(`   [stub] ${status} ${pathname}`);

      await route.fulfill({
        status,
        contentType: 'application/json',
        body: JSON.stringify(body),
      });
    },
  );

  return log;
}

/**
 * 登录并落在已登录外壳里，然后进入 `path`。
 *
 * ## 为什么先登录、**再**走客户端路由
 *
 * 未登录时任何路径都由 `App.tsx` 的 `path="*"` 渲染成**登录页**（这是产品行为），
 * 而登录成功后 `Login.tsx` 一律 `navigate('/')` —— 也就是说
 * **"打开 /graph → 登录 → 停在该地址"这条路在真实产品里不存在**：
 * 用户点完登录会落到仪表盘。所以桩里若直接 `goto('/graph')` 再登录，
 * 断言"图谱工具栏出现"必然超时，而这不是缺陷、只是走错了路。
 *
 * 这里走的是用户的真实次序：登录 → 落到仪表盘 → 从外壳里点进目标页。
 * 用 `history.pushState` + `popstate` 触发同一次客户端路由，
 * 避免整页刷新（刷新会重新走一遍 AuthContext 初始化，虽然也能到达，
 * 但那不是"用户点进来"的路径）。
 */
export async function loginAs(page: Page, path = '/'): Promise<void> {
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await page.locator('#email').fill('e2e@example.com');
  await page.locator('#password').fill('secret123');
  await page.getByRole('button', { name: '登录' }).click();
  // 侧边栏只在已登录分支渲染：它是"登录真的成功了"的判据
  await page.getByRole('navigation', { name: '主导航' }).waitFor({ state: 'visible' });

  if (path !== '/') {
    await page.evaluate((to) => {
      window.history.pushState({}, '', to);
      window.dispatchEvent(new PopStateEvent('popstate'));
    }, path);
    await page.waitForURL((url) => url.pathname === path);
  }
}
