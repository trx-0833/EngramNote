/**
 * @file 图标图形：projects（项目）
 * @description 图形意图：**书匣 / 函套** —— 一个函套外框，上部一道匣盖分界，
 * 下部正中一个扣手（指孔）。匣是"装东西的容器"，对应"项目"把多条笔记收在一处。
 * 替换的 Unicode：`\u25A3` ▣（`Sidebar.tsx:33`）—— 一个填充方块，什么也说明不了。
 *
 * 只提供图形本身：`<svg>` 外壳（viewBox / 线宽 1.5 / currentColor / 尺寸 / 无障碍）
 * 统一由 `Icon.tsx` 负责。
 */
export default function Projects() {
  return (
    <>
      {/* 函套外框：17×15 的圆角矩（比 `notes` 更矮更宽，一眼分得开） */}
      <rect x="3.5" y="4.5" width="17" height="15" rx="2" />
      {/* 匣盖分界：横贯整个外框宽度 */}
      <path d="M3.5 9.5h17" />
      {/* 扣手（指孔）：匣体下部正中一小段横线 */}
      <path d="M10 14.5h4" />
    </>
  );
}
