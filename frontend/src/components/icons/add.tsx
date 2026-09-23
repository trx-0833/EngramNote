/**
 * @file 图标图形：add（新增）
 * @description 图形意图：**加号** —— 一横一竖，两笔等长（各 14）、交点即几何中心，
 * 正负形完全对称。此前全站同时存在两种写法：全角 `＋`（`NewProjectForm.tsx:77`）
 * 与 ASCII `+`（`Sidebar.tsx:179`）—— 同一语义两个字形，且都跟正文用同一个字体。
 *
 * 只提供图形本身：`<svg>` 外壳（viewBox / 线宽 1.5 / currentColor / 尺寸 / 无障碍）
 * 统一由 `Icon.tsx` 负责。
 */
export default function Add() {
  return (
    <>
      {/* 竖画 */}
      <path d="M12 5v14" />
      {/* 横画 */}
      <path d="M5 12h14" />
    </>
  );
}
