/**
 * @file 图标图形：success（成功 / 已覆盖）
 * @description 图形意图：**圆内勾** —— 外环 r=8.5（四周留 2px 呼吸边），
 * 环内一勾：起笔在左下（8.2,12.3），折点在正下方偏左，收笔指向右上。
 * 替换的 Unicode：`\u2713` ✓（`Toast.tsx:66` 的提示图标、`LearningAssessment.tsx:607`
 * 的"已覆盖知识点"）—— 裸勾没有"完成"的边界感，且字号一变粗细就跟着变。
 *
 * 与 `error` 同参数（r=8.5）：两个状态图标必须同尺寸才并排得住。
 *
 * 只提供图形本身：`<svg>` 外壳（viewBox / 线宽 1.5 / currentColor / 尺寸 / 无障碍）
 * 统一由 `Icon.tsx` 负责。
 */
export default function Success() {
  return (
    <>
      {/* 外环 */}
      <circle cx="12" cy="12" r="8.5" />
      {/* 勾：两段长度不等（3.7 与 6.8），长的那段指向右上，读作"完成了" */}
      <path d="m8.2 12.3 2.6 2.6 5-5.5" />
    </>
  );
}
