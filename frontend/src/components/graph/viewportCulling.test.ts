/**
 * @file 视口剔除判据的单测
 *
 * ## 为什么必须有这个文件
 *
 * 判据第一版写在组件里，坐标换算错了，**真机上把左半边的节点藏了起来**
 * （用户看到的："有链接的某些节点也被隐藏了"）。页面级用例抓不到它 ——
 * 那些用例的桩只有 4 个节点，走的是不剔除的路径。
 *
 * 错在哪（`viewportCulling.ts` 文件头有完整说明）：拿 `onZoom` 回调参数当
 * d3-zoom 的平移量，而它是**图空间里的视口中心**；真实初值是
 * `{ k: 1, x: width/2, y: height/2 }`，页面里却停在 `{ k: 1, x: 0, y: 0 }`。
 *
 * 抽成纯函数之后，这些点可以逐条钉死 —— 尤其是 **`x` / `y` 是平移量、
 * 世界坐标要乘 `k` 再加平移**这一条，正是当初写错的地方。
 */
import { describe, expect, it } from 'vitest';

import { VIEWPORT_MARGIN_PX, isNodeInViewport } from './viewportCulling';

const SIZE = { width: 1000, height: 800 };
const IDENTITY = { k: 1, x: 0, y: 0 };

/** `relation_count = 0` → `getNodeSize` = 5 */
function node(x?: number, y?: number, relationCount = 0) {
  return { x, y, relation_count: relationCount };
}

describe('坐标换算（当初写错的就是这里）', () => {
  it('★ x / y 是**平移量**：世界坐标要乘 k 再加平移', () => {
    // 世界坐标 (600, 500)，变换 k=1 / 平移 (300, 200) → 屏幕 (900, 700)：界内
    expect(isNodeInViewport(node(600, 500), { k: 1, x: 300, y: 200 }, SIZE)).toBe(true);

    // 同一节点、同一个平移，但 k=2 → 屏幕 (1500, 1200)：界外
    expect(isNodeInViewport(node(600, 500), { k: 2, x: 300, y: 200 }, SIZE)).toBe(false);
  });

  it('★ 真实初值 k=1 / 平移 (width/2, height/2)：图原点落在画布中心', () => {
    const initial = { k: 1, x: SIZE.width / 2, y: SIZE.height / 2 };
    // 图原点 → 屏幕正中心，必须在
    expect(isNodeInViewport(node(0, 0), initial, SIZE)).toBe(true);
    // 左上角外侧（世界 -600, -500 → 屏幕 -100, -100）：余量之外，剔除
    expect(isNodeInViewport(node(-600, -500), initial, SIZE)).toBe(false);
  });

  it('把平移量当成"图空间中心"会让左半边被误判 —— 这就是那个回归', () => {
    // 待判节点：世界 (-550, 0)。真实变换 k=1 / 平移 (500, 400) → 屏幕 (-50, 400)：
    // 离画布左边 50 px，在余量 69 px 之内 → 该画
    const truth = { k: 1, x: 500, y: 400 };
    expect(isNodeInViewport(node(-550, 0), truth, SIZE)).toBe(true);

    // 错误写法：把"视口中心在图空间的位置"当成平移量用。
    // 视口中心在图空间是 ((1000/2 - 500)/1, (800/2 - 400)/1) = (0, 0)，
    // 拿它当平移 → 屏幕 (-550, 0)，离左边 550 px → 被误判为界外，节点就此消失。
    const misinterpreted = { k: 1, x: 0, y: 0 };
    expect(isNodeInViewport(node(-550, 0), misinterpreted, SIZE)).toBe(false);
  });
});

describe('余量', () => {
  it('刚好落在画布外、但在余量内 → 仍然绘制（宁可多画，不要忽然消失）', () => {
    // relation_count = 0 → 半径 5；margin = (5 + 24) * 1 + 40 = 69
    const margin = (5 + 24) * 1 + VIEWPORT_MARGIN_PX;
    expect(isNodeInViewport(node(-(margin - 1), 400), IDENTITY, SIZE)).toBe(true);
    expect(isNodeInViewport(node(-(margin + 1), 400), IDENTITY, SIZE)).toBe(false);
  });

  it('余量随缩放放大（节点半径在屏幕上也随缩放放大）', () => {
    /*
     * 同样的**屏幕偏移** -126 px，两种缩放下的结论必须不同：
     *   k=3 → 余量 = (5 + 24) * 3 + 40 = 127 → 界内
     *   k=1 → 余量 = (5 + 24) * 1 + 40 =  69 → 界外
     * 这正是"余量里的半径项要乘 k、常量项不乘"的判据。
     *
     * ⚠️ y 也要给 0：`screenY = y * k`，给个 400 在 k=3 下就变成 1200，
     * 会被 y 轴判出去（第一版用例就是这么写错的 —— 量的是实现，错的是用例）。
     */
    const screenOffset = -126;
    expect(isNodeInViewport(node(screenOffset / 3, 0), { k: 3, x: 0, y: 0 }, SIZE)).toBe(true);
    expect(isNodeInViewport(node(screenOffset, 0), { k: 1, x: 0, y: 0 }, SIZE)).toBe(false);
  });

  it('枢纽节点（半径更大）的余量也更大', () => {
    // relation_count = 20 → 半径被夹到 15；margin = (15 + 24) * 1 + 40 = 79
    const bigMargin = (15 + 24) * 1 + VIEWPORT_MARGIN_PX;
    expect(isNodeInViewport(node(-(bigMargin - 1), 400, 20), IDENTITY, SIZE)).toBe(true);
    expect(isNodeInViewport(node(-(bigMargin + 1), 400, 20), IDENTITY, SIZE)).toBe(false);
  });
});

describe('放行的情形（不剔除）', () => {
  it('坐标还没算出来（力导向起步阶段）→ 放行，交给绘制侧的守卫', () => {
    expect(isNodeInViewport(node(undefined, undefined), IDENTITY, SIZE)).toBe(true);
    expect(isNodeInViewport(node(undefined, 0), IDENTITY, SIZE)).toBe(true);
  });

  it('画布四个角与正中心都在界内', () => {
    for (const [sx, sy] of [
      [0, 0],
      [SIZE.width, 0],
      [0, SIZE.height],
      [SIZE.width, SIZE.height],
      [SIZE.width / 2, SIZE.height / 2],
    ]) {
      expect(isNodeInViewport(node(sx, sy), IDENTITY, SIZE)).toBe(true);
    }
  });
});
