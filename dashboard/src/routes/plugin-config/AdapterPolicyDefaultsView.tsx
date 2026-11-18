import type { ReactNode } from 'react'

import { SegmentedControl } from '@/components/ui/management'
import { PageFrame } from '@/components/ui/surface'
import type { AdapterPolicyDefaults } from '@/lib/chat-management-api'

interface AdapterPolicyDefaultsViewProps {
  defaults?: AdapterPolicyDefaults
  disabled?: boolean
  error?: string
  accounts?: ReactNode
  onChange: (chatType: keyof AdapterPolicyDefaults, action: 'allow' | 'block') => void
}

/** 默认规则的展示层；保存、加载和账号编辑由调用者提供。 */
export function AdapterPolicyDefaultsView({
  defaults,
  disabled = false,
  error,
  accounts,
  onChange,
}: AdapterPolicyDefaultsViewProps) {
  return (
    <PageFrame asChild>
      <section
        aria-label="适配器全局默认策略"
        className="flex flex-wrap items-center justify-between gap-4 p-4"
      >
        <div className="min-w-0">
          <h2 className="text-base font-medium">黑白名单设置</h2>
        </div>
        <div className="flex flex-wrap items-center gap-4 sm:gap-5">
          {error ? (
            <p className="text-destructive text-sm" role="alert">
              {error}
            </p>
          ) : (
            <>
              {(['group', 'private'] as const).map((chatType) => {
                const label = chatType === 'group' ? '群聊' : '私聊'
                return (
                  <div key={chatType} className="flex items-center gap-2">
                    <span className="text-sm">{label}</span>
                    <SegmentedControl
                      aria-label={`${label}默认规则`}
                      value={defaults?.[chatType]}
                      onValueChange={(value) => onChange(chatType, value as 'allow' | 'block')}
                      disabled={!defaults || disabled}
                      options={[
                        { value: 'allow', label: '接收' },
                        { value: 'block', label: '不接收' },
                      ]}
                    />
                  </div>
                )
              })}
            </>
          )}
          {accounts}
        </div>
      </section>
    </PageFrame>
  )
}
