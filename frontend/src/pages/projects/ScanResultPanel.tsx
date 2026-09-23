/**
 * @file 项目卡片里的扫描结果面板
 * @description 自 `pages/Projects.tsx` 的 `renderCard` 拆分（overhaul-plan 5.5），
 * **只搬不改**：新增/跳过/不支持三个计数，以及"收件箱里没有新文件"时
 * 告诉用户文件该放哪里的说明，文案与条件（`imported === 0 && scanned === 0`）逐字保留。
 */
import type { ScanImportResponse } from '../../api/client';

interface ScanResultPanelProps {
  result: ScanImportResponse;
}

export default function ScanResultPanel({ result }: ScanResultPanelProps) {
  return (
    <div
      style={{
        background: 'var(--color-bg)',
        border: '1px solid var(--color-border-light)',
        borderRadius: 'var(--radius-sm)',
        padding: 10,
        fontSize: '0.8rem',
      }}
    >
      <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
        <span style={{ fontWeight: 600, color: 'var(--color-text)' }}>扫描结果</span>
        <span className="status-converted" style={{ fontWeight: 600 }}>
          新增 {result.imported}
        </span>
        <span style={{ color: 'var(--color-text-tertiary)' }}>跳过 {result.skipped}</span>
        <span style={{ color: 'var(--color-text-tertiary)' }}>不支持 {result.unsupported}</span>
      </div>
      {result.imported === 0 && result.scanned === 0 && (
        <div style={{ marginTop: 6, color: 'var(--color-text-secondary)' }}>
          未在收件箱 <code style={{ color: 'var(--color-primary)' }}>source/</code>{' '}
          目录发现新文件。可把文件拷贝到{' '}
          <code style={{ color: 'var(--color-primary)' }}>Vault 根目录的 source/</code> 后再扫描。
        </div>
      )}
    </div>
  );
}
