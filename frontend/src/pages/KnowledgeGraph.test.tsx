/**
 * @file 知识图谱页的表征测试（overhaul-plan 阶段 5.5 的前置条件）
 *
 * ## 为什么先写测试再拆页面
 *
 * `KnowledgeGraph.tsx` 有 1005 行、**一行测试都没有**。5.5 要把它拆到 300 行以下，
 * 而这一页的状态有二十多个（选中节点/选中边/创建关系两步/搜索/过滤/子图/批量选择/
 * 悬停/侧边栏开合…），拆分时漏传一个回调、把「清空选中」的时机写错，都不会有任何
 * 东西告诉我。这里先把**拆坏了后果最严重**的行为固定下来：
 *
 * | 行为 | 拆坏了会怎样 |
 * |---|---|
 * | 加载/空/失败三种状态各有明确界面 | 白屏，用户不知道发生了什么 |
 * | 失败后「重试」真的重新拉取 | 错误页变成死路 |
 * | 回收站节点与其边不进画布 | force-graph 生成幽灵节点，图与数据对不上 |
 * | 类型过滤保留邻居节点 | 图被切碎，看不出结构 |
 * | 搜索防抖 → 点结果 → 聚焦该节点 | 搜到了却点不动 |
 * | 点节点/边 → 侧边栏详情 → 确认建议 | 图谱退化成一张只能看的图 |
 * | 画布绘制回调对残缺坐标/端点不抛错 | canvas 抛错 → 整页被错误边界接走 |
 *
 * ## 画布库为什么必须 mock
 *
 * `react-force-graph-2d` 依赖真实 canvas 布局与 WebGL 级别的尺寸测量，jsdom 里没有
 * 布局引擎（`getContext('2d')` 直接返回 null），整库跑不起来。这里用**最小替身**顶掉：
 * 它渲染节点/边的 id 列表与可点击按钮，并把收到的 props 原样交给测试 —— 于是
 * 「页面交给画布的是什么数据」「点和边被点击后页面做什么」都能断言，
 * 而且**真实的 canvas 绘制回调**（nodeCanvasObject / linkCanvasObject）仍然被调用，
 * 崩溃高发路径照样覆盖。只有图表内部布局本身没被测（那属于库的职责）。
 *
 * ## 关于「★ 契约漂移」用例
 *
 * 本文件用真实 `ErrorBoundary`（与 App.tsx 一致）包住页面。文件末尾那一组**曾经**
 * 故意断言页面崩到错误边界（记录当时的真实行为，见附录 AZ）。缺陷修好后它们已全部
 * 翻成**正向断言**：喂残缺数据时页面必须给出明确的降级界面（空状态 / 空列表 /
 * 可读占位），而不是白屏。断言里的「没有崩到错误边界」只是必要条件 ——
 * 每条用例都另外钉死了用户实际看到的东西，删掉守卫就会变红（附录 BA 记录变异验证）。
 */
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import {
  afterAll,
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
  type MockInstance,
} from 'vitest';

import type { GraphData, GraphNode, GraphStats, SuggestedRelation } from '../api/client';
import ErrorBoundary from '../components/ErrorBoundary';
import { ConfirmProvider } from '../components/ConfirmProvider';
import type { ForceGraphLink, ForceGraphNode } from '../components/graph/types';
import {
  GRAPH_CANVAS_TOKEN_FALLBACKS,
  getRelationDash,
  resetGraphCanvasTokensCache,
} from '../components/graph/types';
// 图谱的类名归模块所有（overhaul-plan 5.6 序 10）：字面量 `.graph-panel` 之流
// 在产物里是哈希过的，必然查不到。这里改不动语义查询的几处（面板/建议卡片/
// 结果项都是纯展示 div，没有角色可查，为测试加 role 属于产品改动），
// 所以按规范 §6 的第二种做法用模块导出的类名 —— 与 `NoteAskPanel.test.tsx`
// （`.ask-ai-panel`）、`StatCard.test.tsx` 同一处理。
// 能改成语义查询的都已改：搜索框用的是 `getByPlaceholderText('搜索卡片...')`。
import graphStyles from '../components/graph/Graph.module.css';

// ── 画布库替身：捕获 props、暴露可点击的节点/边、提供与真实库同形的 ref API ──
interface ForceGraphMockProps {
  graphData: { nodes: ForceGraphNode[]; links: ForceGraphLink[] };
  nodeCanvasObject: (node: ForceGraphNode, ctx: CanvasRenderingContext2D, scale: number) => void;
  linkCanvasObject: (link: ForceGraphLink, ctx: CanvasRenderingContext2D, scale: number) => void;
  onNodeClick: (node: ForceGraphNode) => void;
  onNodeHover: (node: ForceGraphNode | null) => void;
  onLinkClick: (link: ForceGraphLink) => void;
  onLinkHover: (link: ForceGraphLink | null) => void;
  onBackgroundClick: () => void;
  /** 批次 E4：箭头长度改成"按类型/状态取值"的函数（相关/对比与待审边不画箭头） */
  linkDirectionalArrowLength: number | ((link: ForceGraphLink) => number);
}

const fg = vi.hoisted(() => ({
  props: null as ForceGraphMockProps | null,
  // 与真实库同形的命令式 API；必须是**同一个** spy 实例，否则每次重渲都会换新对象、
  // 把"聚焦时调用过 zoom"的记录丢掉
  api: {
    centerAt: vi.fn(),
    zoom: vi.fn(),
    zoomToFit: vi.fn(),
  },
}));

vi.mock('react-force-graph-2d', async () => {
  const { createElement, forwardRef, useImperativeHandle } = await import('react');
  const Mock = forwardRef<unknown, ForceGraphMockProps>((props, ref) => {
    fg.props = props;
    useImperativeHandle(ref, () => fg.api, []);
    const { nodes, links } = props.graphData;
    return createElement(
      'div',
      { 'data-testid': 'force-graph' },
      createElement('span', { 'data-testid': 'fg-node-ids' }, nodes.map((n) => n.id).join(',')),
      createElement('span', { 'data-testid': 'fg-link-ids' }, links.map((l) => l.id).join(',')),
      // 画布空白处：真实库里是 onBackgroundClick
      createElement('button', {
        key: 'background',
        type: 'button',
        'data-testid': 'fg-background',
        onClick: () => props.onBackgroundClick(),
      }),
      nodes.map((n) =>
        createElement('button', {
          key: `n-${n.id}`,
          type: 'button',
          'data-testid': `fg-node-${n.id}`,
          onClick: () => props.onNodeClick(n),
        }),
      ),
      links.map((l) =>
        createElement('button', {
          key: `l-${l.id}`,
          type: 'button',
          'data-testid': `fg-link-${l.id}`,
          onClick: () => props.onLinkClick(l),
        }),
      ),
    );
  });
  return { default: Mock };
});

// ── mock 掉整条 API 层：本文件测的是页面行为，不是接口契约 ──
vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client');
  return {
    ...actual,
    getGraphData: vi.fn(),
    getSuggestions: vi.fn(),
    getGraphStats: vi.fn(),
    suggestRelations: vi.fn(),
    confirmRelation: vi.fn(),
    rejectRelation: vi.fn(),
    createRelation: vi.fn(),
    deleteRelation: vi.fn(),
    searchGraphNodes: vi.fn(),
    getNodeSubgraph: vi.fn(),
    batchConfirmRelations: vi.fn(),
    batchRejectRelations: vi.fn(),
  };
});

// toast 用可断言的替身（确认/拒绝失败必须报到用户面前，不能静默吞掉）
const toast = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  info: vi.fn(),
  warning: vi.fn(),
  show: vi.fn(),
  dismiss: vi.fn(),
}));
vi.mock('../components/Toast', () => ({ useToast: () => toast }));

import {
  batchConfirmRelations,
  confirmRelation,
  createRelation,
  getGraphData,
  getGraphStats,
  getNodeSubgraph,
  getSuggestions,
  searchGraphNodes,
  suggestRelations,
} from '../api/client';
import KnowledgeGraph from './KnowledgeGraph';
import { resetContractDriftNotices } from './contractDrift';

const mockedGraphData = vi.mocked(getGraphData);
const mockedSuggestions = vi.mocked(getSuggestions);
const mockedStats = vi.mocked(getGraphStats);
const mockedSearch = vi.mocked(searchGraphNodes);
const mockedSubgraph = vi.mocked(getNodeSubgraph);
const mockedConfirm = vi.mocked(confirmRelation);
const mockedCreate = vi.mocked(createRelation);
const mockedSuggest = vi.mocked(suggestRelations);
const mockedBatchConfirm = vi.mocked(batchConfirmRelations);

