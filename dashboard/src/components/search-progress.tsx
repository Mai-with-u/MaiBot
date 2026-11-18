import type { TFunction } from 'i18next'
import { ChevronDown, CircleAlert, Loader2, Sparkles } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { DataPanel } from '@/components/ui/surface'
import type { AIHelpPlanItem, AIHelpSkill, AISearchProgressEvent } from '@/lib/ai-search-api'
import { cn } from '@/lib/utils'
import '@/styles/data-rows.css'

function getProgressTitle(event: AISearchProgressEvent, t: TFunction): string {
  if (event.stage === 'start') {
    return t('search.progressStart')
  }
  if (event.stage === 'planning') {
    return t('search.progressPlanning', { round: event.round ?? 1 })
  }
  if (event.stage === 'finalizing') {
    return t('search.progressFinalizing')
  }
  if (event.stage === 'correcting') {
    return t('search.progressCorrecting')
  }
  if (event.stage === 'cache_hit') {
    return t('search.progressCacheHit')
  }
  if (event.stage === 'completed') {
    return t('search.progressAnswerCompleted')
  }
  if (event.stage === 'failed') {
    return t('search.progressAnswerFailed')
  }

  const actionKeyByTool: Record<string, string> = {
    search_webui_index: 'search.progressSearchWebui',
    search_official_docs: 'search.progressSearchDocs',
    read_official_docs: 'search.progressReadDocs',
    read_local_config: 'search.progressReadConfig',
    read_recent_logs: 'search.progressReadLogs',
    list_recent_reasoning: 'search.progressListReasoning',
    read_reasoning_record: 'search.progressReadReasoning',
    publish_help_artifact: 'search.progressPublishArtifact',
    search_plugins: 'search.progressSearchPlugins',
    read_plugin_detail: 'search.progressReadPluginDetail',
    read_plugin_readme: 'search.progressReadPluginReadme',
    prepare_plugin_install: 'search.progressPrepareInstall',
    list_installed_plugins: 'search.progressListInstalled',
    read_plugin_config: 'search.progressReadPluginConfig',
    update_plugin_config: 'search.progressUpdatePluginConfig',
  }
  const action = t(actionKeyByTool[event.tool ?? ''] ?? 'search.progressTool')
  if (event.status === 'completed') {
    return t('search.progressCompleted', { action, count: event.count ?? 0 })
  }
  if (event.status === 'failed') {
    return t('search.progressFailed', { action })
  }
  return action
}

function getProgressDetail(event: AISearchProgressEvent): string {
  if (event.error) {
    return event.error
  }
  if (event.query) {
    return event.query
  }
  if (event.titles && event.titles.length > 0) {
    return event.titles.join('、')
  }
  if (event.targets && event.targets.length > 0) {
    return event.targets.join('、')
  }
  return ''
}

function isSettled(event: AISearchProgressEvent) {
  return event.status === 'completed' || event.status === 'failed'
}

/**
 * 同一个工具的「开始」紧跟着它的「完成 / 失败」时合成一步：保留结果标题，
 * 结果没有带查询词时沿用开始那条的说明。
 */
function mergeProgressEvents(events: AISearchProgressEvent[]): AISearchProgressEvent[] {
  const merged: AISearchProgressEvent[] = []
  events.forEach((event, index) => {
    const next = events[index + 1]
    if (
      next &&
      event.tool &&
      !isSettled(event) &&
      next.tool === event.tool &&
      next.stage === event.stage &&
      isSettled(next)
    ) {
      return
    }
    const previous = events[index - 1]
    if (previous && isSettled(event) && previous.tool === event.tool && !isSettled(previous)) {
      merged.push({ ...previous, ...event, query: event.query ?? previous.query })
      return
    }
    merged.push(event)
  })
  return merged
}

