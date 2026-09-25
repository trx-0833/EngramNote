/**
 * @file 视口剔除的判据（纯函数）
 *
 * ## 它解决的问题
 *
 * `force-graph` 的 `nodeVisibility` 默认恒为 `true`，而它在绘制时是**唯一的过滤条件**
 * （`force-graph.mjs:557` 的 `filter(getVisibility)`）—— 也就是说**离屏节点照样每帧
 * 走完整绘制**。1181 个节点里放大后可能只有几十个在屏幕上，另外一千多个仍在白画。
 *
 * ## ⚠️ 这里踩过一次真实的坑，读之前先看
 *
 * 第一版把判据直接写在 `GraphCanvas.tsx` 里，并且拿 `viewportRef.current` 当
 * d3-zoom 的**平移量**用。那个 ref 是 `onZoom` 写的，而 `onZoom` 收到的是
 * （`force-graph.mjs:1506`）：
 *
 *     state.onZoom({ ...zoomTransform(canvas), ...this.centerAt() })
 *
 * `centerAt()` 的 `x` / `y` 是**图空间里的视口中心**，展开在后面会把变换自己的
 * `t.x` / `t.y` **覆盖掉**。再加上页面初值是 `{ k: 1, x: 0, y: 0 }`，而真实初值是
 * `{ k: 1, x: width/2, y: height/2 }`（`force-graph.mjs:999-1002`）——
 * 于是"可见区域"被算成了图空间左上角那一块：**左半边的节点被藏起来，
 * 右半边的界外节点反倒被画了出来**。用户看到的就是"有链接的节点也不见了"。
 *
 * 现在变换由 `GraphCanvas` 的 `onRenderFramePre` 每帧用
 * `graph2ScreenCoords(0, 0)` 取真值写入（那是库自己的换算，恒等于 `t.x` / `t.y`）。
 * 本文件只管**给定正确变换之后**的判据，因此它是纯函数、可以被单测逐点压住。
 *
 * ## 约定
 *
 * `force-graph.mjs:1501-1503` 把变换施加在上下文上：
 *
 *     ctx.translate(t.x, t.y); ctx.scale(t.k, t.k);
 *
 * 即 `screen = world * k + (x, y)`，`world` 就是节点的 `node.x` / `node.y`。
 */
import { type ForceGraphNode, getNodeSize } from './types';

/** d3-zoom 变换：`screen = world * k + (x, y)`，`x` / `y` 是**平移量** */
export interface ViewportTransform {
  k: number;
  x: number;
  y: number;
}

/** 画布尺寸（CSS 像素，与 `force-graph` 的 `state.width` / `state.height` 同口径） */
export interface ViewportSize {
  width: number;
  height: number;
}

/**
 * 节点半径之外的固定余量（**屏幕像素**）：标签底板高度 + 抖动。
 *
 * 给得宽是刻意的：剔除错一个边缘节点的代价是"它忽然不见了"，
 * 比多画几个节点难看得多。
 */
export const VIEWPORT_MARGIN_PX = 40;

/**
 * 节点是否可能出现在屏幕上。
 *
 * 坐标还没算出来的节点（力导向起步阶段 `x` / `y` 是 undefined）一律放行 ——
 * 交给绘制侧那条被测试压住的守卫处理，这里不重复判断。
 */
export function isNodeInViewport(
  node: Pick<ForceGraphNode, 'x' | 'y' | 'relation_count'>,
  transform: ViewportTransform,
  size: ViewportSize,
): boolean {
  if (node.x == null || node.y == null) return true;
  const { k, x, y } = transform;
  const screenX = node.x * k + x;
  const screenY = node.y * k + y;
  // 半径与余量都在屏幕空间：半径随缩放变，余量不随
  const margin = (getNodeSize(node as ForceGraphNode) + 24) * k + VIEWPORT_MARGIN_PX;
  return (
    screenX >= -margin &&
    screenX <= size.width + margin &&
    screenY >= -margin &&
    screenY <= size.height + margin
  );
}
