/**
 * @file 图标图形：zoom-out（缩小）
 * @description 图形意图：与 `zoom-in` **逐点相同，只少一笔** —— 同样的镜圈（r=7）、
 * 同样的手柄，圈内只留那一道 6 长的横线。
 * 两个按钮必须"只差一根竖画"，用户才能一眼看出它们是一对反操作；
 * 若各画各的，就会出现"放大镜大小不同、手柄角度不同"的假差异。
 * 替换的 Unicode：`\u2212` −（`GraphCanvas.tsx:148`）——
 * 它是**数学减号**，不是连字符也不是 ASCII 减号，跨字体宽度会变。
 *
 * 只提供图形本身：`<svg>` 外壳（viewBox / 线宽 1.5 / currentColor / 尺寸 / 无障碍）
 * 统一由 `Icon.tsx` 负责。
 */
export default function ZoomOut() {
  return (
    <>
      {/* 镜圈（与 zoom-in 同参数） */}
      <circle cx="10.5" cy="10.5" r="7" />
      {/* 手柄（与 zoom-in 同参数） */}
      <path d="m15.5 15.5 5 5" />
      {/* 圈内减号：只留横线 */}
      <path d="M7.5 10.5h6" />
    </>
  );
}