// ── 假 canvas 2D 上下文：drawMinimap 会真的画縮略图，这里只记录调用 ──
interface FakeCtx {
  [key: string]: unknown;
}

/** 最近一次由 getContext 创建的上下文（minimap 用），供断言绘制确实发生 */
let lastCanvasCtx: FakeCtx | null = null;

/**
 * 真实 canvas 对**非有限数值**并不宽容（`createRadialGradient` 等直接抛 TypeError），
 * 而 force 布局刚起步时节点坐标就是 undefined/NaN。替身在这里同样抛错，
 * 好让页面里"跳过无效坐标"的守卫真的被测试压住 —— 否则一个照单全收的假上下文
 * 会让那几行守卫形同虚设（删掉也测不出来）。
 */
function assertFinite(method: string, args: unknown[]) {
  if (args.some((v) => typeof v === 'number' && !Number.isFinite(v))) {
    throw new TypeError(`canvas.${method}: 传入的数值不是有限数`);
  }
}

function makeCtx(): FakeCtx {
  const gradient = { addColorStop: vi.fn() };
  const ctx: FakeCtx = {
    beginPath: vi.fn(),
    closePath: vi.fn(),
    moveTo: vi.fn((...args: unknown[]) => assertFinite('moveTo', args)),
    lineTo: vi.fn((...args: unknown[]) => assertFinite('lineTo', args)),
    arc: vi.fn((...args: unknown[]) => assertFinite('arc', args)),
    quadraticCurveTo: vi.fn((...args: unknown[]) => assertFinite('quadraticCurveTo', args)),
    fill: vi.fn(),
    stroke: vi.fn(),
    save: vi.fn(),
    restore: vi.fn(),
    clearRect: vi.fn(),
    fillRect: vi.fn(),
    strokeRect: vi.fn((...args: unknown[]) => assertFinite('strokeRect', args)),
    setLineDash: vi.fn(),
    fillText: vi.fn(),
    measureText: vi.fn(() => ({ width: 20 })),
    roundRect: vi.fn((...args: unknown[]) => assertFinite('roundRect', args)),
    createRadialGradient: vi.fn((...args: unknown[]) => {
      assertFinite('createRadialGradient', args);
      return gradient;
    }),
  };
  lastCanvasCtx = ctx;
  return ctx;
}

/**
 * jsdom 不实现 canvas 2D 上下文（返回 null 并打一条 "Not implemented"）。
 * 这里补一个**只记录调用**的替身，好让 drawMinimap 走完整条路径 ——
 * 它正是「节点还没有坐标时会不会抛错」的那段代码。
 */
const originalGetContext = HTMLCanvasElement.prototype.getContext;
beforeAll(() => {
  HTMLCanvasElement.prototype.getContext = (() =>
    makeCtx()) as unknown as typeof HTMLCanvasElement.prototype.getContext;
});
afterAll(() => {
  HTMLCanvasElement.prototype.getContext = originalGetContext;
});

// ── 夹具 ──
function makeNode(over: Partial<GraphNode> = {}): GraphNode {
  return {
    id: 'c-1',
    title: '浮充的定义',
    card_type: 'concept',
    note_id: 'note-aaaa1111',
    relation_count: 3,
    // 阶段 5.1 / S2：`note_trashed` 在后端带默认值（false）→ 生成类型里是**必填**。
    // 这条字段是"所属笔记是否在回收站、渲染时过滤掉"的判据，工厂必须显式给出。
    note_trashed: false,
    ...over,
  };
}

function makeForce(over: Partial<ForceGraphNode> = {}): ForceGraphNode {
  return { ...makeNode(), ...over };
}

/** 四张卡片：两个概念互为邻居，公式与概念相连，问答孤立（用于过滤器断言） */
function makeGraph(): GraphData {
  return {
    nodes: [
      makeNode({ id: 'c-1', title: '浮充的定义', card_type: 'concept', relation_count: 3 }),
      makeNode({ id: 'c-2', title: '均充的定义', card_type: 'concept', relation_count: 1 }),
      makeNode({ id: 'c-3', title: '浮充电压公式', card_type: 'formula', relation_count: 1 }),
      makeNode({ id: 'c-4', title: '浮充与均充的区别', card_type: 'qa', relation_count: 0 }),
    ],
    edges: [
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
    ],
  };
}

function makeStats(over: Partial<GraphStats> = {}): GraphStats {
  return {
    total_nodes: 4,
    total_edges: 2,
    confirmed_edges: 1,
    suggested_edges: 1,
    relation_type_distribution: [
      { relation_type: 'related', count: 1 },
      { relation_type: 'prerequisite', count: 1 },
    ],
    isolated_nodes: 1,
    ...over,
  };
}

