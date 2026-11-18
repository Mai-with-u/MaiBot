import { useTranslation } from 'react-i18next'

import { Button } from '@/components/ui/button'
import { StatusLight } from '@/components/ui/status-light'

export interface ConnectionTestRoleResult {
  role: 'planner' | 'replyer'
  success: boolean
  latencyMs: number | null
  error: string | null
}

export type ConnectionTestState =
  | { status: 'idle' }
  | { status: 'testing' }
  | { status: 'done'; results: ConnectionTestRoleResult[] }
  | { status: 'error'; error: string }

/** 向导里的模型连通性测试：结果仅供参考，不影响完成配置。 */
export function ConnectionTestPanel({
  state,
  onTest,
  disabled,
}: {
  state: ConnectionTestState
  onTest: () => void
  disabled?: boolean
}) {
  const { t } = useTranslation()
  const testing = state.status === 'testing'

  return (
    <div className="border-border mt-4 space-y-3 border-t-2 pt-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-muted-foreground text-sm">{t('setupPage.connectionTest.hint')}</p>
        <Button type="button" variant="outline" size="sm" onClick={onTest} disabled={disabled || testing}>
          {testing ? (
            <>
              <div className="mr-2 h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
              {t('setupPage.connectionTest.testing')}
            </>
          ) : (
            t('setupPage.connectionTest.button')
          )}
        </Button>
      </div>

      {state.status === 'error' && (
        <p role="alert" className="text-destructive text-sm">
          {t('setupPage.connectionTest.saveFailed', { error: state.error })}
        </p>
      )}

      {state.status === 'done' && (
        <ul className="space-y-2" aria-live="polite">
          {state.results.map((result) => (
            <li key={result.role} className="flex min-w-0 items-start gap-2 text-sm">
              <StatusLight
                status={result.success ? 'on' : 'error'}
                glow={false}
                label={t(result.success ? 'setupPage.connectionTest.passed' : 'setupPage.connectionTest.failed')}
              />
              <div className="min-w-0">
                <span className="font-medium">{t(`setupPage.connectionTest.roles.${result.role}`)}</span>
                {' · '}
                {result.success
                  ? t('setupPage.connectionTest.passedWithLatency', {
                      latency: result.latencyMs != null ? `${(result.latencyMs / 1000).toFixed(2)}s` : '-',
                    })
                  : t('setupPage.connectionTest.failed')}
                {!result.success && result.error && (
                  <p className="text-muted-foreground mt-0.5 line-clamp-3 break-all text-xs">{result.error}</p>
                )}
              </div>
            </li>
          ))}
          {state.results.some((result) => !result.success) && (
            <li className="text-muted-foreground text-xs">{t('setupPage.connectionTest.canContinue')}</li>
          )}
        </ul>
      )}
    </div>
  )
}
