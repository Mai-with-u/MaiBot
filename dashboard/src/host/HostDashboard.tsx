import {
  Bot,
  ChevronDown,
  CircleAlert,
  Folder,
  Loader2,
  Play,
  Plus,
  RefreshCw,
  RotateCw,
  Square,
} from 'lucide-react'
import { type FormEvent, useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Board, BoardModule, BoardScreen } from '@/components/ui/board'
import { Button } from '@/components/ui/button'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { Input } from '@/components/ui/input'
import { SegmentedMeter } from '@/components/ui/segmented-meter'
import { type LightStatus, StatusLight } from '@/components/ui/status-light'
import { DataPanel, PageFrame } from '@/components/ui/surface'
import { ApiError } from '@/lib/http/errors'
import { cn } from '@/lib/utils'

import {
  type BotAction,
  controlHostBot,
  createHostBot,
  type HostBot,
  type HostBotList,
  listHostBots,
  selectHostBot,
} from './api'
import { BotOperations } from './BotOperations'
import { useHostRuntime } from './state'

function errorMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason)
}

function statusKey(bot: HostBot): string {
  if (bot.initialization === 'initializing') return 'initializing'
  if (bot.initialization === 'failed') return 'initializationFailed'
  return bot.status === 'crash_loop' ? 'crashLoop' : bot.status
}

function botLightStatus(bot: HostBot): LightStatus {
  if (bot.initialization === 'failed' || bot.status === 'failed' || bot.status === 'crash_loop') {
    return 'error'
  }
  if (
    bot.initialization === 'initializing' ||
    bot.status === 'starting' ||
    bot.status === 'migrating' ||
    bot.status === 'stopping'
  ) {
    return 'mixed'
  }
  return bot.status === 'running' ? 'on' : 'off'
}

function botBadgeVariant(bot: HostBot): 'success' | 'destructive' | 'secondary' | 'outline' {
  const light = botLightStatus(bot)
  if (light === 'on') return 'success'
  if (light === 'error') return 'destructive'
  if (light === 'mixed') return 'secondary'
  return 'outline'
}

function botMeterClassName(bot: HostBot): string {
  const light = botLightStatus(bot)
  if (light === 'on') return 'bg-emerald-500'
  if (light === 'error') return 'bg-destructive'
  if (light === 'mixed') return 'bg-amber-500'
  return 'bg-muted-foreground/35'
}

