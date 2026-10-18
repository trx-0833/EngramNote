/**
 * 图谱规模性能探针（先立基线，再动优化）
 *
 * ## 它回答的问题
 *
 * "节点太多时显示卡"到底卡在哪。真实库是 **1181 节点 / 260 边**
 * （见 `graph-perf-fixtures.ts` 文件头的统计），而 `GET /api/graph`
 * 没有上限，全部下发全部渲染。
 *
 * ## ⚠️ 为什么"空闲时测帧率"会给出假绿
 *
 * 第一版探针在力导向冷却后直接采样 rAF 间隔。改动**之前**它是对的
 * （粒子让画布每帧都重绘，量到的就是真实帧成本）；改动**之后**画布彻底空闲，
 * 于是它量的是"一个什么都不做的 rAF 循环" —— 报出漂亮的 60fps / 掉帧 0，
 * 而真正要回答的"重绘一次要多久"一个字都没说。
 *
 * 所以本探针分成两个**必须都看**的窗口：
 *
 * 1. **空闲窗口**：`clearRect` 次数/秒。它回答"用户不动的时候还在不在烧 CPU"。
 *    0 是好的 —— 但**它单独不能证明渲染变快了**。
 * 2. **持续缩放窗口**：每帧派发一个真实 `WheelEvent`（走 d3-zoom 的真实路径），
 *    强制每帧重绘，再采样帧间隔与 canvas 调用。**这一条才是可比的渲染吞吐**。
 *
 * 只看第 1 条会把"什么都不画"误读成"性能很好"。
 *
 * ## 归因：在探针侧做消融
 *
 * 改代码再量，量到的是"改动之和"。逐个关掉某类开销（节点阴影 / 玻璃径向渐变）
 * 才能把帧时间归因到具体调用上，也才知道该先动哪一处。
 * 补丁只影响绘制开销，不改变几何与交互路径。
 *
 * ## 它有一条断言，其余只打印
 *
 * 纯打印的探针会**静默地量了个寂寞**：桩若没生效（路由没拦住、契约字段改名），
 * 页面渲染的是空图，数字漂亮得毫无意义。所以这里断言工具栏上的
 * `1181 节点 · 260 边`（`GraphToolbar.tsx:70` 的渲染口径就是画布实际数据）——
 * 桩没生效就直接红。除此之外**不做任何断言**：探针不判定"快不快"，
 * 只给同一把尺子的两次读数。
 *
 * ## 用法
 *
 *     npm run perf:graph
 *
 * 不进任何阻断门禁（与 `shot-probe` 同一个理由：它永远不会"失败"，
 * 混进 `npm run e2e` 只会让那一层变慢变糊）。
 */
import { expect, test, type Page } from '@playwright/test';

import { installA11yStubs, loginAs } from './a11y-fixtures';
import { REAL_SHAPE, buildLargeGraph } from './graph-perf-fixtures';

/**
 * 力导向冷却等待：`GraphCanvas.tsx` 的 `cooldownTicks={100}`。
 * 冷却之后 `isEngineRunning()` 为假 —— 此时若还在重绘，就只可能是粒子。
 */
const SETTLE_MS = 8_000;

/** 采样窗口。3 秒 × 60fps ≈ 180 帧，够看 p95 与掉帧数。 */
const SAMPLE_MS = 3_000;

/** 被计数的 canvas 调用：每一项都对应绘制路径上的一处已知开销 */
const CANVAS_CALLS = [
  'clearRect',
  'beginPath',
  'arc',
  'fill',
  'stroke',
  'createRadialGradient',
  'measureText',
  'roundRect',
  'fillText',
  'setLineDash',
] as const;

interface FrameStats {
  frames: number;
  avgMs: number;
  p50: number;
  p95: number;
  max: number;
  over33: number;
}