function makeSuggestion(over: Partial<SuggestedRelation> = {}): SuggestedRelation {
  return {
    id: 's-1',
    card_id_1: 'c-1',
    card_id_2: 'c-2',
    card_1_title: '浮充的定义',
    card_2_title: '均充的定义',
    similarity_score: 0.87,
    ...over,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

function renderPage() {
  return render(
    // 批次 D3 后半：`useGraphMutations` 的两次确认改走 `useConfirm()`，
    // 渲染树里必须有这个宿主（层级与 main.tsx 一致）
    <ConfirmProvider>
      <MemoryRouter initialEntries={['/graph']}>
        {/* 与 App.tsx 一致：页面被路由级错误边界包住 */}
        <ErrorBoundary resetKey="/graph">
          <KnowledgeGraph />
        </ErrorBoundary>
      </MemoryRouter>
    </ConfirmProvider>,
  );
}

/** jsdom 没有真实视口，`window.innerWidth` 是唯一能决定"窄屏还是宽屏"的入口 */
const DEFAULT_VIEWPORT_WIDTH = 1024;

function setViewportWidth(width: number) {
  Object.defineProperty(window, 'innerWidth', { configurable: true, writable: true, value: width });
}

/**
 * 断言「整页没有崩到错误边界」。单靠它是不够的（页面可能是空白）——
 * 契约漂移用例必须同时断言**降级后用户实际看到的东西**。
 */
function expectNoCrash() {
  expect(screen.queryByText('这个页面出错了')).not.toBeInTheDocument();
}

function canvasProps(): ForceGraphMockProps {
  if (!fg.props) throw new Error('画布尚未渲染：先等待页面加载完成');
  return fg.props;
}

/** 画布区域（批次 E4：可聚焦 + `role="application"`，键盘切换当前节点的入口） */
function canvasRegion(): HTMLElement {
  return screen.getByRole('application', { name: '知识图谱画布' });
}

/**
 * 键盘切换的可读反馈行（`aria-live`）。
 *
 * 为什么按这个属性找而不是按文本：canvas 里没有 DOM 语义，节点名**只**出现在
 * 这一行与详情面板里；用 `getByText(/当前节点/)` 会把画布的用法说明也匹配进来。
 */
function graphStatusLine(): HTMLElement {
  const el = document.querySelector('[aria-live="polite"][aria-atomic="true"]');
  if (!el) throw new Error('页面上没有 aria-live 状态行：键盘切换的播报丢了');
  return el as HTMLElement;
}

/** 节点详情面板（与既有用例同一取法） */
function nodeDetailPanel(): HTMLElement {
  return screen.getByText('节点详情').closest(`.${graphStyles.graphPanel}`) as HTMLElement;
}

/** 一次绘制里出现过的 `setLineDash` 图案（顺序即调用顺序） */
function dashPatterns(ctx: FakeCtx): unknown[] {
  const spy = ctx.setLineDash as unknown as MockInstance;
  return spy.mock.calls.map((call: unknown[]) => call[0]);
}

beforeEach(() => {
  fg.props = null;
  lastCanvasCtx = null;
  // canvas 的水墨令牌是"读一次并缓存"的模块级状态：不清的话，前面用例读到的
  // 回退值会漏给后面那些要模拟"读得到令牌"的用例（批次 E4）
  resetGraphCanvasTokensCache();
  // 每个用例都从宽屏开始：侧边栏的默认开合取决于视口宽度，残留的窄屏值会让
  // 后面的用例莫名其妙地"少了统计面板"。窄屏用例自己改，改完由这里复位。
  setViewportWidth(DEFAULT_VIEWPORT_WIDTH);
  // 漂移提示的去重键是模块级状态、跨用例存活：不清的话，前面喂过漂移数据的用例
  // 会把后面用例的提示去重掉，测出来是"提示没出现"的假红
  resetContractDriftNotices();
  mockedGraphData.mockResolvedValue(makeGraph());
  mockedStats.mockResolvedValue(makeStats());
  mockedSuggestions.mockResolvedValue([]);
  mockedSearch.mockResolvedValue({ items: [], total: 0 });
  mockedSubgraph.mockResolvedValue({
    center_node: makeNode({ id: 'c-1', title: '浮充的定义' }),
    neighbor_nodes: [],
    edges: [],
  });
  // 阶段 5.1 / S2：这三处 mock 的返回值此前是"最小形状"（只有 success）。
  // 生成类型把每个端点**自己**的固定形状钉住了：关系操作是
  // `{success, relation_id}`，批量操作是 `{success, confirmed_count, failed_count, rejected_count}`。
  mockedConfirm.mockResolvedValue({ success: true, relation_id: 'e-1' });
  mockedCreate.mockResolvedValue({ success: true, relation_id: 'e-new' });
  mockedSuggest.mockResolvedValue({ success: true, new_count: 0 });
  mockedBatchConfirm.mockResolvedValue({
    success: true,
    confirmed_count: 0,
    failed_count: 0,
    rejected_count: 0,
  });
});

describe('加载与状态展示', () => {
  it('加载中显示加载提示，加载完成后展示图谱本体', async () => {
    const pending = deferred<GraphData>();
    mockedGraphData.mockImplementation(() => pending.promise);
    renderPage();

    // 请求还没回来：用户看到的必须是"在加载"，而不是空白页
    expect(screen.getByText('加载知识图谱...')).toBeInTheDocument();

    await act(async () => {
      pending.resolve(makeGraph());
    });

    expect(await screen.findByText('知识图谱')).toBeInTheDocument();
    expect(screen.queryByText('加载知识图谱...')).not.toBeInTheDocument();
  });

  it('★ 加载成功后画布拿到真实节点与边，顶部计数与统计面板一致', async () => {
    mockedSuggestions.mockResolvedValue([makeSuggestion()]);
    renderPage();

    expect(await screen.findByText('知识图谱')).toBeInTheDocument();
    // 交给力导向图的必须是这四条节点、两条边（顺序即渲染顺序）
    expect(screen.getByTestId('fg-node-ids')).toHaveTextContent('c-1,c-2,c-3,c-4');
    expect(screen.getByTestId('fg-link-ids')).toHaveTextContent('e-1,e-2');
    // 计数文案：4 节点 · 2 边 · 1 待审
    const meta = screen.getByText(/4 节点/);
    expect(meta.textContent).toContain('4 节点');
    expect(meta.textContent).toContain('2 边');
    expect(meta.textContent).toContain('1 待审');
    // 统计面板真的拿到了 /graph/stats 的数字（不是空壳）
    expect(screen.getByText('图谱统计')).toBeInTheDocument();
    expect(screen.getByText('待确认')).toBeInTheDocument();
    expect(screen.getByText('孤立节点')).toBeInTheDocument();
    // 待审建议数出现在「建议」按钮的徽标上
    expect(screen.getByRole('button', { name: /建议/ })).toHaveTextContent('1');
  });

  it('★ 图谱为空时给出可操作的说明，而不是空白画布', async () => {
    mockedGraphData.mockResolvedValue({ nodes: [], edges: [] });
    renderPage();

    expect(await screen.findByText('暂无图谱数据')).toBeInTheDocument();
    expect(
      screen.getByText(/请先上传笔记并触发理解管道，生成知识卡片后即可查看图谱/),
    ).toBeInTheDocument();
  });

  it('★ 接口失败时显示错误与重试入口，而不是白屏（错误信息如实展示）', async () => {
    mockedGraphData.mockRejectedValue(new Error('图谱服务不可用'));
    mockedStats.mockRejectedValue(new Error('图谱服务不可用'));
    mockedSuggestions.mockRejectedValue(new Error('图谱服务不可用'));
    renderPage();

    expect(await screen.findByText('图谱服务不可用')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument();
  });

  it('★ 点「重试」会重新拉取，成功后错误页让位给图谱', async () => {
    let attempt = 0;
    mockedGraphData.mockImplementation(async () => {
      attempt += 1;
      if (attempt === 1) throw new Error('图谱服务不可用');
      return makeGraph();
    });
    renderPage();
    await screen.findByText('图谱服务不可用');

    await userEvent.click(screen.getByRole('button', { name: '重试' }));

    expect(await screen.findByText('知识图谱')).toBeInTheDocument();
    expect(mockedGraphData).toHaveBeenCalledTimes(2);
    expect(screen.queryByText('图谱服务不可用')).not.toBeInTheDocument();
  });
});

describe('筛选与画布数据装配', () => {
  it('★ 回收站里的笔记对应的节点（及其边）不进画布', async () => {
    mockedGraphData.mockResolvedValue({
      nodes: [
        makeNode({ id: 'c-1' }),
        makeNode({ id: 'c-2', title: '均充的定义' }),
        makeNode({ id: 'c-9', title: '已进回收站的卡片', note_trashed: true }),
      ],
      edges: [
        {
          id: 'e-1',
          source: 'c-1',
          target: 'c-2',
          relation_type: 'related',
          status: 'confirmed',
          similarity_score: 0.8,
        },
        {
          id: 'e-9',
          source: 'c-2',
          target: 'c-9',
          relation_type: 'related',
          status: 'confirmed',
          similarity_score: 0.7,
        },
      ],
    });
    renderPage();

    await screen.findByText('知识图谱');
    expect(screen.getByTestId('fg-node-ids')).toHaveTextContent('c-1,c-2');
    expect(screen.getByTestId('fg-node-ids')).not.toHaveTextContent('c-9');
    // 指向回收站节点的边必须一起剔除，否则 force-graph 会生成幽灵节点
    expect(screen.getByTestId('fg-link-ids')).toHaveTextContent('e-1');
    expect(screen.getByTestId('fg-link-ids')).not.toHaveTextContent('e-9');
  });

  it('★ 边指向不存在的节点时被剔除（脏数据不再生成幽灵节点）', async () => {
    mockedGraphData.mockResolvedValue({
      nodes: [makeNode({ id: 'c-1' }), makeNode({ id: 'c-2', title: '均充的定义' })],
      edges: [
        {
          id: 'e-1',
          source: 'c-1',
          target: 'c-2',
          relation_type: 'related',
          status: 'confirmed',
          similarity_score: 0.8,
        },
        {
          id: 'e-ghost',
          source: 'c-1',
          target: '不存在',
          relation_type: 'related',
          status: 'confirmed',
          similarity_score: null,
        },
      ],
    });
    renderPage();

    await screen.findByText('知识图谱');
    expect(screen.getByTestId('fg-link-ids')).toHaveTextContent('e-1');
    expect(screen.getByTestId('fg-link-ids')).not.toHaveTextContent('e-ghost');
  });

  it('★ 类型过滤只保留该类型节点及其邻居，切回「全部类型」恢复', async () => {
    renderPage();
    await screen.findByText('知识图谱');
    expect(screen.getByTestId('fg-node-ids')).toHaveTextContent('c-1,c-2,c-3,c-4');

    await userEvent.selectOptions(screen.getByRole('combobox'), 'formula');

    // c-3（公式）加上与它相连的 c-1；孤立且类型不符的 c-4 必须消失
    expect(screen.getByTestId('fg-node-ids')).toHaveTextContent('c-1,c-3');
    expect(screen.getByTestId('fg-node-ids')).not.toHaveTextContent('c-4');
    expect(screen.getByTestId('fg-link-ids')).toHaveTextContent('e-2');

    await userEvent.selectOptions(screen.getByRole('combobox'), '');

    expect(screen.getByTestId('fg-node-ids')).toHaveTextContent('c-1,c-2,c-3,c-4');
  });

  it('★ minimap 在节点还没有坐标时照常重绘（force 布局过程中坐标可能是 undefined）', async () => {
    // makeGraph 的节点都没有 x/y —— 正是布局开始前的真实状态
    renderPage();
    await screen.findByText('知识图谱');

    await waitFor(() => expect(lastCanvasCtx?.fillRect).toHaveBeenCalled());
    expect(lastCanvasCtx?.strokeRect).toHaveBeenCalled(); // 视口框也画了
  });
});

describe('图谱交互', () => {
  it('★ 点节点打开节点详情面板，点「收起」把侧边栏收起来', async () => {
    renderPage();
    await screen.findByText('知识图谱');

    await userEvent.click(screen.getByTestId('fg-node-c-1'));

    const panel = screen.getByText('节点详情').closest(`.${graphStyles.graphPanel}`) as HTMLElement;
    expect(within(panel).getByText('浮充的定义')).toBeInTheDocument();
    expect(within(panel).getByText('概念')).toBeInTheDocument();
    expect(within(panel).getByRole('button', { name: '查看知识点详情' })).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: '收起' }));

    expect(screen.queryByText('节点详情')).not.toBeInTheDocument();
    expect(screen.queryByText('图谱统计')).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: '展开' }));

    expect(screen.getByText('图谱统计')).toBeInTheDocument();
  });

  it('★ 点画布空白处取消选中，详情面板随之关闭', async () => {
    renderPage();
    await screen.findByText('知识图谱');

    await userEvent.click(screen.getByTestId('fg-node-c-1'));
    expect(screen.getByText('节点详情')).toBeInTheDocument();

    await userEvent.click(screen.getByTestId('fg-background'));

    expect(screen.queryByText('节点详情')).not.toBeInTheDocument();
  });

  it('★ 搜索：防抖后按关键词请求，点结果聚焦到该节点', async () => {
    mockedSearch.mockResolvedValue({
      items: [
        {
          id: 'c-4',
          title: '浮充与均充的区别',
          card_type: 'qa',
          note_id: 'note-dddd4444',
          relation_count: 0,
        },
      ],
      total: 1,
    });
    renderPage();
    await screen.findByText('知识图谱');

    await userEvent.type(screen.getByPlaceholderText('搜索卡片...'), '区别');

    await waitFor(() => expect(mockedSearch).toHaveBeenCalledWith('区别', 15), { timeout: 2000 });
    const hit = await screen.findByText('浮充与均充的区别');
    // 结果项显示中文类型（"问答" 在类型下拉框里也有一份，所以必须限定在结果项内断言）
    const item = hit.closest(`.${graphStyles.graphSearchResultItem}`) as HTMLElement;
    expect(within(item).getByText('问答')).toBeInTheDocument();

    await userEvent.click(hit);

    // 聚焦 = 画布真的被指挥去居中/放大 + 节点详情跟着切过去
    expect(fg.api?.zoom).toHaveBeenCalledWith(3, 400);
    const panel = screen.getByText('节点详情').closest(`.${graphStyles.graphPanel}`) as HTMLElement;
    expect(within(panel).getByText('浮充与均充的区别')).toBeInTheDocument();
  });

  it('★ 搜索接口返回残缺结构时不崩，也不残留旧结果', async () => {
    mockedSearch.mockResolvedValue({} as never);
    renderPage();
    await screen.findByText('知识图谱');

    await userEvent.type(screen.getByPlaceholderText('搜索卡片...'), '不存在');
    await waitFor(() => expect(mockedSearch).toHaveBeenCalled(), { timeout: 2000 });

    expect(screen.queryByText('这个页面出错了')).not.toBeInTheDocument();
    expect(screen.getByText('知识图谱')).toBeInTheDocument();
  });

  it('★ 点边看到关系详情，待审边能直接确认', async () => {
    renderPage();
    await screen.findByText('知识图谱');

    await userEvent.click(screen.getByTestId('fg-link-e-1'));

    const panel = screen.getByText('关系详情').closest(`.${graphStyles.graphPanel}`) as HTMLElement;
    expect(within(panel).getByText('相关')).toBeInTheDocument();
    expect(within(panel).getByText('建议')).toBeInTheDocument();
    expect(within(panel).getByText('0.82')).toBeInTheDocument();

    await userEvent.click(within(panel).getByRole('button', { name: '确认' }));

    expect(mockedConfirm).toHaveBeenCalledWith('e-1');
    // 确认后要重新拉图谱与统计，否则数字与图还是旧的
    await waitFor(() => expect(mockedGraphData).toHaveBeenCalledTimes(2));
    expect(mockedStats).toHaveBeenCalledTimes(2);
    expect(screen.queryByText('关系详情')).not.toBeInTheDocument();
  });

  it('★ 创建关系：连点两个节点后提交，调用 createRelation 并退出创建模式', async () => {
    renderPage();
    await screen.findByText('知识图谱');

    await userEvent.click(screen.getByRole('button', { name: '创建关系' }));
    expect(screen.getByText('请点击第一个节点')).toBeInTheDocument();

    await userEvent.click(screen.getByTestId('fg-node-c-1'));
    expect(screen.getByText('已选择: 浮充的定义，请点击第二个节点')).toBeInTheDocument();

    await userEvent.click(screen.getByTestId('fg-node-c-3'));
    await userEvent.click(screen.getByRole('button', { name: '确认创建' }));

    await waitFor(() => expect(mockedCreate).toHaveBeenCalledWith('c-1', 'c-3', 'related'));
    await waitFor(() => expect(screen.queryByText(/已选择/)).not.toBeInTheDocument());
    expect(mockedGraphData).toHaveBeenCalledTimes(2);
  });

  it('★ 建议面板：确认一条建议后它从列表消失，接口报错时如实提示且不假装成功', async () => {
    mockedSuggestions.mockResolvedValue([
      makeSuggestion({ id: 's-1' }),
      makeSuggestion({ id: 's-2', card_1_title: '浮充电压公式', card_2_title: '浮充与均充的区别' }),
    ]);
    mockedConfirm.mockRejectedValue(new Error('关系已被他人确认'));
    renderPage();
    await screen.findByText('知识图谱');

    await userEvent.click(screen.getByRole('button', { name: /建议/ }));
    expect(screen.getByText('建议关系 (2)')).toBeInTheDocument();

    const card = screen
      .getByText('浮充的定义')
      .closest(`.${graphStyles.graphSuggestionCard}`) as HTMLElement;
    await userEvent.click(within(card).getByRole('button', { name: '确认' }));

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('关系已被他人确认'));
    // 失败不能把建议从列表里抹掉（否则用户以为确认成功了）
    expect(screen.getByText('建议关系 (2)')).toBeInTheDocument();
    expect(screen.getByText('浮充电压公式')).toBeInTheDocument();
  });

  it('★ 批量确认：全选后一次提交所有建议 ID', async () => {
    mockedSuggestions.mockResolvedValue([
      makeSuggestion({ id: 's-1' }),
      makeSuggestion({ id: 's-2', card_1_title: '浮充电压公式', card_2_title: '浮充与均充的区别' }),
    ]);
    renderPage();
    await screen.findByText('知识图谱');
    await userEvent.click(screen.getByRole('button', { name: /建议/ }));

    const batchButton = screen.getByRole('button', { name: /批量确认/ });
    expect(batchButton).toBeDisabled(); // 没勾选时不该能点

    await userEvent.click(screen.getByRole('checkbox', { name: '全选' }));
    expect(screen.getByRole('button', { name: '批量确认 (2)' })).toBeEnabled();

    await userEvent.click(screen.getByRole('button', { name: '批量确认 (2)' }));

    await waitFor(() => expect(mockedBatchConfirm).toHaveBeenCalledWith(['s-1', 's-2']));
    expect(await screen.findByText(/暂无建议关系/)).toBeInTheDocument();
  });

  it('★ 只勾选一部分时，全选框显示 indeterminate（不能误显示成"已全选"）', async () => {
    mockedSuggestions.mockResolvedValue([
      makeSuggestion({ id: 's-1' }),
      makeSuggestion({ id: 's-2', card_1_title: '浮充电压公式', card_2_title: '浮充与均充的区别' }),
    ]);
    renderPage();
    await screen.findByText('知识图谱');
    await userEvent.click(screen.getByRole('button', { name: /建议/ }));

    const card = screen
      .getByText('浮充的定义')
      .closest(`.${graphStyles.graphSuggestionCard}`) as HTMLElement;
    await userEvent.click(within(card).getByRole('checkbox'));

    const selectAll = screen.getByRole('checkbox', { name: '全选' }) as HTMLInputElement;
    expect(selectAll.checked).toBe(false);
    expect(selectAll.indeterminate).toBe(true);
    expect(screen.getByRole('button', { name: '批量确认 (1)' })).toBeEnabled();

    // 再点全选 → 两条都选中，横杠状态消失
    await userEvent.click(selectAll);

    expect((screen.getByRole('checkbox', { name: '全选' }) as HTMLInputElement).indeterminate).toBe(
      false,
    );
    expect(screen.getByRole('button', { name: '批量确认 (2)' })).toBeEnabled();
  });

  it('★ 生成建议失败时在面板内显示原因（而不是静默什么都不发生）', async () => {
    mockedSuggest.mockRejectedValue(new Error('嵌入服务未就绪'));
    renderPage();
    await screen.findByText('知识图谱');
    await userEvent.click(screen.getByRole('button', { name: /建议/ }));

    await userEvent.click(screen.getByRole('button', { name: '生成相关建议' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('嵌入服务未就绪');
  });
});

/**
 * 侧边栏的**首屏默认值**（overhaul-plan 5.10 移动端补丁）：手机上侧边栏是固定底部抽屉
 * （`max-height: 50vh`），默认展开等于首屏就盖掉半张画布 —— 用户得先找到「收起」才看得见图。
 * 断点与 responsive.css 的 `@media (max-width: 768px)` 同值（两处不同值会出现
 * "CSS 当手机、JS 当桌面"的错位）。
 *
 * 桌面行为必须逐字不变：宽屏仍默认展开（下面第二条用例专门守这一点）。
 */
describe('侧边栏的首屏默认值（窄屏收起 / 宽屏展开）', () => {
  it('★ 窄屏（375px）默认收起，且「展开」仍然能打开它', async () => {
    setViewportWidth(375);
    renderPage();
    await screen.findByText('知识图谱');

    // 首屏不该有侧边栏内容（统计面板），按钮应显示「展开」
    expect(screen.queryByText('图谱统计')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '展开' })).toBeInTheDocument();

    // 改的只是默认值：开关本身照旧可用
    await userEvent.click(screen.getByRole('button', { name: '展开' }));
    expect(screen.getByText('图谱统计')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '收起' })).toBeInTheDocument();
  });

  it('★ 宽屏（1024px）仍默认展开（桌面行为逐字不变）', async () => {
    setViewportWidth(1024);
    renderPage();
    await screen.findByText('知识图谱');

    expect(screen.getByText('图谱统计')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '收起' })).toBeInTheDocument();
  });

  it('边界：正好 768px 算窄屏（与 CSS 的 max-width: 768px 同值，不差一个像素）', async () => {
    setViewportWidth(768);
    renderPage();
    await screen.findByText('知识图谱');

    expect(screen.queryByText('图谱统计')).not.toBeInTheDocument();
  });
});