/** 复用于 /bots 路由与设置页的机器人管理标签，不另建页面外壳或登录入口。 */
export function HostDashboard({ embedded = false }: { embedded?: boolean }) {
  const { t, i18n } = useTranslation()
  const runtime = useHostRuntime()
  const [data, setData] = useState<HostBotList | null>(null)
  const [readError, setReadError] = useState('')
  const [actionError, setActionError] = useState('')
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const [name, setName] = useState('')
  const [creating, setCreating] = useState(false)
  const [selecting, setSelecting] = useState(false)
  const [pending, setPending] = useState<Record<string, BotAction>>({})
  const refreshRequest = useRef<Promise<void> | null>(null)
  const creatingRef = useRef(false)
  const pendingIds = useRef(new Set<string>())

  const refresh = useCallback((): Promise<void> => {
    // 手动刷新、轮询和操作完成后的刷新共用一次读取，避免慢响应覆盖较新的状态。
    if (refreshRequest.current) return refreshRequest.current
    setRefreshing(true)
    const request = listHostBots()
      .then(
        (result) => {
          setData(result)
          setLastUpdated(new Date())
          setReadError('')
        },
        (reason: unknown) => {
          setReadError(errorMessage(reason))
        }
      )
      .finally(() => {
        refreshRequest.current = null
        setRefreshing(false)
      })
    refreshRequest.current = request
    return request
  }, [])

  useEffect(() => {
    void refresh()
    const timer = window.setInterval(() => {
      void refresh()
    }, 3_000)
    return () => window.clearInterval(timer)
  }, [refresh])

  function reportActionError(reason: unknown) {
    const message = errorMessage(reason)
    const uncertain = !(reason instanceof ApiError) || reason.status === undefined
    setActionError(uncertain ? `${message}\n${t('hostManager.uncertainResult')}` : message)
  }

  async function createBot(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const displayName = name.trim()
    if (creatingRef.current || !displayName) return
    creatingRef.current = true
    setCreating(true)
    setActionError('')
    try {
      await createHostBot(displayName)
      setName('')
    } catch (reason) {
      reportActionError(reason)
    } finally {
      // 创建不自动重试：即使客户端超时，宿主仍可能已登记或正在初始化这个 Bot。
      await refresh()
      creatingRef.current = false
      setCreating(false)
    }
  }

  async function controlBot(bot: HostBot, action: BotAction) {
    if (pendingIds.current.has(bot.bot_id)) return
    pendingIds.current.add(bot.bot_id)
    setPending((current) => ({ ...current, [bot.bot_id]: action }))
    setActionError('')
    try {
      const result = await controlHostBot(bot.bot_id, action)
      if (result.status === 'failed')
        setActionError(result.error || t('hostManager.operationFailed'))
    } catch (reason) {
      reportActionError(reason)
    } finally {
      await refresh()
      pendingIds.current.delete(bot.bot_id)
      setPending((current) => {
        const next = { ...current }
        delete next[bot.bot_id]
        return next
      })
    }
  }

  async function selectBot(id: string) {
    if (selecting) return
    setSelecting(true)
    setActionError('')
    try {
      await selectHostBot(id)
    } catch (reason) {
      reportActionError(reason)
      setSelecting(false)
    }
  }

  const bots = data?.bots ?? []
  const running = bots.filter((bot) => bot.status === 'running').length
  const failures = bots.filter(
    (bot) =>
      bot.status === 'failed' || bot.status === 'crash_loop' || bot.initialization === 'failed'
  ).length
  const selectedBot = bots.find((bot) => bot.bot_id === runtime.selected_bot_id) ?? null

  return (
    <div
      className={cn('flex w-full flex-col gap-5', !embedded && 'mx-auto max-w-7xl p-4 sm:p-6 lg:p-7')}
    >
      {/* 1. 页头区：对齐 P3 麦麦观察页头排布 */}
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div className="min-w-0 space-y-1.5">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="flex items-center gap-2.5 text-2xl font-bold tracking-tight">
              <Bot className="h-6 w-6 shrink-0" />
              {t('hostManager.title')}
            </h1>
            {selectedBot && (
              <Badge
                variant={selectedBot.status === 'running' ? 'success' : 'outline'}
                className="gap-1.5"
              >
                <StatusLight status={botLightStatus(selectedBot)} decorative />
                <span>
                  {t('hostManager.currentBot')}：{selectedBot.name}
                </span>
              </Badge>
            )}
          </div>
          <p className="text-muted-foreground text-xs sm:text-sm">{t('hostManager.subtitle')}</p>
        </div>

        <div className="ml-auto flex items-center gap-3">
          {lastUpdated && (
            <span className="text-muted-foreground hidden text-xs tabular-nums sm:inline">
              {t('hostManager.lastUpdated', {
                time: lastUpdated.toLocaleTimeString(i18n.language),
              })}
            </span>
          )}
          <Button variant="outline" disabled={refreshing} onClick={() => void refresh()}>
            <RefreshCw className={refreshing ? 'animate-spin' : ''} />
            {t('hostManager.refresh')}
          </Button>
        </div>
      </header>

      {/* 告警提示区 */}
      {readError && (
        <Alert variant="destructive">
          <CircleAlert className="h-4 w-4" />
          <AlertTitle>{t('hostManager.refreshFailed')}</AlertTitle>
          <AlertDescription>
            <p>{readError}</p>
            {data && <p>{t('hostManager.staleData')}</p>}
          </AlertDescription>
        </Alert>
      )}
      {actionError && (
        <Alert variant="destructive">
          <CircleAlert className="h-4 w-4" />
          <AlertDescription className="whitespace-pre-line">{actionError}</AlertDescription>
        </Alert>
      )}

      {/* 2. 首屏 12 列仪器面板：左舱集群概览与状态条 (7列) + 右舱新建实例 (5列) */}
      <Board rows="auto">
        <BoardModule
          code="FLEET"
          title={t('hostManager.summary')}
          span={7}
          spanNarrow={12}
          tone={0}
        >
          <BoardScreen className="flex flex-col justify-between gap-3 p-3.5">
            <div
              className="grid grid-cols-3 divide-x sm:grid-cols-4"
              aria-label={t('hostManager.summary')}
            >
              <div className="flex flex-col justify-between gap-1 pr-3">
                <span data-board-label="true">{t('hostManager.total')}</span>
                <strong data-board-number="true" className="text-2xl sm:text-3xl">
                  {data ? bots.length : '—'}
                </strong>
              </div>
              <div className="flex flex-col justify-between gap-1 px-3">
                <span data-board-label="true" className="inline-flex items-center gap-1.5">
                  <StatusLight status={running > 0 ? 'on' : 'off'} decorative />
                  {t('hostManager.status.running')}
                </span>
                <strong
                  data-board-number="true"
                  className={cn('text-2xl sm:text-3xl', running > 0 && 'text-primary')}
                >
                  {running}
                </strong>
              </div>
              <div className="flex flex-col justify-between gap-1 px-3">
                <span data-board-label="true" className="inline-flex items-center gap-1.5">
                  <StatusLight status={failures > 0 ? 'error' : 'off'} decorative />
                  {t('hostManager.needsAttention')}
                </span>
                <strong
                  data-board-number="true"
                  className={cn('text-2xl sm:text-3xl', failures > 0 && 'text-destructive')}
                >
                  {failures}
                </strong>
              </div>
              <div className="hidden min-w-0 flex-col justify-between gap-1 pl-3 sm:flex">
                <span data-board-label="true">{t('hostManager.currentBot')}</span>
                <strong
                  className={cn(
                    'truncate text-base font-bold',
                    selectedBot ? 'text-primary' : 'text-muted-foreground'
                  )}
                  title={selectedBot?.name ?? t('hostManager.noSelection')}
                >
                  {selectedBot?.name ?? t('hostManager.noSelection')}
                </strong>
              </div>
            </div>

            <div data-board-divider="true" className="flex flex-col gap-2 pt-2.5">
              <SegmentedMeter
                label={t('hostManager.summary')}
                separated
                className="h-2.5"
                segments={
                  bots.length > 0
                    ? bots.map((bot) => ({
                        label: `${bot.name}：${t(`hostManager.status.${statusKey(bot)}`)}`,
                        value: 1,
                        className: botMeterClassName(bot),
                      }))
                    : [{ label: t('hostManager.emptyTitle'), value: 1, className: 'bg-muted' }]
                }
              />
              <p data-board-label="true" className="leading-relaxed">
                {t('hostManager.runtimeHint')}
              </p>
            </div>
          </BoardScreen>
        </BoardModule>

        <BoardModule
          code="CREATE"
          title={t('hostManager.createTitle')}
          span={5}
          spanNarrow={12}
          tone={1}
        >
          <BoardScreen className="flex flex-col justify-between gap-3 p-3.5">
            <p data-board-label="true" className="leading-relaxed">
              {t('hostManager.createHint')}
            </p>
            <form
              data-board-divider="true"
              className="flex flex-col gap-2.5 pt-3 sm:flex-row sm:items-center"
              onSubmit={(event) => void createBot(event)}
            >
              <label className="sr-only" htmlFor="host-bot-name">
                {t('hostManager.botName')}
              </label>
              <Input
                id="host-bot-name"
                value={name}
                maxLength={100}
                onChange={(event) => setName(event.target.value)}
                placeholder={t('hostManager.namePlaceholder')}
                disabled={creating}
                className="min-w-0 flex-1"
              />
              <Button type="submit" disabled={creating || !name.trim()} className="shrink-0">
                {creating ? <Loader2 className="animate-spin" /> : <Plus />}
                {t('hostManager.create')}
              </Button>
            </form>
          </BoardScreen>
        </BoardModule>
      </Board>

      {/* 3. 受管 Bot 模块区（方案 A：P3 模块化双栏硬件舱） */}
      <section aria-labelledby="host-bot-list-heading" className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2 px-0.5">
          <div className="flex items-center gap-2.5">
            <span className="text-muted-foreground font-mono text-xs font-bold tracking-widest uppercase">
              INSTANCES
            </span>
            <h2 id="host-bot-list-heading" className="text-base font-bold">
              {t('hostManager.botList')}
            </h2>
            {data && (
              <Badge variant="outline" className="font-mono tabular-nums">
                {bots.length}
              </Badge>
            )}
          </div>
          {lastUpdated && (
            <span className="text-muted-foreground text-xs tabular-nums sm:hidden">
              {t('hostManager.lastUpdated', {
                time: lastUpdated.toLocaleTimeString(i18n.language),
              })}
            </span>
          )}
        </div>

        {!data && !readError && (
          <PageFrame className="p-3">
            <DataPanel className="py-10 text-center">
              <p role="status" className="text-muted-foreground text-sm">
                {t('hostManager.loading')}
              </p>
            </DataPanel>
          </PageFrame>
        )}

        {data?.bots.length === 0 && (
          <PageFrame className="p-3">
            <DataPanel className="py-10 text-center">
              <h3 className="font-semibold">{t('hostManager.emptyTitle')}</h3>
              <p className="text-muted-foreground mt-1.5 text-sm">{t('hostManager.emptyHint')}</p>
            </DataPanel>
          </PageFrame>
        )}

        <div className="grid gap-5 xl:grid-cols-2">
          {bots.map((bot, index) => {
            const action = pending[bot.bot_id]
            const busy =
              Boolean(action) || ['starting', 'migrating', 'stopping'].includes(bot.status)
            const selected = runtime.selected_bot_id === bot.bot_id
            const error = bot.error || bot.initialization_error
            const lightStatus = botLightStatus(bot)
            const code = `BOT-${String(index + 1).padStart(2, '0')}`

            return (
              <PageFrame
                asChild
                key={bot.bot_id}
                className={cn(
                  'flex flex-col gap-2.5 p-3.5 transition-shadow',
                  selected && 'ring-primary/65 ring-2'
                )}
              >
                <article aria-busy={busy}>
                  {/* 舱顶丝印栏：指示灯 + 编号 + 名称 + 状态徽标 + 右侧「切换/当前 Bot」主开关 */}
                  <header className="flex flex-wrap items-center justify-between gap-2 px-0.5">
                    <div className="flex min-w-0 items-center gap-2">
                      <StatusLight status={lightStatus} size="sm" />
                      <span className="text-muted-foreground shrink-0 font-mono text-xs font-bold tracking-widest">
                        {code}
                      </span>
                      <h3 className="min-w-0 truncate text-base font-bold" title={bot.name}>
                        {bot.name}
                      </h3>
                      <Badge variant={botBadgeVariant(bot)} className="shrink-0">
                        {t(`hostManager.status.${statusKey(bot)}`)}
                      </Badge>
                    </div>

                    <Button
                      variant={selected ? 'outline' : 'default'}
                      size="sm"
                      aria-pressed={selected}
                      className={cn('ml-auto shrink-0', selected && 'disabled:opacity-100')}
                      disabled={selected || selecting || bot.initialization !== 'initialized'}
                      onClick={() => void selectBot(bot.bot_id)}
                    >
                      {t(selected ? 'hostManager.selected' : 'hostManager.useBot')}
                    </Button>
                  </header>

                  {/* 中央下沉数据屏：遥测读数 + 错误/阶段说明 + 内嵌折叠目录详情 */}
                  <DataPanel className="flex flex-col gap-2.5 p-3">
                    {error && (
                      <Alert variant="destructive" className="p-2.5 text-xs">
                        <CircleAlert className="h-3.5 w-3.5" />
                        <AlertDescription className="text-xs break-words">{error}</AlertDescription>
                      </Alert>
                    )}

                    {bot.detail && (
                      <p className="text-muted-foreground text-xs break-words" role="status">
                        {bot.detail}
                      </p>
                    )}

                    {bot.retry_at !== null && (
                      <p className="text-muted-foreground text-xs" role="status">
                        {t('hostManager.retryAt', {
                          time: new Date(bot.retry_at * 1000).toLocaleTimeString(i18n.language),
                        })}
                      </p>
                    )}

                    {/* 三分格遥测读数 */}
                    <div className="grid grid-cols-3 divide-x">
                      <div className="flex flex-col gap-0.5 pr-3">
                        <span data-board-label="true">{t('hostManager.pid')}</span>
                        <strong data-board-number="true" className="text-base">
                          {bot.pid ?? '—'}
                        </strong>
                      </div>
                      <div className="flex flex-col gap-0.5 px-3">
                        <span data-board-label="true">{t('hostManager.exitCode')}</span>
                        <strong
                          data-board-number="true"
                          className={cn('text-base', lightStatus === 'error' && 'text-destructive')}
                        >
                          {bot.exit_code ?? '—'}
                        </strong>
                      </div>
                      <div className="flex min-w-0 flex-col gap-0.5 pl-3">
                        <span data-board-label="true">GATEWAY</span>
                        <code
                          className="text-primary mt-0.5 truncate font-mono text-xs font-semibold"
                          title={`/maim/${bot.bot_id}/api`}
                        >
                          /maim/{bot.bot_id}/api
                        </code>
                      </div>
                    </div>

                    {/* 屏幕内折叠：目录与运行详情 */}
                    <Collapsible className="border-t pt-2">
                      <CollapsibleTrigger className="text-muted-foreground hover:text-foreground group flex w-full cursor-pointer items-center justify-between gap-2 text-left text-xs font-semibold">
                        <span className="inline-flex items-center gap-1.5">
                          <Folder className="h-3.5 w-3.5 shrink-0" />
                          {t('hostManager.details')}
                        </span>
                        <span className="inline-flex items-center gap-1 font-mono">
                          <span className="max-w-[9rem] truncate">{bot.bot_id}</span>
                          <ChevronDown className="h-3.5 w-3.5 shrink-0 transition-transform group-data-[state=open]:rotate-180" />
                        </span>
                      </CollapsibleTrigger>
                      <CollapsibleContent>
                        <dl className="mt-2.5 grid grid-cols-1 gap-x-3 gap-y-1.5 border-t pt-2.5 text-xs break-all sm:grid-cols-[5.5rem_minmax(0,1fr)]">
                          <dt className="text-muted-foreground">{t('hostManager.directory')}</dt>
                          <dd>
                            <code className="font-mono">{bot.directory}</code>
                          </dd>
                          <dt className="text-muted-foreground">{t('hostManager.logs')}</dt>
                          <dd>
                            <code className="font-mono">{bot.log_path}</code>
                          </dd>
                          <dt className="text-muted-foreground">{t('hostManager.botId')}</dt>
                          <dd>
                            <code className="font-mono">{bot.bot_id}</code>
                          </dd>
                          <dt className="text-muted-foreground">{t('hostShared.gateway')}</dt>
                          <dd className="flex flex-wrap gap-1.5">
                            <code className="bg-muted border px-1.5 py-0.5 font-mono">
                              /maim/{bot.bot_id}/ws
                            </code>
                            <code className="bg-muted border px-1.5 py-0.5 font-mono">
                              /maim/{bot.bot_id}/api
                            </code>
                          </dd>
                          {bot.started_at && (
                            <>
                              <dt className="text-muted-foreground">{t('hostManager.startedAt')}</dt>
                              <dd className="tabular-nums">
                                {new Date(bot.started_at).toLocaleString(i18n.language)}
                              </dd>
                            </>
                          )}
                          {bot.exited_at && (
                            <>
                              <dt className="text-muted-foreground">{t('hostManager.exitedAt')}</dt>
                              <dd className="tabular-nums">
                                {new Date(bot.exited_at).toLocaleString(i18n.language)}
                              </dd>
                            </>
                          )}
                        </dl>
                      </CollapsibleContent>
                    </Collapsible>
                  </DataPanel>

                  {action && (
                    <p className="text-muted-foreground px-0.5 text-xs" role="status">
                      {t(`hostManager.pending.${action}`)}
                    </p>
                  )}

                  {/* 底部单行键帽槽：左区进程启停 + 右区重命名/克隆/归档 */}
                  <BotOperations
                    bot={bot}
                    onChanged={refresh}
                    leadingActions={
                      <>
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={bot.initialization !== 'initialized' || bot.pid !== null || busy}
                          onClick={() => void controlBot(bot, 'start')}
                        >
                          {action === 'start' ? <Loader2 className="animate-spin" /> : <Play />}
                          {t('hostManager.start')}
                        </Button>
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={bot.pid === null || busy}
                          onClick={() => void controlBot(bot, 'stop')}
                        >
                          {action === 'stop' ? <Loader2 className="animate-spin" /> : <Square />}
                          {t('hostManager.stop')}
                        </Button>
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={bot.initialization !== 'initialized' || busy}
                          onClick={() => void controlBot(bot, 'restart')}
                        >
                          {action === 'restart' ? (
                            <Loader2 className="animate-spin" />
                          ) : (
                            <RotateCw />
                          )}
                          {t('hostManager.restart')}
                        </Button>
                      </>
                    }
                  />
                </article>
              </PageFrame>
            )
          })}
        </div>
      </section>

      {/* 4. 底部 WORKSPACE 宿主工作区铭牌栏 */}
      <PageFrame
        asChild
        className="mt-auto flex flex-wrap items-center justify-between gap-3 px-4 py-3 text-xs"
      >
        <footer>
          {data && (
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              <span className="text-muted-foreground font-mono font-bold tracking-widest uppercase">
                WORKSPACE
              </span>
              <span className="text-muted-foreground">{t('hostManager.workspace')}：</span>
              <DataPanel
                asChild
                padding="none"
                className="px-2 py-0.5 font-mono text-xs break-all"
              >
                <code>{data.workspace}</code>
              </DataPanel>
            </div>
          )}
          <p className="text-muted-foreground text-xs">{t('hostManager.unifiedScope')}</p>
        </footer>
      </PageFrame>
    </div>
  )
}
