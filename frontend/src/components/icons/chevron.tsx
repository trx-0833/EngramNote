/**
 * @file 图标图形：chevron（折叠 / 展开指示）
 * @description 图形意图：**一个指向右的折线** —— 自 (9,6) 起笔，折点在右侧中点 (15,12)，
 * 收在左下 (9,18)。它本身不表达"开还是关"：开关状态由宿主的 `aria-expanded` 承担，
 * 展开时由 `.collapse-arrow-open` 把它整体 `rotate(90deg)` 转成朝下
 * （`styles/learning.css` 的共享类，4 个页面共用同一份过渡）。
 *
 * 替换的 Unicode：`\u25B6` ▶（`KnowledgeCards.tsx:309` / `QuestionSets.tsx:282` /
 * `projects/ProjectCard.tsx:250` / `DailyMaterials.tsx:564`）——
 * `▶` 是**媒体播放**符号，而且它只存在于字体里：字号一变宽高比就跟着变，
 * 旋转过渡作用在一个字符上时还会受基线影响而偏移。
 *
 * 只提供图形本身：`<svg>` 外壳（viewBox / 线宽 1.5 / currentColor / 尺寸 / 无障碍）
 * 统一由 `Icon.tsx` 负责。
 */
export default function Chevron() {
  return (
    <>
      {/* 折线：开口朝左、尖端朝右；宽 6、高 12，中心落在 (12,12) */}
      <path d="m9 6 6 6-6 6" />
    </>
  );
}
