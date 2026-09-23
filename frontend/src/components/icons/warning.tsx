/**
 * @file 图标图形：warning（警告）
 * @description 图形意图：**三角叹号** —— 顶角 60° 的等边三角（底边横贯 2.5 → 21.5），
 * 三角内一支叹号：竖线从 9.8 到 14.2，下面一个点（用一段极短线 `h.01`
 * 靠 `round` 端点撑成圆点，与 Lucide 同一手法）。
 * 替换的 Unicode：`\u26A0` ⚠（`KnowledgeCards.tsx:379` 的"难点"标记）——
 * 它跨平台会被渲染成彩色 emoji，且与 `\uFE0F` 变体选择符纠缠。
 *
 * 只提供图形本身：`<svg>` 外壳（viewBox / 线宽 1.5 / currentColor / 尺寸 / 无障碍）
 * 统一由 `Icon.tsx` 负责。
 */
export default function Warning() {
  return (
    <>
      {/* 三角：M 顶角 → 隐式 L 左下 → h 底边 → z 回顶角 */}
      <path d="M12 3.8 2.5 20.2h19z" />
      {/* 叹号竖线 */}
      <path d="M12 9.8v4.4" />
      {/* 叹号圆点 */}
      <path d="M12 17.4h.01" />
    </>
  );
}
