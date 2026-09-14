/**
 * @file 「重试转换」按钮
 * @description
 * 由 `pages/NoteDetail.tsx` 拆分而来（overhaul-plan 5.5），只做搬运：
 * 成功时只更新 status/error_message（不整页刷新）、失败走 toast.error，
 * 与拆分前逐字一致。
 */
import { retryConvert, type RetryConvertOutcome } from '../../api/client'
import { useToast } from '../../components/Toast'

interface RetryConvertButtonProps {
  /** 当前笔记 ID（缺失时不发起请求） */
  noteId: string | undefined
  /**
   * 局部合并重试结果到当前笔记 state
   *
   * 阶段 5.1 / S2：入参改用 `retryConvert` 的生成返回类型（`status` 是
   * `NoteStatus` 枚举、`error_message` 可缺省且可空），不再手抄一份形状。
   */
  onRetried: (next: RetryConvertOutcome) => void
}

/** 转换失败提示旁的重试按钮 */
export default function RetryConvertButton({ noteId, onRetried }: RetryConvertButtonProps) {
  const toast = useToast()

  return (
    <button
      className="btn btn-primary"
      style={{ fontSize: '0.75rem', padding: '4px 12px' }}
      onClick={async () => {
        if (!noteId) return
        try {
          const result = await retryConvert(noteId)
          onRetried({ status: result.status, error_message: result.error_message })
        } catch (err) {
          toast.error(err instanceof Error ? err.message : '重试失败')
        }
      }}
    >
      重试转换
    </button>
  )
}
