import { useEffect, useState } from 'react'

import { Card } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { MultiSelect } from '@/components/ui/multi-select'
import { Switch } from '@/components/ui/switch'
import { getChatStreams } from '@/lib/chat-management-api'
import { getInstalledPlugins, type InstalledPlugin } from '@/lib/plugin-api'

interface UsageRule {
  enabled: boolean
  allow_users: string[]
  allow_chats: string[]
}

interface Props {
  pluginSection: Record<string, unknown> | null
  onChange: (value: Record<string, unknown>) => void
}

export function PluginUsagePermissions({ pluginSection, onChange }: Props) {
  const [plugins, setPlugins] = useState<InstalledPlugin[]>([])
  const [chats, setChats] = useState<Array<{ label: string; value: string }>>([])
  const [selected, setSelected] = useState('')
  const [error, setError] = useState('')
  const [userDraft, setUserDraft] = useState({ plugin: '', saved: '', text: '' })
  const rules = (pluginSection?.usage_permissions ?? {}) as Record<string, UsageRule>
  const rule = rules[selected] ?? { enabled: false, allow_users: [], allow_chats: [] }
  const savedUsers = rule.allow_users.join(', ')
  const usersText = userDraft.plugin === selected && userDraft.saved === savedUsers ? userDraft.text : savedUsers

  useEffect(() => {
    Promise.all([getInstalledPlugins(), getChatStreams()])
      .then(([items, streams]) => {
        setPlugins(items)
        setSelected((current) => current || items[0]?.id || '')
        setChats(streams.map((chat) => ({ value: chat.session_id, label: `${chat.display_name} · ${chat.platform}` })))
      })
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : '加载插件权限失败'))
  }, [])

  const update = (next: UsageRule) => onChange({
    ...(pluginSection ?? {}),
    usage_permissions: { ...rules, [selected]: next },
  })

  return (
    <Card className="mb-6 space-y-4 p-5">
      <div>
        <h2 className="text-lg font-semibold">插件使用权限</h2>
        <p className="text-muted-foreground mt-1 text-sm">
          核心统一限制插件的命令和工具，无需插件支持。未开启限制时保持原有行为；原有命令鉴权仍然生效。
        </p>
      </div>
      {error ? <p className="text-destructive text-sm">{error}</p> : (
        <>
          <label className="block space-y-2 text-sm">
            <span>选择插件</span>
            <select className="bg-background w-full rounded-md border p-2" value={selected} onChange={(event) => setSelected(event.target.value)}>
              {plugins.length === 0 && <option value="">暂无已安装插件</option>}
              {plugins.map((plugin) => <option key={plugin.id} value={plugin.id}>{plugin.manifest.name} · {plugin.id}</option>)}
            </select>
          </label>
          {selected && (
            <>
              <div className="flex items-center justify-between gap-3">
                <span className="text-sm font-medium">限制此插件的使用范围</span>
                <Switch aria-label="限制此插件的使用范围" checked={rule.enabled} onCheckedChange={(enabled) => update({ ...rule, enabled })} />
              </div>
              {rule.enabled && (
                <div className="space-y-4">
                  <label className="block space-y-2 text-sm">
                    <span>放行用户</span>
                    <Input value={usersText} placeholder="例如 qq:123456789，多个用户用逗号分隔"
                      onChange={(event) => {
                        const text = event.target.value
                        const users = Array.from(new Set(text.split(/[，,\s]+/).filter(Boolean)))
                        setUserDraft({ plugin: selected, saved: users.join(', '), text })
                        update({ ...rule, allow_users: users })
                      }} />
                  </label>
                  <div className="space-y-2 text-sm">
                    <span>放行聊天</span>
                    <MultiSelect options={chats} selected={rule.allow_chats} onChange={(allow_chats) => update({ ...rule, allow_chats })}
                      placeholder="选择群聊或私聊" emptyText="没有找到聊天" compact />
                  </div>
                  <p className="text-muted-foreground text-xs">
                    用户或聊天任一匹配即可使用；均为空时禁止所有调用，包括管理员。用户授权适用于命令和私聊工具；群聊工具由模型自主调用，请放行对应群聊。保存配置后生效。
                  </p>
                </div>
              )}
            </>
          )}
        </>
      )}
    </Card>
  )
}
