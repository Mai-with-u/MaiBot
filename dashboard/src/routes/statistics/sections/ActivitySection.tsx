import { useState, type CSSProperties } from 'react'
import { useTranslation } from 'react-i18next'

import { BoardGrid, BoardModule, BoardScreen } from '@/components/ui/board'
import { SegmentedMeter } from '@/components/ui/segmented-meter'

import { formatCurrency, formatDateTime, formatNumber } from '../statistics-model'
import type { DetailedStatisticsPeriod } from '../types'
import { EmptyState, OptionSwitch } from './shared'

type HeatmapMetric = 'messages' | 'costs'

/** 热力图色阶档数 */
const HEAT_LEVELS = 5
const CHAT_LIMIT = 8

export function ActivitySection({ period, locale }: { period: DetailedStatisticsPeriod; locale: string }) {
  return (
    <BoardGrid>
      <HeatmapModule period={period} locale={locale} />
      <ChatRankModule period={period} locale={locale} />
    </BoardGrid>
  )
}

function HeatmapModule({ period, locale }: { period: DetailedStatisticsPeriod; locale: string }) {
  const { t } = useTranslation()
  const [metric, setMetric] = useState<HeatmapMetric>('messages')
  const { trend } = period
  const grid = metric === 'messages' ? trend.heatmap_messages : trend.heatmap_costs
  const maximum = Math.max(0, ...grid.flat())
  const formatValue = (value: number) =>
    metric === 'messages'
      ? t('statisticsPage.heatmap.messagesValue', { value: formatNumber(value, locale) })
      : formatCurrency(value, locale)
  const days = t('statisticsPage.heatmap.weekdays').split(',')
  // 色阶按最大值分成固定档位，零值保持底色
  const level = (value: number) => (value <= 0 || maximum <= 0 ? 0 : Math.ceil((value / maximum) * HEAT_LEVELS))

  return (
    <BoardModule
      code="HEATMAP"
      title={t('statisticsPage.modules.heatmap')}
      span={8}
      spanNarrow={12}
      tone={1}
      actions={
        <OptionSwitch
          label={t('statisticsPage.heatmap.metric')}
          value={metric}
          onChange={setMetric}
          options={(['messages', 'costs'] as const).map((value) => ({
            value,
            label: t(`statisticsPage.heatmap.metrics.${value}`),
          }))}
        />
      }
    >
      <BoardScreen flush className="flex flex-col gap-2">
        {trend.truncated && (
          <p data-board-label="true">
            {t('statisticsPage.trend.truncated', { start: formatDateTime(trend.start_time, locale) })}
          </p>
        )}
        <div data-stats-scope="true">
          {maximum <= 0 ? (
            <EmptyState />
          ) : (
            <>
              <div data-stats-heatmap="true" role="grid" aria-label={t('statisticsPage.modules.heatmap')}>
                <span data-axis="true" />
                {Array.from({ length: 24 }, (_, hour) => (
                  <span key={hour} data-axis="true">
                    {hour % 3 === 0 ? hour : ''}
                  </span>
                ))}
                {grid.map((row, day) => [
                  <span key={`day-${day}`} data-axis="true" className="text-left">
                    {days[day]}
                  </span>,
                  ...row.map((value, hour) => (
                    <span
                      key={`${day}-${hour}`}
                      role="gridcell"
                      data-cell="true"
                      title={t('statisticsPage.heatmap.cellTitle', {
                        day: days[day],
                        hour,
                        value: formatValue(value),
                      })}
                      style={{ '--heat': `${(level(value) / HEAT_LEVELS) * 100}%` } as CSSProperties}
                    />
                  )),
                ])}
              </div>
              <div className="mt-2 flex items-center gap-1.5 text-xs">
                <span>{t('statisticsPage.heatmap.less')}</span>
                <span data-stats-heatmap="true" className="!flex" aria-hidden="true">
                  {Array.from({ length: HEAT_LEVELS + 1 }, (_, index) => (
                    <span
                      key={index}
                      data-cell="true"
                      className="w-3.5"
                      style={{ '--heat': `${(index / HEAT_LEVELS) * 100}%` } as CSSProperties}
                    />
                  ))}
                </span>
                <span>{t('statisticsPage.heatmap.more')}</span>
              </div>
            </>
          )}
        </div>
      </BoardScreen>
    </BoardModule>
  )
}

function ChatRankModule({ period, locale }: { period: DetailedStatisticsPeriod; locale: string }) {
  const { t } = useTranslation()
  const costByName = new Map(period.distributions.chat_costs.map((item) => [item.name, item.value]))
  const chats = [...period.chats].sort((a, b) => b.message_count - a.message_count).slice(0, CHAT_LIMIT)
  const maximum = chats[0]?.message_count ?? 0

  return (
    <BoardModule
      code="CHATS"
      title={t('statisticsPage.modules.chats')}
      span={4}
      spanNarrow={12}
      tone={2}
      actions={
        period.chats.length > CHAT_LIMIT ? (
          <span data-board-label="true">{t('statisticsPage.chats.topHint', { count: CHAT_LIMIT, total: period.chats.length })}</span>
        ) : undefined
      }
    >
      <BoardScreen>
        {chats.length === 0 ? (
          <EmptyState />
        ) : (
          chats.map((chat, index) => {
            const cost = costByName.get(chat.name)
            return (
              <div key={chat.name} data-stats-row="true" className="flex-wrap">
                <span data-board-label="true" className="w-5 font-mono">
                  {String(index + 1).padStart(2, '0')}
                </span>
                <span data-stats-row-name="true" title={chat.name}>
                  {chat.name}
                </span>
                <span data-board-number="true">
                  {t('statisticsPage.heatmap.messagesValue', { value: formatNumber(chat.message_count, locale) })}
                </span>
                <span className="w-24 text-right">
                  {cost === undefined ? (
                    <span data-board-label="true">—</span>
                  ) : (
                    <>
                      <span data-board-number="true">{formatCurrency(cost, locale)}</span>
                      <span data-board-label="true" className="block">
                        {t('statisticsPage.chats.costPer100', {
                          cost: formatCurrency(chat.message_count > 0 ? (cost / chat.message_count) * 100 : 0, locale, 3),
                        })}
                      </span>
                    </>
                  )}
                </span>
                <SegmentedMeter
                  label={chat.name}
                  total={maximum}
                  segments={[{ label: chat.name, value: chat.message_count, className: 'bg-primary' }]}
                  className="ml-7 h-1 basis-full"
                />
              </div>
            )
          })
        )}
      </BoardScreen>
    </BoardModule>
  )
}
