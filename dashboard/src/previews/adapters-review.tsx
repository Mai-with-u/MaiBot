import { useState } from 'react'
import { ChevronDown, Plug, Send, Snowflake } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import type { AdapterPolicyDefaults } from '@/lib/chat-management-api'
import type { InstalledPlugin } from '@/lib/plugin-api'
import { AdapterManagementView } from '@/routes/plugin-config/AdapterManagementView'
import { AdapterPolicyDefaultsView } from '@/routes/plugin-config/AdapterPolicyDefaultsView'
import type { PluginListGroup } from '@/routes/plugin-config/hooks/usePluginList'

const samples = [
  {
    id: 'snowluma',
    name: 'SnowLuma 适配器',
    version: '1.1.7',
    description: 'QQ 适配器，同时支持 SnowLuma 与 NapCat 客户端',
  },
  {
    id: 'maiwithu',
    name: 'MaiWithU MaiBotWS 适配器',
    version: '2.1.0',
    description: '通过认证 WebSocket 和原生消息网关 SDK 为 MaiWithU 提供外部 MaiBot 对话。',
  },
  {
    id: 'telegram',
    name: 'Telegram 适配器',
    version: '0.1.13',
    description:
      '单账号 Telegram Bot API 适配器，支持论坛话题、文本、图片、静态/动画/视频贴纸、语音、@、回复引用和转发。',
  },
]
const sampleAdapters: InstalledPlugin[] = samples.map((sample) => ({
  id: sample.id,
  path: `/preview/${sample.id}`,
  enabled: true,
  loaded: true,
  load_status: 'success',
  manifest: {
    ...sample,
    manifest_version: 2,
    plugin_type: 'adapter',
    author: { name: '示例作者' },
    license: 'AGPL-3.0',
    host_application: { min_version: '1.3.0' },
  },
}))

/** 运行状态和接收规则均为本地示例，沿用正式页面的展示和交互组件。 */
export function AdaptersReview() {
  const [adapters, setAdapters] = useState(sampleAdapters)
  const [defaults, setDefaults] = useState<AdapterPolicyDefaults>({
    group: 'allow',
    private: 'allow',
  })
  const [notice, setNotice] = useState('示例页面；开关和接收规则只影响本页，不会启停实际适配器。')
  const groups: PluginListGroup[] = [
    {
      key: 'success',
      label: '加载成功',
      dotClassName: 'bg-emerald-500',
      plugins: adapters.filter((item) => item.enabled),
    },
    {
      key: 'disabled',
      label: '已禁用',
      dotClassName: 'bg-muted-foreground/45',
      plugins: adapters.filter((item) => !item.enabled),
    },
  ]

  return (
    <main className="management-review-screen">
      <AdapterManagementView
        onInstalled={() => {}}
        groups={groups.filter((group) => group.plugins.length > 0)}
        defaults={
          <AdapterPolicyDefaultsView
            defaults={defaults}
            onChange={(chatType, action) =>
              setDefaults((current) => ({ ...current, [chatType]: action }))
            }
            accounts={
              <Popover>
                <PopoverTrigger asChild>
                  <Button variant="outline" size="sm">
                    平台账号
                    <ChevronDown />
                  </Button>
                </PopoverTrigger>
                <PopoverContent className="text-sm">
                  平台账号请在正式页面编辑；预览不会读取账号配置。
                </PopoverContent>
              </Popover>
            }
          />
        }
        getStatusMeta={(plugin) => ({
          dotClassName: plugin.enabled ? 'bg-emerald-500' : 'bg-muted-foreground/45',
          label: plugin.enabled ? '加载成功' : '已禁用',
        })}
        getUpdateState={() => ({ canUpdate: false, hasUpdate: false, title: '示例适配器暂无更新' })}
        onConfigure={(plugin) => setNotice(`预览：正式页面将打开「${plugin.manifest.name}」配置。`)}
        onToggle={(plugin) =>
          setAdapters((current) =>
            current.map((item) =>
              item.id === plugin.id
                ? {
                    ...item,
                    enabled: !item.enabled,
                    loaded: !item.enabled,
                    load_status: item.enabled ? 'disabled' : 'success',
                  }
                : item
            )
          )
        }
        onUpdate={() => setNotice('预览不会更新实际适配器。')}
        onDelete={(plugin) =>
          setNotice(`预览：正式页面将确认是否卸载「${plugin.manifest.name}」。`)
        }
        onFailureDetails={() => setNotice('当前示例没有加载失败。')}
        renderIcon={(plugin) => {
          const Icon = plugin.id === 'snowluma' ? Snowflake : plugin.id === 'telegram' ? Send : Plug
          return <Icon className="text-primary h-7 w-7" />
        }}
      />
      <p role="status" className="text-muted-foreground mt-6 text-xs">
        {notice}
      </p>
    </main>
  )
}
