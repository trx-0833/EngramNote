/**
 * @file 项目页头部（标题 + 副标题）
 * @description 自 `pages/Projects.tsx` 拆分（overhaul-plan 5.5），**只搬不改**。
 *
 * ## ⚠️ "只搬不改"的边界在 visual-refactor-plan 批次 C1 被有意打破一次
 *
 * 这一块此前用的是 `src/styles/assessment.css` 里的 `.assessment-header` /
 * `.assessment-title` / `.assessment-subtitle`。核对过：那是**全局**类名，
 * 判据写在 `assessment.css` 文件头（"学习评估页与项目页共用同一组类名，
 * 塞进任一页面的模块都会逼另一个页面反向 import"）—— 所以它并不是
 * "项目页借了学习评估页模块的类名"，只是两个页面共用一份全局样式。
 *
 * C1 把 18 个页面的标题统一进 `<PageHeader>` 之后，项目页与学习评估页
 * **都不再需要**那三个类名，于是它们一起退休（墓碑注释留在 `assessment.css`）。
 *
 * 逐项对照：
 *   - 标题量尺：`1.75rem` + 渐变字（A3 之后渐变只剩纯色）→ `1.5rem` 衬线 600；
 *   - 副标题量尺：`0.9rem` → `0.875rem`（`--text-base`，差 0.4px）；
 *   - 与下方内容的间距：`.assessment-header` 的 `--space-xl` → `spacing="xl"`；
 *   - 文案、DOM 语义（一级标题 + 一行副标题）、`Projects.tsx` 的调用点：**逐字未变**。
 */
import PageHeader from '../../components/PageHeader';

export default function ProjectsHeader() {
  return (
    <PageHeader
      title="项目"
      subtitle="项目作为标签归属笔记，一篇笔记可属于多个项目；所有文件统一存放在收件箱（inbox）。"
      spacing="xl"
    />
  );
}