export function HelpHarnessProgress({
  plan,
  skills,
}: {
  plan: AIHelpPlanItem[]
  skills: AIHelpSkill[]
}) {
  const { t } = useTranslation()
  if (plan.length === 0 && skills.length === 0) return null

  return (
    <section
      aria-label={t('search.harness.title')}
      aria-live="polite"
      className="border-border space-y-2 rounded-lg border px-3 py-2 text-xs"
    >
      {plan.length > 0 && (
        <div className="space-y-1">
          <h3 className="font-medium">{t('search.harness.plan')}</h3>
          <ol className="space-y-1">
            {plan.map(({ title, status }, index) => (
              <li key={index} className="flex items-baseline justify-between gap-3">
                <span className="min-w-0 break-words">{title}</span>
                <span
                  className={cn(
                    'shrink-0',
                    status === 'in_progress' ? 'text-primary font-medium' : 'text-muted-foreground'
                  )}
                >
                  {t(`search.harness.status.${status}`)}
                </span>
              </li>
            ))}
          </ol>
        </div>
      )}
      {skills.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-muted-foreground">{t('search.harness.skills')}</span>
          {skills.map((skill) => (
            <span key={skill} className="bg-muted rounded px-1.5 py-0.5">
              {t(`search.harness.skill.${skill}`)}
            </span>
          ))}
        </div>
      )}
    </section>
  )
}

export function SearchProgress({
  events,
  loading,
  open,
  onOpenChange,
}: {
  events: AISearchProgressEvent[]
  loading: boolean
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const { t } = useTranslation()
  if (!events.length) return null
  const failed = events.at(-1)?.stage === 'failed'
  const steps = mergeProgressEvents(events)
  const failedSteps = steps.filter((event) => event.stage === 'failed' || event.status === 'failed').length

  return (
    <Collapsible open={open} onOpenChange={onOpenChange} asChild>
      <section
        data-search-progress="true"
        data-failed={failed}
        className="border-border/70 rounded-lg border p-3"
        aria-live="polite"
        aria-label={t('search.progressTitle')}
      >
        <CollapsibleTrigger asChild>
          <button
            type="button"
            className="flex w-full items-center gap-2 text-sm font-medium"
            aria-label={open ? t('search.progressCollapse') : t('search.progressExpand')}
          >
            {loading ? (
              <Loader2 className="text-primary h-4 w-4 animate-spin" />
            ) : failed ? (
              <CircleAlert className="text-destructive h-4 w-4" />
            ) : (
              <Sparkles className="text-primary h-4 w-4" />
            )}
            <span>{t('search.progressTitle')}</span>
            <span className="text-muted-foreground ml-auto font-mono text-xs font-normal">
              {steps.length}
              {failedSteps > 0 && (
                <span className="text-destructive inline-flex items-center gap-0.5">
                  {' · '}
                  <CircleAlert className="h-3 w-3" aria-hidden="true" />
                  {failedSteps}
                </span>
              )}
            </span>
            <ChevronDown
              className={cn('text-muted-foreground h-4 w-4 transition-transform', open && 'rotate-180')}
            />
          </button>
        </CollapsibleTrigger>
        <CollapsibleContent>
          <DataPanel asChild padding="none" className="mt-2">
          <ol>
            {steps.map((event, index) => {
              const detail = getProgressDetail(event)
              const isLatest = loading && index === steps.length - 1
              const isFailed = event.stage === 'failed' || event.status === 'failed'
              return (
                <li
                  key={`${event.stage}-${event.tool ?? ''}-${index}`}
                  data-rows-row="true"
                  className="flex items-start gap-2.5 px-3 py-2 text-xs"
                >
                  <span
                    data-search-progress-step="true"
                    className={cn(
                      'mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center font-mono text-[10px]',
                      isFailed
                        ? 'bg-destructive text-destructive-foreground'
                        : isLatest
                          ? 'bg-primary text-primary-foreground'
                          : 'bg-muted text-muted-foreground'
                    )}
                  >
                    {index + 1}
                  </span>
                  <div className="min-w-0">
                    <div className={isFailed ? 'text-destructive font-medium' : 'text-foreground'}>
                      {getProgressTitle(event, t)}
                    </div>
                    {detail && (
                      <div
                        className={cn(
                          'mt-0.5 break-words',
                          isFailed ? 'text-destructive/90' : 'text-muted-foreground'
                        )}
                      >
                        {detail}
                      </div>
                    )}
                    {/* 开始与完成合成一步后，查询词和找到的结果各占一行 */}
                    {event.query && !event.error && Boolean(event.titles?.length) && (
                      <div className="text-muted-foreground mt-0.5 break-words">
                        {event.titles?.join('、')}
                      </div>
                    )}
                  </div>
                </li>
              )
            })}
          </ol>
          </DataPanel>
        </CollapsibleContent>
      </section>
    </Collapsible>
  )
}