/**
 * 宽容解析的另一半（overhaul-plan AZ.7）：形状不对时**必须有人知道**。
 *
 * 下面的漂移用例（以及文件末尾那一组）证明的是"喂残缺数据不白屏"，
 * 但也正是它们把真实的契约破坏吞掉了 —— 后端一直回 `{items:[…]}`，页面一直正常显示，
 * 没有任何地方会报错。这一组钉住：漂移浮出成一条非阻塞提示（全局 toast），
 * 且**正常响应一条都不报** —— 一个总在响的提示比没有提示更糟，用户会学会无视它。
 */
describe('契约漂移的可见性（既容忍又报出来）', () => {
  it('★ 包装载荷（/graph/suggestions 回 {items:[…]}）：既照常渲染又浮出提示', async () => {
    mockedSuggestions.mockResolvedValue({ items: [makeSuggestion()] } as never);
    renderPage();
    await screen.findByText('知识图谱');

    await userEvent.click(screen.getByRole('button', { name: /建议/ }));
    // 容忍照旧：建议照常渲染（数据其实在）
    expect(screen.getByText(/建议关系 \(1\)/)).toBeInTheDocument();

    // 提示浮出来，且能看出是哪个接口、什么形状
    await waitFor(() => expect(toast.warning).toHaveBeenCalledTimes(1));
    expect(String(toast.warning.mock.calls[0][0])).toContain('/graph/suggestions');
    expect(String(toast.warning.mock.calls[0][1])).toContain('items');
  });

  it('★ 非数组载荷（stats 的分布字段是对象）：面板降级的同时报出来', async () => {
    mockedStats.mockResolvedValue(
      makeStats({ relation_type_distribution: { related: 1 } as never }),
    );
    renderPage();
    await screen.findByText('知识图谱');

    // 降级照旧：只少一段条形图，四个基础数字还在
    expect(document.querySelector(`.${graphStyles.graphStatsBarRow}`)).toBeNull();
    expect(screen.getByText('待确认')).toBeInTheDocument();

    await waitFor(() => expect(toast.warning).toHaveBeenCalledTimes(1));
    expect(String(toast.warning.mock.calls[0][0])).toContain('/graph/stats');
    expect(String(toast.warning.mock.calls[0][1])).toContain('relation_type_distribution');
  });

  it('★ 正常响应一条提示都不报（最重要的反向断言）', async () => {
    renderPage();
    await screen.findByText('知识图谱');

    // 再走一次写操作后的重拉：加载路径与重拉路径都不该报警
    await userEvent.click(screen.getByTestId('fg-link-e-1'));
    const panel = screen.getByText('关系详情').closest(`.${graphStyles.graphPanel}`) as HTMLElement;
    await userEvent.click(within(panel).getByRole('button', { name: '确认' }));
    await waitFor(() => expect(mockedGraphData).toHaveBeenCalledTimes(2));

    // 搜索接口也一样：`{items:[…],total}` 里 items 是**约定字段**、不是包装对象，
    // 用错判据（把它当顶层数组契约）就会让每次正常搜索都弹一条无用提示
    await userEvent.type(screen.getByPlaceholderText('搜索卡片...'), '浮充');
    await waitFor(() => expect(mockedSearch).toHaveBeenCalled(), { timeout: 2000 });

    expect(toast.warning).not.toHaveBeenCalled();
  });

  it('★ 搜索接口返回残缺结构时也会报出来（本目录最后一处没走判据的列表赋值）', async () => {
    mockedSearch.mockResolvedValue({} as never);
    renderPage();
    await screen.findByText('知识图谱');

    await userEvent.type(screen.getByPlaceholderText('搜索卡片...'), '不存在');
    await waitFor(() => expect(mockedSearch).toHaveBeenCalled(), { timeout: 2000 });

    await waitFor(() => expect(toast.warning).toHaveBeenCalledTimes(1));
    expect(String(toast.warning.mock.calls[0][0])).toContain('/graph/search');
    expect(String(toast.warning.mock.calls[0][1])).toContain('items');

    // 退化行为不变：不崩，也不残留旧结果（GraphToolbar 的 length 检查照旧兜底）
    expect(screen.queryByText('这个页面出错了')).not.toBeInTheDocument();
    expect(screen.getByText('知识图谱')).toBeInTheDocument();
  });

  it('★ 去重：同一处漂移在反复重拉中只提示一次（确认后每次写操作都会重拉统计）', async () => {
    mockedStats.mockResolvedValue(makeStats({ relation_type_distribution: {} as never }));
    renderPage();
    await screen.findByText('知识图谱');
    await waitFor(() => expect(toast.warning).toHaveBeenCalledTimes(1));

    // 写操作后重拉统计：漂移又出现了一次，但提示不该再来一条
    await userEvent.click(screen.getByTestId('fg-link-e-1'));
    const panel = screen.getByText('关系详情').closest(`.${graphStyles.graphPanel}`) as HTMLElement;
    await userEvent.click(within(panel).getByRole('button', { name: '确认' }));
    await waitFor(() => expect(mockedStats).toHaveBeenCalledTimes(2));

    expect(toast.warning).toHaveBeenCalledTimes(1);
  });
});

