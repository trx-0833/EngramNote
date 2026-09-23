/**
 * @file 项目页底部的使用说明（常驻，不随列表状态消失）
 * @description 自 `pages/Projects.tsx` 拆分（overhaul-plan 5.5），**只搬不改**。
 */
export default function ProjectsUsageNotes() {
  return (
    <div
      className="card"
      style={{
        marginTop: 24,
        background: 'var(--color-bg)',
        border: '1px dashed var(--color-border)',
        fontSize: '0.85rem',
        color: 'var(--color-text-secondary)',
        lineHeight: 1.8,
      }}
    >
      <strong style={{ color: 'var(--color-text)' }}>📖 使用说明</strong>
      <ol style={{ margin: '8px 0 0 20px', padding: 0 }}>
        <li>项目是纯标签：一篇笔记可打上多个项目标签，创建项目不会生成物理文件夹。</li>
        <li>
          所有文件统一存放在收件箱（inbox）的{' '}
          <code style={{ color: 'var(--color-primary)' }}>source/</code> 目录。
        </li>
        <li>
          把文件直接拷贝到收件箱 <code style={{ color: 'var(--color-primary)' }}>source/</code>{' '}
          后，点击「扫描导入」即可识别为笔记并打上当前项目标签。
        </li>
        <li>也可以在上传页选择多个项目标签，通过网页直接上传文件。</li>
      </ol>
    </div>
  );
}
