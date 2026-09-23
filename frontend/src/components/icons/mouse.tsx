/**
 * @file 图标图形：mouse（鼠标）
 * @description 图形意图：**一只立着的鼠标** —— 10×17 的圆角矩（上下两端各 r=5，
 * 于是收成两个半圆头），鼠身上部一道 3.5 长的竖线当滚轮。
 * 替换的 emoji：`\u{1F5B1}` 🖱（`MarkdownReader.tsx:58` 的"鼠标跟随"提示）——
 * 那是这一行 0.8rem 灰色提示里唯一一个彩色字形。
 *
 * 只提供图形本身：`<svg>` 外壳（viewBox / 线宽 1.5 / currentColor / 尺寸 / 无障碍）
 * 统一由 `Icon.tsx` 负责。
 */
export default function Mouse() {
  return (
    <>
      {/* 鼠身：10×17，上下各一个 r=5 的半圆头 */}
      <rect x="7" y="3.5" width="10" height="17" rx="5" />
      {/* 滚轮：自鼠身顶内 4 处起的一段竖线 */}
      <path d="M12 7.5v3.5" />
    </>
  );
}