/** 打开图谱页并喂进真实规模的桩；等到力导向冷却 */
async function openLargeGraph(page: Page): Promise<void> {
  const graph = buildLargeGraph();
  await installA11yStubs(page, {
    '/api/graph': { nodes: graph.nodes, edges: graph.edges },
    '/api/graph/suggestions': [],
    '/api/graph/stats': {
      total_nodes: graph.nodes.length,
      total_edges: graph.edges.length,
      confirmed_edges: graph.edges.length,
      suggested_edges: 0,
      relation_type_distribution: [{ relation_type: 'related', count: graph.edges.length }],
      isolated_nodes:
        graph.nodes.length -
        (REAL_SHAPE.degree1Nodes + REAL_SHAPE.degree2Nodes + REAL_SHAPE.hubNodes),
    },
  });
  await loginAs(page, '/graph');
  await page.getByRole('application', { name: '知识图谱画布' }).waitFor({ state: 'visible' });

  // 桩生效的判据（见文件头）：计数口径来自画布实际渲染的数据
  await page
    .getByText(`${graph.nodes.length} 节点 · ${graph.edges.length} 边`, { exact: false })
    .waitFor({ state: 'visible' });

  await page.waitForTimeout(SETTLE_MS);
}

/** 在页面里采样 rAF 间隔；`driveZoom` 为真时每帧派发一个真实滚轮事件强制重绘 */
async function sampleFrames(
  page: Page,
  durationMs: number,
  driveZoom: boolean,
): Promise<FrameStats> {
  return page.evaluate(
    async ({ duration, zoom }) => {
      const canvas = document.querySelector('canvas');
      const rect = canvas?.getBoundingClientRect();
      const cx = rect ? rect.left + rect.width / 2 : 0;
      const cy = rect ? rect.top + rect.height / 2 : 0;

      const deltas: number[] = [];
      let last = performance.now();
      const stop = last + duration;
      let direction = 1;
      let tick = 0;

      await new Promise<void>((resolve) => {
        const frame = () => {
          const now = performance.now();
          deltas.push(now - last);
          last = now;

          if (zoom && canvas) {
            // 每帧一次真实滚轮事件：走 d3-zoom 的真实路径 → needsRedraw → 全量重绘。
            // 方向定期翻转，避免一路缩到极限后不再产生变换。
            if (tick % 40 === 0) direction = -direction;
            canvas.dispatchEvent(
              new WheelEvent('wheel', {
                deltaY: direction * 120,
                clientX: cx,
                clientY: cy,
                bubbles: true,
                cancelable: true,
              }),
            );
          }
          tick += 1;

          if (now < stop) requestAnimationFrame(frame);
          else resolve();
        };
        requestAnimationFrame(frame);
      });

      const sorted = [...deltas].sort((a, b) => a - b);
      const at = (p: number) => sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * p))];
      return {
        frames: deltas.length,
        avgMs: deltas.reduce((sum, d) => sum + d, 0) / deltas.length,
        p50: at(0.5),
        p95: at(0.95),
        max: sorted[sorted.length - 1],
        over33: deltas.filter((d) => d > 33.4).length,
      };
    },
    { duration: durationMs, zoom: driveZoom },
  );
}

/** 装 canvas 调用计数器（必须在导航之前调用） */
async function installCanvasCounters(page: Page): Promise<void> {
  await page.addInitScript(
    (names: string[]) => {
      const store = window as unknown as { __canvasCalls: Record<string, number> };
      store.__canvasCalls = {};
      const proto = CanvasRenderingContext2D.prototype as unknown as Record<string, unknown>;
      for (const name of names) {
        const original = proto[name];
        if (typeof original !== 'function') continue;
        const call = original as (...args: unknown[]) => unknown;
        proto[name] = function patched(this: unknown, ...args: unknown[]) {
          store.__canvasCalls[name] = (store.__canvasCalls[name] ?? 0) + 1;
          return call.apply(this, args);
        };
      }
    },
    CANVAS_CALLS as unknown as string[],
  );
}

async function readCanvasCounts(page: Page): Promise<Record<string, number>> {
  return page.evaluate(
    () => (window as unknown as { __canvasCalls?: Record<string, number> }).__canvasCalls ?? {},
  );
}

/**
 * 消融开关：在**探针侧**把某类开销关掉，用来量"它值多少毫秒"。
 *
 * 两个补丁都只影响绘制开销，不改变几何/交互路径 —— 帧率可比。
 */
interface Ablation {
  label: string;
  noShadow: boolean;
  noGradient: boolean;
}

