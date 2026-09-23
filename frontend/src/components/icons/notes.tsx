/**
 * @file 图标图形：notes（笔记列表）
 * @description 图形意图：**线装册页** —— 一册竖长的封面，左缘三道横向的装订线
 * （线头与封面左缘相接，读作"订书的线"而不是"列表项"）。
 * 替换的 Unicode：`\u2630` ☰（`Sidebar.tsx:41`）—— 三条横线，是"菜单"不是"笔记"。
 *
 * 只提供图形本身：`<svg>` 外壳（viewBox / 线宽 1.5 / currentColor / 尺寸 / 无障碍）
 * 统一由 `Icon.tsx` 负责。
 */
export default function Notes() {
  return (
    <>
      {/* 封面：竖长矩形（14.5×17，比 `projects` 的函套更瘦更高） */}
      <rect x="6" y="3.5" width="14.5" height="17" rx="2" />
      {/* 三道装订线：自封面左缘向左伸出 3 单位，间距 4.5（上下留白对称） */}
      <path d="M3 8h3M3 12.5h3M3 17h3" />
    </>
  );
}