/**
 * BB.8 第 1 条：8 处"重新拉取"抽成了 `refetchGraphAndStats(mode)`。
 * 合并的是重复**写法**，不是可观测**行为** —— 两种方式的调用顺序与失败行为不同
 * （顺序版：图谱失败就不拉统计；并发版：两个请求同时发出），所以两条路径各钉一条用例。
 * 把任何一条改成另一种写法，对应用例立刻变红。
 */
describe('写操作后重拉的调用顺序（抽取后必须逐字不变）', () => {
  it('★ 单条确认后的重拉是顺序 await：图谱请求还挂着时统计请求不发', async () => {
    const pendingGraph = deferred<GraphData>();
    renderPage();
    await screen.findByText('知识图谱');
    expect(mockedStats).toHaveBeenCalledTimes(1); // 首屏加载的统计

    mockedGraphData.mockImplementation(() => pendingGraph.promise);
    await userEvent.click(screen.getByTestId('fg-link-e-1'));
    const panel = screen.getByText('关系详情').closest(`.${graphStyles.graphPanel}`) as HTMLElement;
    await userEvent.click(within(panel).getByRole('button', { name: '确认' }));
    await waitFor(() => expect(mockedGraphData).toHaveBeenCalledTimes(2));

    // 图谱还没回来 → 统计请求**还没发**（合并成 Promise.all 就会立刻发出第二条）
    expect(mockedStats).toHaveBeenCalledTimes(1);

    await act(async () => {
      pendingGraph.resolve(makeGraph());
    });

    await waitFor(() => expect(mockedStats).toHaveBeenCalledTimes(2));
  });

  it('★ 批量确认后的重拉是 Promise.all：图谱请求还挂着时统计请求已经发出', async () => {
    // 批量按钮只在有两条以上建议时出现（见 GraphSidebar），所以这里给两条
    mockedSuggestions.mockResolvedValue([
      makeSuggestion({ id: 's-1' }),
      makeSuggestion({ id: 's-2', card_1_title: '浮充电压公式', card_2_title: '浮充与均充的区别' }),
    ]);
    const pendingGraph = deferred<GraphData>();
    renderPage();
    await screen.findByText('知识图谱');
    await userEvent.click(screen.getByRole('button', { name: /建议/ }));
    await userEvent.click(screen.getByRole('checkbox', { name: '全选' }));

    mockedGraphData.mockImplementation(() => pendingGraph.promise);
    await userEvent.click(screen.getByRole('button', { name: '批量确认 (2)' }));
    await waitFor(() => expect(mockedGraphData).toHaveBeenCalledTimes(2));

    // 图谱请求挂着，统计照样已经发出（被"顺手统一"成顺序 await 就会变红）
    expect(mockedStats).toHaveBeenCalledTimes(2);

    await act(async () => {
      pendingGraph.resolve(makeGraph());
    });
  });
});

