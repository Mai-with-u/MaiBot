import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from '@tanstack/react-router'
import { ArrowLeft } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import { FormColumn, FormColumns, FormRow, FormSection } from '@/components/ui/form-section'
import { DashboardFolderPanel, DashboardFolderTabBar } from '@/components/ui/dashboard-tabs'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Tabs, TabsContent, TabsTrigger } from '@/components/ui/tabs'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { getCommonSettings, saveCommonSettings, type CommonSettings as Settings } from '@/host/api'

import { useAutoSave } from './hooks/useAutoSave'

const networkFields = [
  ['hosts', '监听地址', '多个地址用逗号分隔'],
  ['port', '监听端口', 'WebUI、文件服务和 maim_message 共用的端口'],
  ['public_base_url', '公共访问地址', '用于文件下载和预览链接，例如 https://bot.example.com'],
  ['ssl_certfile', 'HTTPS 证书', '证书文件的绝对路径；留空使用 HTTP'],
  ['ssl_keyfile', 'HTTPS 私钥', '与证书配套的私钥文件绝对路径'],
] as const

export function CommonSettings() {
  const [settings, setSettings] = useState<Settings | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [hasUnsavedChanges, setHasUnsavedChanges] = useState(false)
  const draftRef = useRef<Settings | null>(null)
  const revisionRef = useRef('')

  const saveSection = useCallback(async (_section: string, data: unknown) => {
    const draft = data as Settings
    // 保存串行执行，发送时取上次成功响应的版本，避免连续编辑发生自身版本冲突。
    const result = await saveCommonSettings({ ...draft, revision: revisionRef.current })
    revisionRef.current = result.revision
    // 响应只替换对应的草稿，不能覆盖请求期间继续输入的内容。
    const current = draftRef.current
    const next = current === draft ? result : current && {
      ...current, revision: result.revision, restart_required: result.restart_required,
    }
    draftRef.current = next
    setSettings(next)
    setError('')
  }, [])
  const onSaveError = useCallback((reason: Error) => setError(reason.message), [])
  const { triggerAutoSave } = useAutoSave(
    settings === null, setBusy, setHasUnsavedChanges, { saveSection, onSaveError }
  )

  useEffect(() => {
    let active = true
    void getCommonSettings().then(value => {
      if (!active) return
      revisionRef.current = value.revision
      draftRef.current = value
      setSettings(value)
    }, reason => {
      if (active) setError(reason instanceof Error ? reason.message : String(reason))
    })
    return () => { active = false }
  }, [])

  function edit(next: Settings) {
    draftRef.current = next
    setSettings(next)
    setError('')
    triggerAutoSave('common', next)
  }

  function update(key: string, value: string | number | boolean | string[]) {
    const current = draftRef.current
    if (current) edit({ ...current, webui: { ...current.webui, [key]: value } })
  }

  const textField = (key: string, label: string, hint?: string) => <FormRow key={key} label={label}>
    <div className="w-full space-y-1">
      <Input aria-label={label} type={key === 'port' ? 'number' : 'text'}
        value={Array.isArray(settings?.webui[key]) ? (settings.webui[key] as string[]).join(', ') : String(settings?.webui[key] ?? '')}
        onChange={event => update(key, key === 'hosts' ? event.target.value.split(',').map(value => value.trim()) : key === 'port' ? Number(event.target.value) : event.target.value)} />
      {hint && <p className="text-muted-foreground text-xs">{hint}</p>}
    </div>
  </FormRow>

  const retentionSection = (section: 'images' | 'emoji' | 'logs') => {
    if (!settings) return null
    const titles = { images: '图片缓存', emoji: '表情包缓存', logs: '日志保留' }
    const labels: Record<string, string> = {
      enabled: '启用自动清理',
      check_interval_hours: '检查间隔（小时）',
      image_file_retention_days: '图片保留天数',
      no_file_result_retention_days: '识别结果保留天数',
      emoji_file_retention_days: '未注册表情包保留天数',
      no_file_record_retention_days: '无文件记录保留天数',
      log_file_max_bytes: '单个日志大小（字节）',
      max_log_files: '日志保留份数',
      log_cleanup_days: '日志保留天数',
    }
    return (
      <FormSection title={titles[section]}>
        {Object.entries(settings.retention[section]).map(([key, value]) => {
          const change = (next: number | boolean) => {
            const current = draftRef.current
            if (current) edit({
              ...current,
              retention: { ...current.retention, [section]: { ...current.retention[section], [key]: next } },
            })
          }
          return (
            <FormRow key={key} label={labels[key]}>
              {typeof value === 'boolean' ? (
                <Switch aria-label={labels[key]} checked={value} onCheckedChange={change} />
              ) : (
                <Input aria-label={labels[key]} type="number"
                  min={key === 'check_interval_hours' ? 1 / 60 : 1}
                  step={key === 'check_interval_hours' ? 'any' : 1}
                  value={value} onChange={event => change(Number(event.target.value))} />
              )}
            </FormRow>
          )
        })}
      </FormSection>
    )
  }

  return (
    <ScrollArea
      className="h-full min-w-0 max-lg:h-auto max-lg:overflow-visible max-lg:[&>[data-dashboard-scrollbar]]:hidden"
      viewportClassName="max-lg:h-auto max-lg:!overflow-visible max-lg:overscroll-auto"
      scrollbars="vertical"
    >
      <div className="max-w-full space-y-2 p-4 sm:p-6">
        <div data-config-section-navigation="true" className="sticky top-0 z-20 flex flex-wrap items-center gap-3 py-2">
          <Button asChild variant="outline" size="sm">
            <Link to="/config/bot" search={{ tab: 'bot' }}><ArrowLeft className="mr-2 h-4 w-4" />麦麦设置</Link>
          </Button>
          <span className="text-base font-semibold">公共设置</span>
          {settings && <span role="status" aria-live="polite" className="text-muted-foreground ml-auto text-sm">
            {busy ? '自动保存中…' : error ? '保存失败，修改后重试' : hasUnsavedChanges ? '等待自动保存…' : '已保存'}
          </span>}
        </div>
        <p className="text-muted-foreground pb-2 text-sm">所有 Bot 共用这些设置，切换 Bot 不会改变配置。</p>
        {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
        {settings?.restart_required && <p className="text-muted-foreground text-sm">网络与访问设置有变更，需重启宿主生效。</p>}
        {!settings && !error && <p className="text-muted-foreground py-8 text-center text-sm">正在加载公共设置…</p>}
        {settings && (
          <fieldset className="min-w-0">
            <Tabs defaultValue="network">
              <DashboardFolderTabBar data-config-page-folder-tabs="true" data-config-bot-subtab-list="true">
                <TabsTrigger value="network">网络与访问</TabsTrigger>
                <TabsTrigger value="cache">缓存清理</TabsTrigger>
                <TabsTrigger value="logs">日志保留</TabsTrigger>
              </DashboardFolderTabBar>
              <TabsContent value="network" className="mt-0">
                <DashboardFolderPanel className="space-y-4 p-4">
                  <p className="text-muted-foreground text-sm">监听与访问防护设置需重启宿主；公网出站 URL 校验自动加载。</p>
                  <FormColumns>
                    <FormColumn>
                      <FormSection title="网络与访问地址">
                        {networkFields.map(([key, label, hint]) => textField(key, label, hint))}
                      </FormSection>
                    </FormColumn>
                    <FormColumn>
                      <FormSection title="访问防护">
                        {([
                          ['enabled', '启用 WebUI'],
                          ['secure_cookie', '仅通过 HTTPS 发送登录凭据'],
                          ['trust_xff', '信任反向代理的客户端地址'],
                          ['enforce_public_outbound_url', '强制公网出站 URL 校验'],
                        ] as const).map(([key, label]) => (
                          <FormRow key={key} label={label}>
                            <Switch aria-label={label} checked={Boolean(settings.webui[key])} onCheckedChange={value => update(key, value)} />
                          </FormRow>
                        ))}
                        <FormRow label="防爬虫模式">
                          <Select value={String(settings.webui.anti_crawler_mode)} onValueChange={value => update('anti_crawler_mode', value)}>
                            <SelectTrigger aria-label="防爬虫模式"><SelectValue /></SelectTrigger>
                            <SelectContent>
                              {[['false', '关闭'], ['basic', '只记录'], ['loose', '宽松拦截'], ['strict', '严格拦截']].map(([value, label]) => (
                                <SelectItem key={value} value={value}>{label}</SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                        </FormRow>
                        {textField('allowed_ips', '防爬虫白名单', '逗号分隔，支持 CIDR 和通配符')}
                        {textField('trusted_proxies', '可信反向代理', '填写直连代理的 IP，逗号分隔')}
                      </FormSection>
                    </FormColumn>
                  </FormColumns>
                </DashboardFolderPanel>
              </TabsContent>
              <TabsContent value="cache" className="mt-0">
                <DashboardFolderPanel className="space-y-4 p-4">
                  <p className="text-muted-foreground text-sm">清理策略自动加载，文件和使用记录仍按 Bot 隔离。</p>
                  <FormColumns>
                    <FormColumn>{retentionSection('images')}</FormColumn>
                    <FormColumn>{retentionSection('emoji')}</FormColumn>
                  </FormColumns>
                </DashboardFolderPanel>
              </TabsContent>
              <TabsContent value="logs" className="mt-0">
                <DashboardFolderPanel className="space-y-4 p-4">
                  <p className="text-muted-foreground text-sm">保留策略自动加载，各 Bot 独立保存和清理自己的日志。</p>
                  <div className="max-w-xl">{retentionSection('logs')}</div>
                </DashboardFolderPanel>
              </TabsContent>
            </Tabs>
          </fieldset>
        )}
      </div>
    </ScrollArea>
  )
}
