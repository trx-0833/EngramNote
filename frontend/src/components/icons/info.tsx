/**
 * @file 图标图形：info（提示）
 * @description 图形意图：**圆内 i** —— 外环 r=8.5（与 `success` / `error` / `warning`
 * 三个状态图标同一档大小），环内一竖（自 11 到 16.5）与上方一个点
 * （一段极短线 `h.01`，靠 `round` 端点撑成圆点，与 `warning` 的叹号点同一手法）。
 *
 * 为什么这一批必须有它：`Toast.tsx` 的四种提示共用同一个图形槽
 * （`KIND_STYLE[kind].icon`），B3 把其中的 ✓ / ✕ / `!` 换成
 * `success` / `error` / `warning` 之后，若 `info` 还留着裸字母 `i`，
 * 同一槽里就会有两种画法（三个矢量图标 + 一个字体字母），
 * 而"同一槽不许有两种画法"正是这一批要修的病。
 *
 * 只提供图形本身：`<svg>` 外壳（viewBox / 线宽 1.5 / currentColor / 尺寸 / 无障碍）
 * 统一由 `Icon.tsx` 负责。
 */
export default function Info() {
  return (
    <>
      {/* 外环：与 success / error / warning 同参数（r=8.5） */}
      <circle cx="12" cy="12" r="8.5" />
      {/* 竖干：自 11 落到 16.5 */}
      <path d="M12 11v5.5" />
      {/* 点：居上，与竖干留 1.3 的空隙 */}
      <path d="M12 7.8h.01" />
    </>
  );
}
