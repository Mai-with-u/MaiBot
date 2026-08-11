import { useEffect, useMemo, useState } from 'react'
import { Command as CommandIcon, Search, ShieldCheck } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { MultiSelect } from '@/components/ui/multi-select'
import { getChatStreams } from '@/lib/chat-management-api'
import { getRuntimeCommands, type RuntimeCommand } from '@/lib/plugin-api'
import { cn } from '@/lib/utils'

interface CommandRule {
  allow_chats: string[]
  allow_users: string[]
}

interface Props {
  pluginSection: Record<string, unknown> | null
  onChange: (value: Record<string, unknown>) => void
}

function parseUsers(value: string): string[] {
  return Array.from(new Set(value.split(/[，,\s]+/).map((item) => item.trim()).filter(Boolean)))
}

export function CommandPermissions({ pluginSection, onChange }: Props) {
  const [commands, setCommands] = useState<RuntimeCommand[]>([])
  const [chatOptions, setChatOptions] = useState<Array<{ label: string; value: string }>>([])
  const [query, setQuery] = useState('')
  const [selectedId, setSelectedId] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    Promise.all([getRuntimeCommands(), getChatStreams()])
      .then(([commandItems, chats]) => {
        setCommands(commandItems)
        setSelectedId((current) => current || commandItems[0]?.id || '')
        setChatOptions(
          chats.map((chat) => ({
            value: chat.session_id,
            label: `${chat.display_name} · ${chat.platform}`,
          }))
        )
      })
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : '加载命令失败'))
  }, [])

  const filteredCommands = useMemo(() => {
    const keyword = query.trim().toLocaleLowerCase()
    if (!keyword) return commands
    return commands.filter((command) =>
      [command.name, command.description, command.plugin_name].some((value) =>
        value.toLocaleLowerCase().includes(keyword)
      )
    )
  }, [commands, query])

  const selectedCommand = commands.find((command) => command.id === selectedId)
  const permissionMap = (pluginSection?.command_permissions ?? {}) as Record<string, CommandRule>
  const selectedRule = permissionMap[selectedId] ?? { allow_users: [], allow_chats: [] }
  const operators = Array.isArray(pluginSection?.permission) ? (pluginSection.permission as string[]) : []

  const updateRule = (rule: CommandRule) => {
    onChange({
      ...(pluginSection ?? {}),
      command_permissions: { ...permissionMap, [selectedId]: rule },
    })
  }

  if (error) {
    return <div className="text-destructive rounded-lg border p-4 text-sm">{error}</div>
  }

  return (
    <div className="grid min-h-[34rem] overflow-hidden rounded-xl border bg-card lg:grid-cols-[20rem_minmax(0,1fr)]">
      <aside className="border-b lg:border-r lg:border-b-0">
        <div className="border-b p-4">
          <div className="relative">
            <Search className="text-muted-foreground absolute top-2.5 left-3 h-4 w-4" />
            <Input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索命令或插件"
              className="pl-9"
              aria-label="搜索命令或插件"
            />
          </div>
        </div>
        <div className="max-h-[28rem] overflow-y-auto p-2">
          {filteredCommands.map((command) => (
            <button
              key={command.id}
              type="button"
              onClick={() => setSelectedId(command.id)}
              className={cn(
                'hover:bg-muted/70 focus-visible:ring-ring mb-1 w-full rounded-lg px-3 py-2.5 text-left focus-visible:ring-2 focus-visible:outline-none',
                selectedId === command.id && 'bg-muted'
              )}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="truncate font-medium">/{command.name}</span>
                <Badge variant={command.permission === 'operator' ? 'default' : 'secondary'}>
                  {command.permission === 'operator' ? '受保护' : '公开'}
                </Badge>
              </div>
              <div className="text-muted-foreground mt-1 truncate text-xs">{command.plugin_name}</div>
            </button>
          ))}
          {filteredCommands.length === 0 && (
            <div className="text-muted-foreground px-3 py-8 text-center text-sm">没有匹配的命令</div>
          )}
        </div>
      </aside>

      <main className="min-w-0 p-5 sm:p-6">
        {selectedCommand ? (
          <div className="mx-auto max-w-3xl space-y-6">
            <header className="space-y-2 border-b pb-5">
              <div className="flex flex-wrap items-center gap-2">
                <CommandIcon className="h-5 w-5" />
                <h2 className="text-xl font-semibold">/{selectedCommand.name}</h2>
                <Badge variant="outline">{selectedCommand.plugin_name}</Badge>
              </div>
              <p className="text-muted-foreground text-sm">
                {selectedCommand.description || '该命令未提供说明。'}
              </p>
              <code className="bg-muted block overflow-x-auto rounded-md px-3 py-2 text-xs">
                {selectedCommand.pattern}
              </code>
            </header>

            {selectedCommand.permission === 'public' ? (
              <div className="bg-muted/40 flex gap-3 rounded-lg border p-4">
                <ShieldCheck className="text-muted-foreground mt-0.5 h-5 w-5 shrink-0" />
                <div>
                  <div className="font-medium">公开命令</div>
                  <p className="text-muted-foreground mt-1 text-sm">
                    此命令由插件声明为所有用户可用，不需要额外授权。
                  </p>
                </div>
              </div>
            ) : (
              <div className="space-y-6">
                <div className="space-y-2">
                  <label htmlFor="global-command-operators" className="text-sm font-medium">
                    全局管理员
                  </label>
                  <Input
                    id="global-command-operators"
                    value={operators.join(', ')}
                    onChange={(event) =>
                      onChange({ ...(pluginSection ?? {}), permission: parseUsers(event.target.value) })
                    }
                    placeholder="例如 qq:123456789，多个用户用逗号分隔"
                  />
                  <p className="text-muted-foreground text-xs">这些用户可以执行所有受保护命令。</p>
                </div>

                <div className="space-y-2">
                  <label htmlFor="command-users" className="text-sm font-medium">仅为此命令放行用户</label>
                  <Input
                    id="command-users"
                    value={selectedRule.allow_users.join(', ')}
                    onChange={(event) =>
                      updateRule({ ...selectedRule, allow_users: parseUsers(event.target.value) })
                    }
                    placeholder="例如 qq:234567890，多个用户用逗号分隔"
                  />
                </div>

                <div className="space-y-2">
                  <div className="text-sm font-medium">仅为此命令放行聊天</div>
                  <MultiSelect
                    options={chatOptions}
                    selected={selectedRule.allow_chats}
                    onChange={(allowChats) => updateRule({ ...selectedRule, allow_chats: allowChats })}
                    placeholder="选择群聊或私聊"
                    emptyText="没有找到聊天"
                    compact
                  />
                  <p className="text-muted-foreground text-xs">
                    放行聊天后，该聊天中的所有成员都可以执行此命令。
                  </p>
                </div>
              </div>
            )}
          </div>
        ) : (
          <div className="text-muted-foreground flex h-full items-center justify-center text-sm">
            请选择一个命令
          </div>
        )}
      </main>
    </div>
  )
}