async function installAblation(page: Page, ablation: Ablation): Promise<void> {
  await page.addInitScript(
    (flags: { noShadow: boolean; noGradient: boolean }) => {
      const proto = CanvasRenderingContext2D.prototype as unknown as Record<string, unknown>;

      // `drawNode.ts`：每个节点每帧设一次 shadowBlur + shadowColor。
      // 阴影要求 canvas 逐像素做一次模糊，是已知最贵的一类 2D 操作。
      if (flags.noShadow) {
        try {
          Object.defineProperty(proto, 'shadowBlur', {
            configurable: true,
            get: () => 0,
            set: () => undefined,
          });
        } catch {
          /* 属性不可配置时保持原样：宁可量不准也不要静默改坏页面 */
        }
      }

      // `drawNode.ts`：每个节点每帧新建一个径向渐变对象，**再往它上面加 3 个色标**。
      // 这里把两件事一起摘掉。
      //
      // ⚠️ 只摘"创建"是不够的 —— 第一版消融只缓存了对象、没管 `addColorStop`，
      // 结果每帧从 62ms 变成 **4937ms**：1181 个节点每帧往**同一个**渐变对象上
      // 反复加色标，Chromium 会不断重建它的内部表示。这条教训直接决定了实现写法：
      // 要缓存就得缓存"**建好带色标的**渐变"。
      if (flags.noGradient) {
        const originalCreate = proto.createRadialGradient as (...args: unknown[]) => unknown;
        let cached: unknown = null;
        proto.createRadialGradient = function patched(this: unknown, ...args: unknown[]) {
          if (!cached) cached = originalCreate.apply(this, args);
          return cached;
        };
        const gradientProto = CanvasGradient.prototype as unknown as Record<string, unknown>;
        gradientProto.addColorStop = function noop() {
          /* 见上：连色标一起摘掉，才量得到"渐变这条路总共值多少" */
        };
      }
    },
    { noShadow: ablation.noShadow, noGradient: ablation.noGradient },
  );
}

/** 消融矩阵：先量原样（对照组），再逐个关掉 */
const ABLATIONS: Ablation[] = [
  { label: '对照组（原样）', noShadow: false, noGradient: false },
  { label: '关掉节点阴影 shadowBlur=0', noShadow: true, noGradient: false },
  { label: '关掉玻璃径向渐变（含色标）', noShadow: false, noGradient: true },
  { label: '两者都关', noShadow: true, noGradient: true },
];

function formatStats(stats: FrameStats): string {
  return (
    `${stats.frames} 帧 ｜ 平均 ${stats.avgMs.toFixed(2)} ms（≈ ${(1000 / stats.avgMs).toFixed(1)} fps）` +
    ` ｜ p50 ${stats.p50.toFixed(2)} ｜ p95 ${stats.p95.toFixed(2)} ｜ 最长 ${stats.max.toFixed(2)} ms` +
    ` ｜ 掉帧(>33ms) ${stats.over33}/${stats.frames}`
  );
}

/**
 * 记录**每个被真正画出来的节点**的屏幕坐标。
 *
 * 做法：`drawNode.ts` 里"玻璃反射高光点"是每个被绘制的节点**恰好一次**的
 * `arc` + `fill`，填充色固定为 `rgba(255, 255, 255, 0.85)` —— 用它当指纹。
 * 屏幕坐标由 `ctx.getTransform()` 现场换算（`force-graph.mjs:1501-1503` 把
 * `translate(t.x,t.y); scale(t.k,t.k)` 施加在上下文上）。
 *
 * 为什么需要它：帧率、覆盖率都是**间接**指标。视口剔除一旦算错变换，
 * 帧率会**变好**、画布会**变空**，而账面全是绿的 —— 这一条直接问
 * "画出来的东西到底落在屏幕哪里"，是唯一能证伪它的判据。
 */
