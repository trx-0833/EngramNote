/**
 * @file 项目卡片里的扫描结果面板
 * @description 自 `pages/Projects.tsx` 的 `renderCard` 拆分（overhaul-plan 5.5），
 * **只搬不改**：新增/跳过/不支持三个计数，以及"收件箱里没有新文件"时
 * 告诉用户文件该放哪里的说明，文案与条件（`imported === 0 && scanned === 0`）逐字保留。
 */
import type { ScanImportResponse } from '../../api/client';
import { useTranslation } from '../../i18n';

interface ScanResultPanelProps {
  result: ScanImportResponse;
}

export default function ScanResultPanel({ result }: ScanResultPanelProps) {
  const { t } = useTranslation();
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
        <span style={{ fontWeight: 600, color: 'var(--color-text)' }}>
          {t('projectPage.scanResult')}
        </span>
        <span className="status-converted" style={{ fontWeight: 600 }}>
          {t('projectPage.scanImported', { count: result.imported })}
        </span>
        <span style={{ color: 'var(--color-text-tertiary)' }}>
          {t('projectPage.scanSkipped', { count: result.skipped })}
        </span>
        <span style={{ color: 'var(--color-text-tertiary)' }}>
          {t('projectPage.scanUnsupported', { count: result.unsupported })}
        </span>
      </div>
      {result.imported === 0 && result.scanned === 0 && (
        <div style={{ marginTop: 6, color: 'var(--color-text-secondary)' }}>
          {t('projectPage.scanEmptyPrefix')}{' '}
          <code style={{ color: 'var(--color-primary)' }}>source/</code>{' '}
          {t('projectPage.scanEmptyMiddle')}{' '}
          <code style={{ color: 'var(--color-primary)' }}>Vault/source/</code>{' '}
          {t('projectPage.scanEmptySuffix')}
        </div>
      )}
    </div>
  );
}
