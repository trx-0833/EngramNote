/**
 * @file 图标图形：error（错误 / 未覆盖）
 * @description 图形意图：**圆内叉** —— 与 `success` 逐参数相同的外环（r=8.5），
 * 环内一个两笔等长、交点在环心的叉。
 * 替换的 Unicode：`\u2717` ✗（`LearningAssessment.tsx:621` 的"未覆盖知识点"）
 * —— 它与 `success` 的 ✓ 在中文字体里宽度、基线都不一致，
 * 两栏并排时左栏是"✓"右栏是"✗"，看起来像两套符号。
 *
 * 只提供图形本身：`<svg>` 外壳（viewBox / 线宽 1.5 / currentColor / 尺寸 / 无障碍）
 * 统一由 `Icon.tsx` 负责。
 */
export default function Error() {
  return (
    <>
      {/* 外环：与 `success` 同参数，保证两个状态图标并排时一样大 */}
      <circle cx="12" cy="12" r="8.5" />
      {/* 叉：两笔各 7.6 长，落在环内（对角半径 3.8 < 8.5） */}
      <path d="m9.3 9.3 5.4 5.4" />
      <path d="M14.7 9.3 9.3 14.7" />
    </>
  );
}