async function installNodeDotRecorder(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const store = window as unknown as { __nodeDots: Array<[number, number]> };
    store.__nodeDots = [];
    const proto = CanvasRenderingContext2D.prototype as unknown as Record<string, unknown>;
    let lastArcScreen: { x: number; y: number } | null = null;

    const originalArc = proto.arc as (...args: unknown[]) => unknown;
    proto.arc = function patchedArc(
      this: CanvasRenderingContext2D,
      x: number,
      y: number,
      r: number,
      ...rest: unknown[]
    ) {
      const t = this.getTransform();
      lastArcScreen = { x: t.a * x + t.c * y + t.e, y: t.b * x + t.d * y + t.f };
      return originalArc.call(this, x, y, r, ...rest);
    };

    const originalFill = proto.fill as (...args: unknown[]) => unknown;
    proto.fill = function patchedFill(this: CanvasRenderingContext2D, ...args: unknown[]) {
      if (this.fillStyle === 'rgba(255, 255, 255, 0.85)' && lastArcScreen) {
        store.__nodeDots.push([lastArcScreen.x, lastArcScreen.y]);
      }
      return originalFill.apply(this, args);
    };
  });
}

/** 强制重绘一次（画布空闲时不会调用任何绘制回调，记录不到东西） */
async function forceOneRedraw(page: Page): Promise<void> {
  await page.evaluate(() => {
    const canvas = document.querySelector('canvas');
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    canvas.dispatchEvent(
      new WheelEvent('wheel', {
        deltaY: 120,
        clientX: rect.left + rect.width / 2,
        clientY: rect.top + rect.height / 2,
        bubbles: true,
        cancelable: true,
      }),
    );
  });
  await page.waitForTimeout(400);
}

