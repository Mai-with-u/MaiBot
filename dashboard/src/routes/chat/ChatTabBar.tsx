import { Bot, Plus, UserCircle2, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { cn } from '@/lib/utils'

import type { ChatTab } from './types'
import { getChatTabDisplayName } from './utils'

interface ChatTabBarProps {
  tabs: ChatTab[]
  activeTabId: string
  onSwitch: (tabId: string) => void
  onClose: (tabId: string, e?: React.MouseEvent | React.KeyboardEvent) => void
  onAddVirtual: () => void
}

/**
 * 移动端横向会话切换条：在窄屏隐藏侧边栏时使用，保持与桌面端一致的视觉语言。
 */
export function ChatTabBar({ tabs, activeTabId, onSwitch, onClose, onAddVirtual }: ChatTabBarProps) {
  const { t } = useTranslation()

  return (
    <div className="bg-card/85 supports-backdrop-filter:bg-card/65 shrink-0 border-b backdrop-blur">
      <div className="scrollbar-thin flex items-center gap-1 overflow-x-auto px-3 py-2">
        {tabs.map((tab) => {
          const active = activeTabId === tab.id
          const Icon = tab.type === 'virtual' ? UserCircle2 : Bot
          const displayName = getChatTabDisplayName(tab, t('chat.botNameFallback'))
          return (
            <div
              key={tab.id}
              className={cn(
                'group flex shrink-0 items-center rounded-full border text-xs transition',
                active
                  ? 'bg-primary text-primary-foreground border-transparent shadow-sm'
                  : 'bg-background/60 text-muted-foreground hover:text-foreground hover:bg-background border-transparent'
              )}
            >
              <button
                type="button"
                className="flex items-center gap-1.5 rounded-full px-3 py-1.5"
                onClick={() => onSwitch(tab.id)}
              >
                <Icon className="h-3.5 w-3.5" />
                <span className="max-w-32 truncate font-medium">{displayName}</span>
                <span
                  aria-hidden
                  className={cn(
                    'h-1.5 w-1.5 rounded-full transition-colors',
                    active
                      ? tab.isConnected
                        ? 'bg-primary-foreground'
                        : 'bg-primary-foreground/50'
                      : tab.isConnected
                        ? 'bg-emerald-500'
                        : 'bg-muted-foreground/40'
                  )}
                />
              </button>
              {tab.id !== 'webui-default' && (
                <button
                  type="button"
                  aria-label={t('chat.sidebar.closeConversation', { label: displayName })}
                  className={cn(
                    'mr-1 rounded-full p-0.5 transition',
                    active ? 'hover:bg-primary-foreground/20' : 'hover:bg-muted'
                  )}
                  onClick={(e) => onClose(tab.id, e)}
                >
                  <X className="h-3 w-3" />
                </button>
              )}
            </div>
          )
        })}
        <button
          type="button"
          aria-label={t('chat.sidebar.newVirtual')}
          title={t('chat.sidebar.newVirtual')}
          className="text-muted-foreground hover:bg-muted hover:text-foreground flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-dashed transition"
          onClick={onAddVirtual}
        >
          <Plus className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  )
}
