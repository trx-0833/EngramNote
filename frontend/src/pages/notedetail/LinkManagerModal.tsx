/**
 * @file 「管理关联资料」弹窗
 * @description
 * 由 `pages/NoteDetail.tsx` 拆分而来（overhaul-plan 5.5），只做搬运：
 * 遮罩点击关闭、内容区 stopPropagation、勾选逻辑与按钮文案均与拆分前一致。
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
