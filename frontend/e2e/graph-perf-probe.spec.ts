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
import { test, type Page } from '@playwright/test';

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
      isolated_nodes: graph.nodes.length - REAL_SHAPE.touchedNodes,
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
  await page.addInitScript((names: string[]) => {
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
  }, CANVAS_CALLS as unknown as string[]);
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
});