/**
 * 崩溃高发路径：契约漂移 / 缓存旧结构 / 后端少返回一个字段。
 * 本项目已经因为「noteLinks.* 是 undefined 却直接迭代」白屏过一次，
 * 所以这里逐条喂残缺数据，钉死**降级行为**（空状态 / 空列表 / 可读占位），
 * 而不是"不抛异常就行"。
 */
describe('契约漂移时的健壮性（缺字段一律降级，不再崩到错误边界）', () => {
  let consoleError: MockInstance;

  beforeEach(() => {
    // 这些用例会故意触发渲染异常；React 与错误边界会打 console.error，静音以免淹没输出
    consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  afterEach(() => {
    consoleError.mockRestore();
  });

  it('安全：统计分布的条目缺 count、甚至整个字段是对象，都不崩', async () => {
    mockedStats.mockResolvedValue(
      makeStats({ relation_type_distribution: { related: 1 } as never }),
    );
    renderPage();

    expect(await screen.findByText('知识图谱')).toBeInTheDocument();
    expect(screen.queryByText('这个页面出错了')).not.toBeInTheDocument();
  });

  it('安全：建议条目缺标题/相似度时显示占位而不是崩', async () => {
    mockedSuggestions.mockResolvedValue([{ id: 's-1' } as never]);
    renderPage();
    await screen.findByText('知识图谱');

    await userEvent.click(screen.getByRole('button', { name: /建议/ }));

    expect(screen.getByText(/相似度: —/)).toBeInTheDocument();
    expect(screen.queryByText('这个页面出错了')).not.toBeInTheDocument();
  });

  it('安全：画布绘制回调对无坐标/NaN 坐标/未知类型/缺标题的节点不抛错', async () => {
    renderPage();
    await screen.findByText('知识图谱');
    const props = canvasProps();
    const ctx = makeCtx() as unknown as CanvasRenderingContext2D;

    // 力导向布局开始前坐标是 undefined —— 必须直接跳过，不能把 undefined 丢给 canvas
    expect(() =>
      props.nodeCanvasObject(makeForce({ x: undefined, y: undefined }), ctx, 1),
    ).not.toThrow();
    expect(() => props.nodeCanvasObject(makeForce({ x: NaN, y: NaN }), ctx, 1)).not.toThrow();
    // 未知卡片类型 / 缺标题 / 缺 relation_count：走兜底色与兜底形状
    // （`unknown-type` 不在 `CardType` 枚举里，正是本用例要喂的输入：
    //  后端枚举将来加了值、旧前端还没跟上时，画布不能抛错 —— 因此这里刻意绕过类型）
    expect(() =>
      props.nodeCanvasObject(
        makeForce({
          card_type: 'unknown-type' as never,
          title: undefined as never,
          relation_count: undefined as never,
          x: 5,
          y: 5,
        }),
        ctx,
        1,
      ),
    ).not.toThrow();
    expect(() => props.nodeCanvasObject(makeForce({ x: 5, y: 5 }), ctx, 1)).not.toThrow();
  });

  it('安全：画布绘制回调对「端点还是字符串」或半个对象的边不抛错', async () => {
    renderPage();
    await screen.findByText('知识图谱');
    const props = canvasProps();
    const ctx = makeCtx() as unknown as CanvasRenderingContext2D;

    const base = { relation_type: 'related', status: 'suggested', similarity_score: null };
    // force-graph 在布局完成前会把 source/target 作为字符串传进来
    expect(() =>
      props.linkCanvasObject({ id: 'e-x', source: 'c-1', target: 'c-2', ...base }, ctx, 1),
    ).not.toThrow();
    expect(() =>
      props.linkCanvasObject(
        { id: 'e-x', source: makeForce({ x: 1, y: 2 }), target: { x: 3 } as never, ...base },
        ctx,
        1,
      ),
    ).not.toThrow();
    expect(() =>
      props.linkCanvasObject(
        {
          id: 'e-x',
          source: makeForce({ x: 1, y: 2 }),
          target: makeForce({ id: 'c-2', x: 3, y: 4 }),
          relation_type: undefined as never,
          status: undefined as never,
          similarity_score: undefined as never,
        },
        ctx,
        1,
      ),
    ).not.toThrow();
  });

  it('nodes 字段缺失（后端只回 edges / 旧缓存结构）时按"暂无图谱数据"降级，不崩', async () => {
    mockedGraphData.mockResolvedValue({ edges: [] } as never);
    renderPage();

    // nodes 归一成空数组 → 走页面既有的空状态；画布不该拿到任何节点
    expect(await screen.findByText('暂无图谱数据')).toBeInTheDocument();
    expect(
      screen.getByText(/请先上传笔记并触发理解管道，生成知识卡片后即可查看图谱/),
    ).toBeInTheDocument();
    expectNoCrash();
    expect(fg.props).toBeNull(); // 空图谱不渲染画布，而不是渲染一张空画布
  });

  it('edges 字段缺失时画布与顶部计数都按 0 条边处理，不崩', async () => {
    mockedGraphData.mockResolvedValue({ nodes: [makeNode()] } as never);
    renderPage();

    expect(await screen.findByText('知识图谱')).toBeInTheDocument();
    // 节点照常进画布，边退化成空集（而不是 undefined.filter 抛错）
    expect(screen.getByTestId('fg-node-ids')).toHaveTextContent('c-1');
    expect(screen.getByTestId('fg-link-ids')).toBeEmptyDOMElement();
    const meta = screen.getByText(/1 节点/);
    expect(meta.textContent).toContain('1 节点');
    expect(meta.textContent).toContain('0 边');
    expectNoCrash();
  });

  it('stats.relation_type_distribution 缺失时统计面板仍渲染，只是没有分布条', async () => {
    mockedStats.mockResolvedValue(makeStats({ relation_type_distribution: undefined as never }));
    renderPage();

    expect(await screen.findByText('知识图谱')).toBeInTheDocument();
    // 四个基础数字照常显示（缺的是分布条，不是整个面板）
    expect(screen.getByText('图谱统计')).toBeInTheDocument();
    expect(screen.getByText('待确认')).toBeInTheDocument();
    expect(screen.getByText('孤立节点')).toBeInTheDocument();
    expect(document.querySelector(`.${graphStyles.graphStatsBarRow}`)).toBeNull();
    expectNoCrash();
  });

  it('安全：确认建议后重拉的统计里分布字段是对象时只少一段条形图，不崩（重拉路径同样过归一化）', async () => {
    const pending = deferred<GraphStats>();
    mockedStats.mockResolvedValueOnce(makeStats()); // 首屏统计是正常结构
    mockedStats.mockImplementation(() => pending.promise);
    renderPage();
    await screen.findByText('知识图谱');

    // 确认待审边 → 页面会"重新拉取"图谱与统计，漂移发生在这一次重拉上
    await userEvent.click(screen.getByTestId('fg-link-e-1'));
    const detailPanel = screen
      .getByText('关系详情')
      .closest(`.${graphStyles.graphPanel}`) as HTMLElement;
    await userEvent.click(within(detailPanel).getByRole('button', { name: '确认' }));
    await waitFor(() => expect(mockedStats).toHaveBeenCalledTimes(2));

    await act(async () => {
      pending.resolve(makeStats({ relation_type_distribution: { related: 1 } as never }));
    });

    // 与加载路径同判据：分布归一成空 → 面板保留四个基础数字，只是没有分布条，整页不白屏
    expect(screen.getByText('图谱统计')).toBeInTheDocument();
    expect(screen.getByText('待确认')).toBeInTheDocument();
    expect(document.querySelector(`.${graphStyles.graphStatsBarRow}`)).toBeNull();
    expectNoCrash();
  });

  it('suggestions 不是数组（如 {items:[...]} 包装）时取内层数组渲染，不崩也不丢数据', async () => {
    mockedSuggestions.mockResolvedValue({ items: [makeSuggestion()] } as never);
    renderPage();
    await screen.findByText('知识图谱');

    await userEvent.click(screen.getByRole('button', { name: /建议/ }));

    // 包装对象只是形状漂移：取内层数组照常渲染（用户还能看到并处理这条建议），
    // 而不是把建议整批丢掉只显示"暂无建议关系"
    const panelTitle = screen.getByText(/建议关系 \(1\)/);
    expect(panelTitle).toBeInTheDocument();
    const panel = panelTitle.closest(`.${graphStyles.graphPanel}`) as HTMLElement;
    expect(within(panel).getByText('浮充的定义')).toBeInTheDocument();
    expect(within(panel).getByText('均充的定义')).toBeInTheDocument();
    expect(within(panel).getByText(/相似度: 0\.87/)).toBeInTheDocument();
    expect(within(panel).getByRole('button', { name: '确认' })).toBeInTheDocument();
    expect(within(panel).getByRole('button', { name: '拒绝' })).toBeInTheDocument();
    // 顶部「建议」按钮的徽标同样不能显示 undefined
    expect(screen.getByRole('button', { name: /建议/ })).toHaveTextContent('1');
    expectNoCrash();
  });

  it('★ 生成相关建议拿到 {items:[...]} 包装时拆包渲染（生成路径与加载路径同一道判据）', async () => {
    // 加载时确实一条建议都没有（空态里才有「生成相关建议」按钮），漂移发生在"生成"这条路径上
    const pending = deferred<unknown>();
    mockedSuggestions.mockResolvedValueOnce([]);
    mockedSuggestions.mockImplementation(() => pending.promise as never);
    renderPage();
    await screen.findByText('知识图谱');

    await userEvent.click(screen.getByRole('button', { name: /建议/ }));
    expect(screen.getByText(/暂无建议关系/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '生成相关建议' }));
    // 第二次请求（生成后重拉建议）已经发出，明确把包装响应用 act 灌进去，断言的是它落地之后的样子
    await waitFor(() => expect(mockedSuggestions).toHaveBeenCalledTimes(2));

    await act(async () => {
      pending.resolve({ items: [makeSuggestion()] });
    });

    // 与加载路径同判据：拆包后照常渲染出这条建议，而不是崩到错误边界、也不是"暂无建议关系"
    const panelTitle = await screen.findByText(/建议关系 \(1\)/);
    const panel = panelTitle.closest(`.${graphStyles.graphPanel}`) as HTMLElement;
    expect(within(panel).getByText('浮充的定义')).toBeInTheDocument();
    expect(within(panel).getByText('均充的定义')).toBeInTheDocument();
    expect(within(panel).getByText(/相似度: 0\.87/)).toBeInTheDocument();
    expect(within(panel).getByRole('button', { name: '确认' })).toBeInTheDocument();
    expect(within(panel).getByRole('button', { name: '拒绝' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /建议/ })).toHaveTextContent('1');
    expectNoCrash();
  });

  it('安全：生成相关建议拿到非数组（既不是数组也没有 items）时退化成空态，不崩', async () => {
    const pending = deferred<unknown>();
    mockedSuggestions.mockResolvedValueOnce([]);
    mockedSuggestions.mockImplementation(() => pending.promise as never);
    renderPage();
    await screen.findByText('知识图谱');

    await userEvent.click(screen.getByRole('button', { name: /建议/ }));
    await userEvent.click(screen.getByRole('button', { name: '生成相关建议' }));
    await waitFor(() => expect(mockedSuggestions).toHaveBeenCalledTimes(2));

    await act(async () => {
      pending.resolve({ total: 0 });
    });

    // 真正拿不到数组才降级：面板留在空态并保留「生成相关建议」入口，整页没有被错误边界接走
    expect(screen.getByText(/暂无建议关系/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '生成相关建议' })).toBeInTheDocument();
    expectNoCrash();
  });

  it('节点缺 note_id 时详情面板显示"未知来源"占位，不再在 slice 上崩', async () => {
    mockedGraphData.mockResolvedValue({
      nodes: [makeNode({ note_id: undefined as never })],
      edges: [],
    });
    renderPage();
    await screen.findByText('知识图谱');

    await userEvent.click(screen.getByTestId('fg-node-c-1'));

    const panel = screen.getByText('节点详情').closest(`.${graphStyles.graphPanel}`) as HTMLElement;
    expect(within(panel).getByText('未知来源')).toBeInTheDocument();
    // 详情其余字段照常可用：标题、关联数与两个入口按钮都在
    expect(within(panel).getByText('浮充的定义')).toBeInTheDocument();
    expect(within(panel).getByRole('button', { name: '查看关联节点' })).toBeInTheDocument();
    expectNoCrash();
  });

  it('子图响应缺 neighbor_nodes 时仍展示中心节点并说明"暂无关联节点"，不崩', async () => {
    mockedSubgraph.mockResolvedValue({
      center_node: makeNode({ id: 'c-1' }),
      edges: [],
    } as never);
    renderPage();
    await screen.findByText('知识图谱');

    await userEvent.click(screen.getByTestId('fg-node-c-1'));
    await userEvent.click(await screen.findByRole('button', { name: '查看关联节点' }));

    // 邻居数组归一成空 → 面板出现并给出明确的空说明（而不是"加载失败"或白屏）
    expect(await screen.findByText('关联节点')).toBeInTheDocument();
    expect(screen.getByText('浮充的定义')).toBeInTheDocument();
    expect(screen.getByText(/0 个关联节点/)).toBeInTheDocument();
    expect(screen.getByText('该节点暂无关联节点')).toBeInTheDocument();
    expectNoCrash();
  });

  it('子图响应连 center_node 都缺时不渲染子图面板，图谱本体照常可用', async () => {
    mockedSubgraph.mockResolvedValue({ edges: [] } as never);
    renderPage();
    await screen.findByText('知识图谱');

    await userEvent.click(screen.getByTestId('fg-node-c-1'));
    await userEvent.click(await screen.findByRole('button', { name: '查看关联节点' }));

    // 没有中心节点就没有可展示的内容：面板整体不出现，页面其余部分不受影响
    await waitFor(() => expect(screen.queryByText('关联节点')).not.toBeInTheDocument());
    expect(screen.getByTestId('fg-node-c-1')).toBeInTheDocument();
    expectNoCrash();
  });

  it('回收站节点不进画布时，顶部节点数与画布口径一致（不再"标题 3 节点、画布 2 个"）', async () => {
    mockedGraphData.mockResolvedValue({
      nodes: [
        makeNode({ id: 'c-1' }),
        makeNode({ id: 'c-2', title: '均充的定义' }),
        makeNode({ id: 'c-9', title: '已进回收站的卡片', note_trashed: true }),
      ],
      edges: [],
    });
    renderPage();
    await screen.findByText('知识图谱');

    expect(screen.getByTestId('fg-node-ids')).toHaveTextContent('c-1,c-2');
    expect(screen.getByText(/2 节点/).textContent).toContain('2 节点');
    expectNoCrash();
  });
});

