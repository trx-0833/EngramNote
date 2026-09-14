/**
 * @file 「管理关联资料」弹窗
 * @description
 * 由 `pages/NoteDetail.tsx` 拆分而来（overhaul-plan 5.5），只做搬运：
 * 遮罩点击关闭、内容区 stopPropagation、勾选逻辑与按钮文案均与拆分前一致。
 *
 * ## 本组件为什么**没有**模块样式（overhaul-plan 5.6 序 12 + 收尾轮死代码清理）
 *
 * `refinements.css` 里那一整套 `[style*="rgba(0,0,0,0.5)"] …`（12 条）与
 * 5 个预留语义化类（`.link-modal` / `.material-list-item`(-selected) /
 * `.type-badge`(-material)）看起来都该住在这里，但两者都**没有**搬进来，
 * 而且**现在都已经删掉了**：
 *
 * 1. **属性选择器那 12 条：删了**。真 Chromium 实测 `[style*="rgba(0,0,0,0.5)"]`
 *    命中 **0** 个元素 —— React 走 CSSOM 赋内联值，浏览器把颜色重新序列化成
 *    带空格的形式（`rgba(0, 0, 0, 0.5)`），不带空格的子串永远匹配不上。
 *    也就是说这些规则**迁移前就从未生效过**，删它是可证明的空操作。
 *    实测输出与逐条登记见 `scripts/css-migration-diff.mjs` 的
 *    `BATCHES[].resolvedConflicts` 与 `docs/migration-evidence/5.6-10-*.md`。
 * 2. **5 个预留类：删了**（序 12 先把它们从 `refinements.css` 逐字搬进
 *    `styles/components.css`，收尾轮的死代码清理再整条删除）。
 *    理由是机械的：它们**零引用**，本组件一个类名都没挂；把它们放进本目录的
 *    `LinkManagerModal.module.css` 也不行 —— 那个模块**没有任何 tsx import**
 *    ⇒ Vite 不会把它编进产物 ⇒ 规则会从产物里凭空消失
 *    （规则清单差集当场报 6 条丢失，这是实测踩到的）。
 *    删除的逐条证据（`src/**` grep 0 处 + 运行时拼类名 0 处 + 产物 0 次）
 *    见 `docs/migration-evidence/5.6-13-dead-css-cleanup.md`。
 *
 * 所以本组件两轮都**只改文档、不改 DOM**：弹窗仍然用内联样式渲染，
 * 外观逐属性不变（探针 `notedetail-link-modal` 场景对账过 computed style）。
 *
 * 将来要收口的话，正确的一轮是："把内联样式改成类 + 给遮罩一个真类名 +
 * 重新设计那几个类的样式"，那是**改外观**的改动，必须带截图对比 ——
 * 而不是混在"证明什么都没丢"的迁移批里（规范 §3 雷区 4）。
 */
import type { Note } from '../../api/client'

interface LinkManagerModalProps {
  /** 可关联的学习资料列表 */
  availableMaterials: Note[]
  /** 已勾选的资料 ID */
  linkMaterialIds: string[]
  /** 更新勾选结果（全量数组） */
  onLinkMaterialIdsChange: (updater: (prev: string[]) => string[]) => void
  /** 关闭弹窗 */
  onClose: () => void
  /** 保存关联资料 */
  onSave: () => void
}

/** 链接管理弹窗：选择关联的学习资料 */
export default function LinkManagerModal({
  availableMaterials,
  linkMaterialIds,
  onLinkMaterialIdsChange,
  onClose,
  onSave,
}: LinkManagerModalProps) {
  return (
    <div
      style={{
        position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
        background: 'rgba(0,0,0,0.5)', zIndex: 1000,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}
      onClick={onClose}
    >
      <div
        className="card"
        style={{
          maxWidth: '500px', width: '90%', maxHeight: '70vh', overflowY: 'auto',
          padding: '1.5rem',
        }}
        onClick={e => e.stopPropagation()}
      >
        <h3 style={{ marginBottom: '1rem' }}>管理关联资料</h3>
        {availableMaterials.length === 0 ? (
          <p style={{ color: 'var(--color-text-secondary)' }}>暂无可关联的资料</p>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', marginBottom: '1rem' }}>
            {availableMaterials.map(m => (
              <label key={m.id} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', cursor: 'pointer' }}>
                <input
                  type="checkbox"
                  checked={linkMaterialIds.includes(m.id)}
                  onChange={(e) => {
                    onLinkMaterialIdsChange(prev =>
                      e.target.checked ? [...prev, m.id] : prev.filter(id => id !== m.id)
                    )
                  }}
                />
                <span>{m.title}</span>
              </label>
            ))}
          </div>
        )}
        <div style={{ display: 'flex', gap: '0.5rem', justifyContent: 'flex-end' }}>
          <button className="btn btn-secondary" onClick={onClose}>取消</button>
          <button className="btn btn-primary" onClick={onSave}>保存</button>
        </div>
      </div>
    </div>
  )
}
