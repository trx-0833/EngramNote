/**
 * @file 项目页头部（标题 + 副标题）
 * @description 自 `pages/Projects.tsx` 拆分（overhaul-plan 5.5），**只搬不改**。
 */
export default function ProjectsHeader() {
  return (
    <div className="assessment-header">
      <h1 className="assessment-title">项目</h1>
      <p className="assessment-subtitle">
        项目作为标签归属笔记，一篇笔记可属于多个项目；所有文件统一存放在收件箱（inbox）。
      </p>
    </div>
  )
}
