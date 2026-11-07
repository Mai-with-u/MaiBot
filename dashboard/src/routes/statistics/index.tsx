import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { AlertCircle, RefreshCw } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Board, BoardRule } from '@/components/ui/board'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import '@/styles/statistics.css'

import { ActivitySection } from './sections/ActivitySection'
import { DetailSection, type DetailDimension, type DetailFilterRequest } from './sections/DetailSection'
import { DistributionSection } from './sections/DistributionSection'
import { OverviewSection } from './sections/OverviewSection'
import { ReliabilitySection } from './sections/ReliabilitySection'
import { OptionSwitch } from './sections/shared'
import { TrendSection } from './sections/TrendSection'
import { formatDateTime, getOrderedPeriods, PERIOD_CODES } from './statistics-model'
import { useDetailedStatistics } from './useDetailedStatistics'

const DEFAULT_PERIOD = 'last_7_days'

function StatisticsPageSkeleton() {
  return (
    <div className="space-y-5 p-4" aria-hidden="true">
      <Skeleton className="h-11 w-full" />
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {Array.from({ length: 8 }, (_, index) => (
          <Skeleton key={index} className="h-28" />
        ))}
      </div>
      <Skeleton className="h-[360px] w-full" />
    </div>
  )
}

interface StatisticsPageProps {
  /** 嵌在日志页的文件夹面板里：不再画自己的外框和大标题，面板就是唯一一层框 */
  embedded?: boolean
  /** 嵌入时把截止时间和刷新按钮放进日志页的工具行 */
  toolbarContainerId?: string
  toolbarVisible?: boolean
}

export function StatisticsPage({ embedded = false, toolbarContainerId, toolbarVisible = true }: StatisticsPageProps = {}) {
  const { t, i18n } = useTranslation()
  const [toolbarRoot, setToolbarRoot] = useState<HTMLElement | null>(null)

  useEffect(() => {
    setToolbarRoot(embedded && toolbarContainerId ? document.getElementById(toolbarContainerId) : null)
  }, [embedded, toolbarContainerId])
  const locale = i18n.resolvedLanguage || i18n.language
  const { data, error, loading, refresh } = useDetailedStatistics()
  const periods = useMemo(() => (data ? getOrderedPeriods(data.periods) : []), [data])
  const [periodKey, setPeriodKey] = useState(DEFAULT_PERIOD)
  const [compare, setCompare] = useState(true)
  const [filterRequest, setFilterRequest] = useState<DetailFilterRequest | null>(null)
  const detailRef = useRef<HTMLDivElement>(null)

  const period = periods.find((item) => item.key === periodKey) ?? periods[0]

  const showInTable = (dimension: DetailDimension, name: string) => {
    setFilterRequest((current) => ({ dimension, name, seq: (current?.seq ?? 0) + 1 }))
    detailRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  const generatedAt = data && (
    <span data-stats-ticker-range="true">
      {t('statisticsPage.generatedAt', { time: formatDateTime(data.generated_at, locale) })}
    </span>
  )
  const refreshButton = (
    <Button type="button" variant="outline" size="sm" onClick={() => void refresh()} disabled={loading}>
      <RefreshCw className={loading ? 'animate-spin' : undefined} />
      {t('statisticsPage.actions.refresh')}
    </Button>
  )
  // 嵌入时和终端、推理过程一样：状态在左，操作在右，占用日志页的工具行
  const embeddedToolbar = (
    <div data-stats-ticker="true" className="w-full">
      {generatedAt}
      <span className="flex-1" />
      {refreshButton}
    </div>
  )

  return (
    <div
      data-statistics-page="true"
      data-embedded={embedded}
      className={embedded ? 'flex min-h-full flex-col' : 'flex min-h-full flex-col p-4 sm:p-6'}
    >
      {embedded && toolbarVisible && toolbarRoot && createPortal(embeddedToolbar, toolbarRoot)}
      <div className="mx-auto w-full max-w-[1920px] space-y-3">
        {!embedded && (
          <header data-stats-ticker="true">
            <div>
              <h1 className="text-2xl font-bold tracking-tight">{t('statisticsPage.title')}</h1>
              <p data-board-label="true" className="mt-0.5">
                {t('statisticsPage.description')}
              </p>
            </div>
            <span className="flex-1" />
            {generatedAt}
            {refreshButton}
          </header>
        )}
        {embedded && !toolbarRoot && embeddedToolbar}

        {error && !data ? (
          <Alert variant="destructive">
            <AlertCircle />
            <AlertTitle>{t('statisticsPage.error.title')}</AlertTitle>
            <AlertDescription>{error || t('statisticsPage.error.description')}</AlertDescription>
          </Alert>
        ) : loading && !data ? (
          <StatisticsPageSkeleton />
        ) : data && period ? (
          <Board sections>
            <BoardRule code="PERIOD">
              <span data-board-label="true">{t('statisticsPage.sections.period')}</span>
              <span data-board-label="true" className="max-md:hidden">
                {t('statisticsPage.periodRange', {
                  start: formatDateTime(period.start_time, locale),
                  end: formatDateTime(period.end_time, locale),
                })}
              </span>
              <OptionSwitch
                label={t('statisticsPage.sections.period')}
                value={period.key}
                onChange={setPeriodKey}
                options={periods.map((item) => ({
                  value: item.key,
                  label: PERIOD_CODES[item.key] ?? item.key,
                  ariaLabel: t(`statisticsPage.periods.${item.key}`),
                }))}
              />
              <label className="flex items-center gap-1.5 text-xs">
                <input
                  type="checkbox"
                  checked={compare}
                  disabled={!period.previous_summary}
                  onChange={(event) => setCompare(event.target.checked)}
                />
                {t('statisticsPage.actions.compare')}
              </label>
            </BoardRule>
            <OverviewSection
              period={period}
              locale={locale}
              compare={compare}
              onShowModels={() => showInTable('models', '')}
            />
            <TrendSection period={period} locale={locale} />
            <DistributionSection period={period} locale={locale} onSelect={showInTable} />
            <ReliabilitySection period={period} locale={locale} />
            <ActivitySection period={period} locale={locale} />
            <div ref={detailRef}>
              <DetailSection period={period} locale={locale} filterRequest={filterRequest} />
            </div>
          </Board>
        ) : data ? (
          <div className="text-muted-foreground flex min-h-40 items-center justify-center text-sm">
            {t('statisticsPage.empty')}
          </div>
        ) : null}
      </div>
    </div>
  )
}