/**
 * 批次 E4：关系类型改用线型区分
 *
 * `docs/visual-design-spec.md` §6.4 / `visual-refactor-plan.md` §6 的 E4 行。
 * 这里钉的是"画布真的按类型画线型"，图例与表的正确性在
 * `components/graph/types.test.ts`。
 */
describe('关系类型的线型（批次 E4）', () => {
  /** 画一条边，返回这次绘制的假上下文（供断言 setLineDash / strokeStyle） */
  function drawEdge(props: ForceGraphMockProps, over: Partial<ForceGraphLink>): FakeCtx {
    const ctx = makeCtx();
    props.linkCanvasObject(
      {
        id: 'e-x',
        source: makeForce({ id: 'c-1', x: 0, y: 0 }),
        target: makeForce({ id: 'c-2', x: 40, y: 0 }),
        relation_type: 'related',
        status: 'confirmed',
        similarity_score: null,
        ...over,
      },
      ctx as unknown as CanvasRenderingContext2D,
      1,
    );
    return ctx;
  }

  it('★ 四类关系各画各的线型：实线 / 长虚线 / 虚线 / 点线', async () => {
    renderPage();
    await screen.findByText('知识图谱');
    const props = canvasProps();

    for (const type of ['prerequisite', 'subsequent', 'related', 'contrast']) {
      expect(dashPatterns(drawEdge(props, { relation_type: type }))).toContainEqual(
        getRelationDash(type, 1),
      );
    }

    // 前提是实线：整笔绘制里不允许出现任何非空图案
    expect(dashPatterns(drawEdge(props, { relation_type: 'prerequisite' })).flat()).toEqual([]);
  });

  it('★ 线型只讲类型：待审与否画的线型一字不差，状态改用"更淡 + 无箭头"', async () => {
    renderPage();
    await screen.findByText('知识图谱');
    const props = canvasProps();

    const confirmed = drawEdge(props, { relation_type: 'related', status: 'confirmed' });
    const suggested = drawEdge(props, { relation_type: 'related', status: 'suggested' });

    // 同一类关系，待审与已确认的图案完全一致（线型已经被类型占用）
    expect(dashPatterns(suggested)).toEqual(dashPatterns(confirmed));
    expect(dashPatterns(suggested)).toContainEqual(getRelationDash('related', 1));

    // 状态通道：待审更淡（已确认 A8 ≈ 66%，待审 88 ≈ 53%）
    expect(String(confirmed.strokeStyle)).toMatch(/A8$/);
    expect(String(suggested.strokeStyle)).toMatch(/88$/);

    // 箭头通道：有向的两类才画，且待审一律不画
    const arrowLength = props.linkDirectionalArrowLength;
    expect(typeof arrowLength).toBe('function');
    const arrow = arrowLength as (link: ForceGraphLink) => number;
    const link = (relation_type: string, status: string) =>
      ({
        id: 'e',
        source: 'c-1',
        target: 'c-2',
        relation_type,
        status,
        similarity_score: null,
      }) as ForceGraphLink;

    expect(arrow(link('prerequisite', 'confirmed'))).toBe(3);
    expect(arrow(link('subsequent', 'confirmed'))).toBe(3);
    expect(arrow(link('related', 'confirmed'))).toBe(0);
    expect(arrow(link('contrast', 'confirmed'))).toBe(0);
    expect(arrow(link('prerequisite', 'suggested'))).toBe(0);
  });

  it('★ canvas 颜色从 --graph-* 令牌取值；读不到令牌时退回与 base.css 同源的字面量', async () => {
    renderPage();
    await screen.findByText('知识图谱');
    const props = canvasProps();
    const node = makeForce({ x: 5, y: 5 });

    // ① 真实浏览器：`:root` 上有令牌 → 节点描边用的就是它
    resetGraphCanvasTokensCache();
    vi.stubGlobal('getComputedStyle', () => ({
      getPropertyValue: (name: string) => (name === '--graph-ink-faint' ? 'rgb(1, 2, 3)' : ''),
    }));
    const withTokens = makeCtx();
    props.nodeCanvasObject(node, withTokens as unknown as CanvasRenderingContext2D, 1);
    expect(withTokens.strokeStyle).toBe('rgb(1, 2, 3)');

    // ② jsdom / 令牌被删：回退值必须是**有效颜色**（空串会被 canvas 静默忽略，
    //    于是描边沿用上一笔的颜色 —— 风险登记表 §9 第 5 条就是这件事）
    vi.unstubAllGlobals();
    resetGraphCanvasTokensCache();
    const fallback = makeCtx();
    props.nodeCanvasObject(node, fallback as unknown as CanvasRenderingContext2D, 1);
    expect(fallback.strokeStyle).toBe(GRAPH_CANVAS_TOKEN_FALLBACKS.inkFaint);
    expect(String(fallback.strokeStyle)).not.toBe('');
  });
});

