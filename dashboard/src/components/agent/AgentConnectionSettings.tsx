import { useTranslation } from 'react-i18next'

import type { FieldHookComponentProps } from '@/lib/field-hooks'

export function AgentConnectionSettings({ children, parentValues }: FieldHookComponentProps) {
  const { t } = useTranslation()
  return <div className="space-y-3">
    {children}
    {parentValues?.connection === 'dsh' && <details className="rounded-lg border p-3 text-sm">
      <summary className="cursor-pointer font-medium">{t('chat.computer.dshSetup')}</summary>
      <ol className="mt-3 list-decimal space-y-2 pl-5 text-muted-foreground">
        <li>{t('chat.computer.dshInstall')} <code className="select-all">npm install -g @deepseek-ai/dsh</code></li>
        <li>{t('chat.computer.dshModel')} <code className="select-all">dsh web</code></li>
        <li>{t('chat.computer.dshEnable')}</li>
      </ol>
      <p className="mt-3 text-muted-foreground">{t('chat.computer.dshCommand')} <code>dsh --profile acp</code></p>
    </details>}
    {parentValues?.connection === 'dsh_desktop' && <details open className="rounded-lg border p-3 text-sm">
      <summary className="cursor-pointer font-medium">{t('chat.computer.desktopSetup')}</summary>
      <p className="mt-3 text-muted-foreground">{t('chat.computer.desktopInstall')}</p>
      <code className="mt-2 block select-all overflow-x-auto text-xs">dsh plugin --profile desktop add https://github.com/SengokuCola/dsh-maibot-bridge/releases/download/v0.1.1/dsh-maibot-bridge-0.1.1.tgz</code>
      <p className="mt-3 text-muted-foreground">{t('chat.computer.desktopConnect')}</p>
    </details>}
  </div>
}

export function AgentCustomArgs({ children, parentValues }: FieldHookComponentProps) {
  return parentValues?.connection === 'custom' ? children : null
}

export function AgentACPField({ children, parentValues, fieldPath }: FieldHookComponentProps) {
  if (parentValues?.connection === 'dsh_desktop' && ['agent.acp_command', 'agent.acp_env'].includes(fieldPath)) return null
  return children
}

export function AgentDesktopField({ children, parentValues }: FieldHookComponentProps) {
  return parentValues?.connection === 'dsh_desktop' ? children : null
}