test.describe('图谱规模性能探针', () => {
  test('① 空闲窗口：画布还在不在重绘（0 次/秒 = 已空闲）', async ({ page }) => {
    await installCanvasCounters(page);
    await openLargeGraph(page);

    const before = await readCanvasCounts(page);
    const stats = await sampleFrames(page, SAMPLE_MS, false);
    const after = await readCanvasCounts(page);

    const cleared = (after.clearRect ?? 0) - (before.clearRect ?? 0);

    console.log('\n=== ① 空闲窗口（用户什么都没做）===');
    console.log(`  规模        ${REAL_SHAPE.nodes} 节点 / ${REAL_SHAPE.edges} 边`);
    console.log(`  clearRect   ${cleared} 次 / ${SAMPLE_MS / 1000}s`);
    console.log(`  rAF 间隔    ${formatStats(stats)}`);
    console.log(
      '  读法        clearRect 为 0 = 画布已空闲（省电）；' +
        '非 0 = 有东西每帧触发 doRedraw。' +
        '⚠️ 这一条的帧率**不能**当作渲染性能，见文件头。',
    );
  });

  test('② 持续缩放窗口：真实渲染吞吐（每帧强制重绘）', async ({ page }) => {
    await installCanvasCounters(page);
    await openLargeGraph(page);

    const before = await readCanvasCounts(page);
    const stats = await sampleFrames(page, SAMPLE_MS, true);
    const after = await readCanvasCounts(page);

    const delta: Record<string, number> = {};
    for (const name of CANVAS_CALLS) delta[name] = (after[name] ?? 0) - (before[name] ?? 0);
    const perFrame = (name: string) =>
      stats.frames > 0 ? Math.round((delta[name] ?? 0) / stats.frames) : 0;

    console.log('\n=== ② 持续缩放窗口（每帧一个滚轮事件，强制全量重绘）===');
    console.log(`  规模        ${REAL_SHAPE.nodes} 节点 / ${REAL_SHAPE.edges} 边`);
    console.log(`  帧间隔      ${formatStats(stats)}`);
    console.log(`  clearRect   ${perFrame('clearRect')} 次/帧`);
    console.log(`  beginPath   ${perFrame('beginPath')} 次/帧`);
    console.log(`  arc         ${perFrame('arc')} 次/帧`);
    console.log(`  fill        ${perFrame('fill')} 次/帧`);
    console.log(`  stroke      ${perFrame('stroke')} 次/帧`);
    console.log(`  setLineDash ${perFrame('setLineDash')} 次/帧`);
    console.log(`  createRadialGradient ${perFrame('createRadialGradient')} 次/帧`);
    console.log(`  measureText ${perFrame('measureText')} 次/帧`);
    console.log(`  roundRect   ${perFrame('roundRect')} 次/帧`);
    console.log(`  fillText    ${perFrame('fillText')} 次/帧`);

    // ── 粗粒度回归闸 ──
    //
    // ⚠️ 这条**不是**性能指标，是"灾难探测器"。为什么阈值放这么宽：
    // 同一个配置连跑两次，② 与 ③对照 分别给出 54.65 ms 与 67.46 ms ——
    // **噪声约 ±20%**，比大多数真实改动的效应还大。用窄阈值只会得到 flaky 用例。
    //
    // 150 ms 是实测值（54~68 ms）的 2~3 倍余量，能抓住的是量级级别的回归 ——
    // 例如缓存渐变对象却仍调 `addColorStop` 那次（62 ms → **4937 ms**）。
    // 那种 bug 会让图谱彻底不可用，而"慢了 15%"不会。
    const FORCED_ZOOM_BUDGET_MS = 150;
    console.log(
      `  回归闸      平均帧间隔须 < ${FORCED_ZOOM_BUDGET_MS} ms` +
        `（实测 ${stats.avgMs.toFixed(2)} ms，噪声 ±20%，余量 2~3 倍）`,
    );
    expect(
      stats.avgMs,
      `持续缩放的平均帧间隔 ${stats.avgMs.toFixed(1)} ms 超过 ${FORCED_ZOOM_BUDGET_MS} ms ` +
        '—— 量级级别的渲染回归（不是"慢了百分之十几"）',
    ).toBeLessThan(FORCED_ZOOM_BUDGET_MS);
  });

  /**
   * 消融：把帧时间拆到具体开销上。
   *
   * 每条用例各起一次干净页面（Playwright 默认给每个 test 一个新 context），
   * 否则 init script 会累积，量到的是"补丁之和"。
   * 一律在**持续缩放**下量 —— 空闲窗口量的是空转的 rAF，见文件头。
   */
  for (const ablation of ABLATIONS) {
    test(`③ 消融：${ablation.label}`, async ({ page }) => {
      await installAblation(page, ablation);
      await openLargeGraph(page);
      const stats = await sampleFrames(page, SAMPLE_MS, true);

      console.log(`\n=== ③ 消融（持续缩放）${ablation.label} ===`);
      console.log(`  ${formatStats(stats)}`);
    });
  }

  /**
   * 默认视角的**视觉快照 + 画布覆盖率**。
   *
   * 为什么要有它：帧率与 canvas 调用数都是**间接**指标，它们不能回答
   * "有没有把该看见的东西藏起来"。视口剔除、按需绘制这类优化一旦算错边界，
   * 帧率会**变好**而画面**变空** —— 数字全绿、用户一眼就看出来不对。
   *
   * 覆盖率（非透明像素占比）是把"藏了多少"变成一个可比的数字，
   * 截图则是给人眼复核用的。两者一起看。
   */
  test('④ 默认视角：画布覆盖率 + 截图（防止"变快是因为画得少了"）', async ({ page }) => {
    await openLargeGraph(page);

    const coverage = await page.evaluate(() => {
      const canvases = Array.from(document.querySelectorAll('canvas'));
      const canvas = canvases.sort((a, b) => b.width * b.height - a.width * a.height)[0];
      const ctx = canvas?.getContext('2d');
      if (!canvas || !ctx) return null;
      const { width, height } = canvas;
      const { data } = ctx.getImageData(0, 0, width, height);
      let painted = 0;
      for (let i = 3; i < data.length; i += 4) {
        if (data[i] > 0) painted += 1;
      }
      return {
        width,
        height,
        painted,
        ratio: painted / (width * height),
        canvasCount: canvases.length,
      };
    });

    await page.screenshot({ path: 'shots-graph-perf/default-view.png' });

    console.log('\n=== ④ 默认视角 ===');
    console.log(`  规模        ${REAL_SHAPE.nodes} 节点 / ${REAL_SHAPE.edges} 边`);
    if (coverage) {
      console.log(
        `  画布        ${coverage.width} × ${coverage.height}（页面上共 ${coverage.canvasCount} 个 canvas）`,
      );
      console.log(
        `  覆盖率      ${(coverage.ratio * 100).toFixed(2)}%（非透明像素 ${coverage.painted}）`,
      );
    } else {
      console.log('  覆盖率      取不到（没有 canvas 或上下文）');
    }
    console.log('  截图        frontend/shots-graph-perf/default-view.png');
  });

  /**
   * ★ 视口剔除的端到端判据（给那次真实回归立的守卫）。
   *
   * 回归现象：**有链接的节点被藏起来了**。
   * 成因：剔除用的变换算错了 —— `onZoom` 回调给的 `x` / `y` 是**图空间里的视口中心**
   * （`force-graph.mjs:1506` 的 `{ ...zoomTransform, ...centerAt() }`，后者覆盖前者），
   * 而不是 `translate(t.x, t.y)` 里的平移量。真实初值是
   * `{ k: 1, x: width/2, y: height/2 }`（`force-graph.mjs:999-1002` 的
   * `translateBy((width-curWidth)/2/k, ...)`），而页面里的 `viewportRef` 停在
   * `{ k: 1, x: 0, y: 0 }` —— 于是"可见区域"被算成图空间左上角那一块：
   * **把左半边的节点判成界外藏了起来，同时把右半边的界外节点画了出来**。
   * 实测（本判据）：越界节点 4397 个、最远 290 px。
   *
   * ## 判据的适用范围（别把它当成万能）
   *
   * 它断言的是：**大图（开了剔除）时，被绘制的节点应当全部落在画布 + 余量内**。
   * 变换算错时，判据会"以为"在界内的节点其实在很远处，这条立刻红。
   *
   * 反方向（余量太小导致该画的没画）这条**抓不到** —— 那个由
   * `src/components/graph/viewportCulling.test.ts` 的纯函数单测逐点压住。
   * 两者是互补的：这里管"喂进去的变换是不是真的"，单测管"给定真变换后判得对不对"。
   *
   * 注意这条判据对"**完全没开剔除**"的版本也会红（那时每个节点都画，包括离屏的）。
   * 那不是误报：它同时也在说"剔除没有生效"。
   */
  test('⑤ 被绘制的节点必须落在画布余量内（剔除用的变换算错会立刻红）', async ({ page }) => {
    await installNodeDotRecorder(page);
    await openLargeGraph(page);
    await forceOneRedraw(page);

    const analysis = await page.evaluate(() => {
      const store = window as unknown as { __nodeDots: Array<[number, number]> };
      const canvases = Array.from(document.querySelectorAll('canvas'));
      const canvas = canvases.sort((a, b) => b.width * b.height - a.width * a.height)[0];
      if (!canvas) return null;
      const rect = canvas.getBoundingClientRect();
      const width = rect.width;
      const height = rect.height;

      if (store.__nodeDots.length === 0) return { drawn: 0, width, height, worst: 0, outside: 0 };

      // 绘制侧的余量上界：(半径 15 + 24) * k + 40；这里取 k 的宽松上界 4 → 196
      const margin = 200;
      let outside = 0;
      let worst = 0;
      for (const [x, y] of store.__nodeDots) {
        const dx = Math.max(0, -x, x - width);
        const dy = Math.max(0, -y, y - height);
        const distance = Math.max(dx, dy);
        if (distance > margin) outside += 1;
        if (distance > worst) worst = distance;
      }

      return { drawn: store.__nodeDots.length, width, height, outside, worst, margin };
    });

    console.log('\n=== ⑤ 视口剔除正确性 ===');
    expect(analysis, '取不到画布，判据无法成立').not.toBeNull();

    if (analysis!.drawn === 0) {
      throw new Error('一个节点都没记录到 —— 画布没重绘，判据无法成立（不是"通过"）');
    }

    console.log(`  画布          ${analysis!.width} × ${analysis!.height}`);
    console.log(`  绘制记录      ${analysis!.drawn} 次（跨多帧，未去重）`);
    console.log(
      `  越界(>${analysis!.margin}px) ${analysis!.outside} 次 ｜ 最远越界 ${Math.round(analysis!.worst)} px`,
    );

    expect(
      analysis!.outside,
      `有 ${analysis!.outside} 次绘制落在画布外 ${Math.round(analysis!.worst)} px 处 —— ` +
        '说明视口剔除用的变换是错的（它以为这些节点在界内）。' +
        '变换必须取自 GraphCanvas 的 onRenderFramePre → graph2ScreenCoords(0,0)，' +
        '不能用 onZoom 回调参数（那是图空间中心，不是平移量）。',
    ).toBe(0);
  });
});
