import { Link } from '@tanstack/react-router'
import { Loader2 } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { StatusLight, type LightStatus } from '@/components/ui/status-light'
import { DataPanel } from '@/components/ui/surface'
import { selectHostBot, type HostBot } from '@/host/api'
import { useHostRuntime } from '@/host/state'
import '@/styles/data-rows.css'

function statusKey(bot: HostBot): string {
  if (bot.initialization === 'initializing') return 'initializing'
  if (bot.initialization === 'failed') return 'initializationFailed'
  return bot.status === 'crash_loop' ? 'crashLoop' : bot.status
}

function isLoading(bot: HostBot) {
  return bot.initialization === 'initializing' || bot.status === 'starting' || bot.status === 'migrating'
}

function lightOf(bot: HostBot): LightStatus {
  if (isLoading(bot)) return 'mixed'
  return bot.status === 'running' ? 'on' : 'off'
}

/**
 * 没有运行中的 Bot 时替代页面内容：版式和登录页一致，居中一张卡片，主操作去机器人管理。
 * 已经有 Bot 在加载或运行时把它们列出来：加载中的随宿主轮询自动更新，运行中的可以直接切换过去。
 */
export function BotUnavailable() {
  const { t } = useTranslation()
  const { bots, selected_bot_id: selectedId } = useHostRuntime()
  const [switchingId, setSwitchingId] = useState<string | null>(null)
  const selectedBot = bots.find((bot) => bot.bot_id === selectedId)
  const selectedLoading = Boolean(selectedBot && isLoading(selectedBot))
  const activeBots = bots
    .filter((bot) => isLoading(bot) || bot.status === 'running')
    // 当前选中的排最前，其余加载中的在运行中的前面
    .sort(
      (a, b) =>
        Number(b.bot_id === selectedId) - Number(a.bot_id === selectedId) ||
        Number(isLoading(b)) - Number(isLoading(a))
    )

  const switchTo = async (botId: string) => {
    setSwitchingId(botId)
    try {
      await selectHostBot(botId)
    } catch {
      setSwitchingId(null)
    }
  }

  return (
    <div data-bot-unavailable="true" className="flex min-h-full items-center justify-center p-4">
      <Card className="bg-card/80 border-border/50 relative w-full max-w-md shadow-2xl">
        <CardHeader className="text-left">
          <div className="space-y-2">
            <CardTitle role="heading" aria-level={1} className="flex items-center gap-2 text-2xl font-bold">
              <StatusLight status={selectedLoading ? 'mixed' : 'off'} decorative />
              {t(selectedId ? 'hostManager.selectedStopped' : 'hostManager.noSelection')}
            </CardTitle>
            <CardDescription className="flex items-start gap-2 text-base">
              <span aria-hidden="true" className="text-primary font-mono">
                &gt;
              </span>
              <span>
                {selectedLoading
                  ? t('hostManager.pending.start')
                  : t('hostManager.selectToContinue')}
              </span>
            </CardDescription>
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          {activeBots.length > 0 && (
            <DataPanel padding="none" data-bot-unavailable-list="true" aria-label="正在加载或运行的 Bot">
              <div data-rows-group="true" className="flex items-center gap-2">
                <span>正在加载 / 运行中</span>
                <span className="ml-auto font-mono font-normal tracking-normal">{activeBots.length}</span>
              </div>
              {activeBots.map((bot) => {
                const loading = isLoading(bot)
                const current = bot.bot_id === selectedId
                return (
                  <div
                    key={bot.bot_id}
                    data-rows-row="true"
                    className="flex min-w-0 items-center gap-2.5 px-3 py-2 text-sm"
                  >
                    <StatusLight status={lightOf(bot)} label={t(`hostManager.status.${statusKey(bot)}`)} />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate font-semibold">
                        {bot.name}
                        {current && (
                          <span className="text-muted-foreground ml-2 text-xs font-normal">
                            {t('hostManager.selected')}
                          </span>
                        )}
                      </span>
                      <span className="text-muted-foreground block truncate text-xs">
                        {t(`hostManager.status.${statusKey(bot)}`)}
                        {bot.detail ? ` · ${bot.detail}` : ''}
                      </span>
                    </span>
                    {loading ? (
                      <Loader2 className="text-muted-foreground h-4 w-4 shrink-0 animate-spin" aria-hidden="true" />
                    ) : (
                      !current && (
                        <Button
                          type="button"
                          variant="outline"
                          size="xs"
                          disabled={switchingId !== null}
                          onClick={() => void switchTo(bot.bot_id)}
                        >
                          {switchingId === bot.bot_id && <Loader2 className="h-3 w-3 animate-spin" />}
                          {t('hostManager.useBot')}
                        </Button>
                      )
                    )}
                  </div>
                )
              })}
            </DataPanel>
          )}
          <Button asChild className="w-full">
            <Link to="/bots">{t('hostManager.title')}</Link>
          </Button>
          <Button asChild variant="outline" className="w-full">
            <Link to="/config/bot" search={{ mode: 'common' }}>
              公共设置
            </Link>
          </Button>
        </CardContent>
      </Card>
    </div>
  )
}