/**
 * 批次 E4：当前节点支持键盘上下切换
 *
 * `docs/visual-symbol-research.md` §C3 的「知识图谱」行。
 * 两条硬要求：① 上下键**真的**换节点；② 画布是 canvas、节点没有 DOM 语义，
 * 所以详情面板 + `aria-live` 播报必须跟着走（读屏用户不能失去"当前是哪个"）。
 */
describe('键盘上下切换当前节点（批次 E4）', () => {
  it('★ 画布是可聚焦的区域，用法说明挂在 aria-describedby 上', async () => {
    renderPage();
    await screen.findByText('知识图谱');

    const region = canvasRegion();
    expect(region).toHaveAttribute('tabindex', '0');

    const hintId = region.getAttribute('aria-describedby');
    expect(hintId).toBeTruthy();
    // 说明文字必须真的存在且指出按键（否则读屏用户只知道"这里是一块画布"）
    expect(document.getElementById(hintId as string)?.textContent).toContain('上下方向键');
  });

  it('★ 上下方向键真的换节点：视口跟着走，详情面板与播报一起更新', async () => {
    renderPage();
    await screen.findByText('知识图谱');

    // 画布节点在 jsdom 里没有坐标（力导向布局不会跑），这里补上坐标，
    // 好断言"键盘换节点时视口会跟过去"
    const nodes = canvasProps().graphData.nodes;
    nodes[0].x = 10;
    nodes[0].y = 20;

    expect(graphStatusLine().textContent).toBe('');

    const region = canvasRegion();
    await userEvent.click(region); // 键盘用户先 Tab/点到这块区域上
    expect(region).toHaveFocus();

    // 还没有当前节点时，第一次按「下」从第一个开始（而不是"按了没反应"）
    await userEvent.keyboard('{ArrowDown}');
    expect(within(nodeDetailPanel()).getByText('浮充的定义')).toBeInTheDocument();
    expect(graphStatusLine()).toHaveTextContent('当前节点：浮充的定义（第 1 / 4 个）');
    expect(fg.api.centerAt).toHaveBeenCalledWith(10, 20, 400);

    // 继续向下 / 向上
    await userEvent.keyboard('{ArrowDown}');
    expect(within(nodeDetailPanel()).getByText('均充的定义')).toBeInTheDocument();
    expect(graphStatusLine()).toHaveTextContent('当前节点：均充的定义（第 2 / 4 个）');

    await userEvent.keyboard('{ArrowUp}');
    expect(within(nodeDetailPanel()).getByText('浮充的定义')).toBeInTheDocument();

    // 循环：在第一个上按「上」回到最后一个
    await userEvent.keyboard('{ArrowUp}');
    expect(within(nodeDetailPanel()).getByText('浮充与均充的区别')).toBeInTheDocument();
    expect(graphStatusLine()).toHaveTextContent('（第 4 / 4 个）');
  });

  it('焦点在缩放按钮上时方向键不换节点（不抢子控件的按键）', async () => {
    renderPage();
    await screen.findByText('知识图谱');

    screen.getByRole('button', { name: '放大' }).focus();
    await userEvent.keyboard('{ArrowDown}');

    expect(screen.queryByText('节点详情')).not.toBeInTheDocument();
    expect(graphStatusLine().textContent).toBe('');
  });

  it('鼠标点节点同样会播报当前节点（状态行不是键盘专用）', async () => {
    renderPage();
    await screen.findByText('知识图谱');

    await userEvent.click(screen.getByTestId('fg-node-c-2'));

    expect(graphStatusLine()).toHaveTextContent('当前节点：均充的定义（第 2 / 4 个）');
  });
});

/**
 * 批次 E4：图例改为"线型 → 类型"
 *
 * ⚠️ 可访问名必须仍是关系类型名（'相关' …）：`e2e/a11y.spec.ts` 的键盘走查
 * 用 `getByRole('button', { name: '相关' })` 找它 —— 线型色块是装饰，
 * 所以它是 `aria-hidden` 的 span，不进可访问名。
 */
describe('图例：线型 → 类型（批次 E4）', () => {
  it('★ 四个关系类型各画各的线型，色块本身不进可访问名', async () => {
    renderPage();
    await screen.findByText('知识图谱');

    const lineClassOf = (name: string) => {
      const button = screen.getByRole('button', { name });
      const swatch = button.querySelector('[aria-hidden="true"]');
      expect(swatch).not.toBeNull();
      return swatch as HTMLElement;
    };

    expect(lineClassOf('前置').className).toContain(graphStyles.graphRelationLineSolid);
    expect(lineClassOf('后续').className).toContain(graphStyles.graphRelationLineLongDash);
    expect(lineClassOf('相关').className).toContain(graphStyles.graphRelationLineDashed);
    expect(lineClassOf('对比').className).toContain(graphStyles.graphRelationLineDotted);

    // 可访问名仍是关系类型名（键盘走查按名字找）
    expect(screen.getByRole('button', { name: '相关' })).toHaveAccessibleName('相关');
  });

  it('待审那句不再冒充某种线型（线型已被关系类型占用）', async () => {
    renderPage();
    await screen.findByText('知识图谱');

    expect(screen.getByText('待审建议（淡色 · 无箭头）')).toBeInTheDocument();
    expect(screen.queryByText('建议关系')).not.toBeInTheDocument();
  });
});
