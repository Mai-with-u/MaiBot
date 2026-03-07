import { cn } from '@/lib/utils'
import { MessageSquare, Plus, UserCircle2, X } from 'lucide-react'

import type { ChatTab } from './types'

interface ChatTabBarProps {
  tabs: ChatTab[]
  activeTabId: string
  onSwitch: (tabId: string) => void
  onClose: (tabId: string, e?: React.MouseEvent | React.KeyboardEvent) => void
  onAddVirtual: () => void
}

export function ChatTabBar({
  tabs,
  activeTabId,
  onSwitch,
  onClose,
  onAddVirtual,
}: ChatTabBarProps) {
  return (
    <div className="shrink-0 border-b bg-muted/30">
      <div className="max-w-4xl mx-auto px-2 sm:px-4">
        <div className="flex items-center gap-1 overflow-x-auto py-1.5 scrollbar-thin">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              className={cn(
                "flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm whitespace-nowrap transition-colors cursor-pointer",
                "hover:bg-muted",
                activeTabId === tab.id
                  ? "bg-background shadow-sm border"
                  : "text-muted-foreground"
              )}
              type="button"
              onClick={() => onSwitch(tab.id)}
            >
              {tab.type === 'webui' ? (
                <MessageSquare className="h-3.5 w-3.5" />
              ) : (
                <UserCircle2 className="h-3.5 w-3.5" />
              )}
              <span className="max-w-[100px] truncate">{tab.label}</span>
              {/* 连接状态指示器 */}
              <span className={cn(
                "w-1.5 h-1.5 rounded-full",
                tab.isConnected ? "bg-green-500" : "bg-muted-foreground/50"
              )} />
              {/* 关闭按钮（非默认标签页） */}
              {tab.id !== 'webui-default' && (
                <span
                  onClick={(e) => onClose(tab.id, e)}
                  className="ml-0.5 p-0.5 rounded hover:bg-muted-foreground/20 cursor-pointer"
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault()
                      onClose(tab.id, e)
                    }
                  }}
                >
                  <X className="h-3 w-3" />
                </span>
              )}
            </button>
          ))}
          {/* 新建虚拟身份标签页按钮 */}
          <button
            onClick={onAddVirtual}
            className="flex items-center gap-1 px-2 py-1.5 rounded-md text-sm text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
            title="新建虚拟身份对话"
          >
            <Plus className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>
    </div>
  )
}
