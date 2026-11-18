import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ComponentProps,
  type ReactNode,
} from 'react'
import { useNavigate } from '@tanstack/react-router'
import { BookOpen, Clock, Lightbulb, Search, Sparkles } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { FloatingPanel } from '@/components/ui/floating-panel'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import { StatusLight } from '@/components/ui/status-light'
import { StreamlineIcon } from '@/components/ui/streamline-icon'
import { MarkdownRenderer } from '@/components/markdown-renderer'
import { SearchArtifacts } from '@/components/search-artifacts'
import { SearchPluginActions } from '@/components/search-plugin-actions'
import { HelpHarnessProgress, SearchProgress } from '@/components/search-progress'
import { SearchSuggestions } from '@/components/search-suggestions'
import { MOBILE_ONLY_GLASS_CLASS } from '@/components/layout/mobile-glass'
import type { MenuIcon } from '@/components/layout/types'
import { useAISearchRun } from '@/hooks/use-ai-search-run'
import { matchSearchItems, useSearchIndex, type SearchItem } from '@/hooks/use-search-index'
import { buildSearchNavigationPath } from '@/lib/config-search-navigation'
import { cn } from '@/lib/utils'

const RecentIcon: MenuIcon = Clock
const RECENT_SEARCH_ROUTES_KEY = 'maibot-search-recent-routes'
const MAX_RECENT_SEARCH_ROUTES = 8

interface SearchDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  query?: string
  onQueryChange?: (query: string) => void
  aiSearchRequestId?: number
}

interface SearchWorkspaceProps extends Omit<SearchDialogProps, 'onOpenChange'> {
  onOpenChange?: SearchDialogProps['onOpenChange']
  helpSessionId?: string
  initialQuestion?: string
  onHelpSession?: (id: string) => void
  onHelpSettled?: () => void
  inlineHistory?: ReactNode
}

function loadRecentSearchRoutes(): string[] {
  if (typeof window === 'undefined') {
    return []
  }

  const stored = localStorage.getItem(RECENT_SEARCH_ROUTES_KEY)
  if (!stored) {
    return []
  }

  try {
    const parsed = JSON.parse(stored)
    return Array.isArray(parsed)
      ? parsed
          .filter((path): path is string => typeof path === 'string')
          .slice(0, MAX_RECENT_SEARCH_ROUTES)
      : []
  } catch {
    return []
  }
}

function InlineSearchPanel({ children }: ComponentProps<typeof FloatingPanel>) {
  return <div className="flex min-h-0 flex-1 flex-col">{children}</div>
}

function InlineResultsArea({ children }: ComponentProps<typeof ScrollArea>) {
  return <>{children}</>
}

export function SearchDialog(props: SearchDialogProps) {
  return <SearchWorkspace {...props} />
}

export function SearchWorkspace({
  open,
  onOpenChange,
  query,
  onQueryChange,
  aiSearchRequestId = 0,
  helpSessionId,
  initialQuestion = '',
  onHelpSession,
  onHelpSettled,
  inlineHistory,
}: SearchWorkspaceProps) {
  const inline = helpSessionId !== undefined
  const [localSearchQuery, setLocalSearchQuery] = useState(initialQuestion)
  const searchQuery = query ?? localSearchQuery
  const setSearchQuery = onQueryChange ?? setLocalSearchQuery
  const [selectedIndex, setSelectedIndex] = useState(0)
  const handledAISearchRequestRef = useRef(0)
  const [recentSearchRoutes, setRecentSearchRoutes] = useState<string[]>(loadRecentSearchRoutes)
  const [allowPluginConfigWrite, setAllowPluginConfigWrite] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const inlineResultsRef = useRef<HTMLDivElement>(null)
  const followLatest = useRef(true)
  const navigate = useNavigate()
  const { t } = useTranslation()
  const { searchItems, configSearchItems, configIndexLoading, configIndexReady } =
    useSearchIndex(open)

  const indexedItems = useMemo(
    () => [...searchItems, ...configSearchItems],
    [configSearchItems, searchItems]
  )
  const { state, run, setProgressOpen } = useAISearchRun({
    query: searchQuery,
    items: indexedItems,
    helpSessionId,
    onHelpSession,
    onHelpSettled,
  })
  const {
    response,
    loading: aiSearchLoading,
    matches: aiSearchItems,
    error: aiSearchError,
    progress: aiSearchProgress,
    plan: helpPlan,
    skills: helpSkills,
  } = state
  const aiAnswer = inline ? '' : response?.answer
  const artifacts = inline ? undefined : response?.artifacts
  const aiSuggestions = inline ? [] : (response?.suggestions ?? [])
  const aiSources = inline ? [] : (response?.sources ?? [])
  const aiPlugins = response?.plugins ?? []
  const aiExpandedTerms = response?.expanded_terms ?? []
  const installRequests = response?.install_requests ?? state.installRequests
  const configUpdates = response?.config_updates ?? state.configUpdates

  useEffect(() => {
    const results = inlineResultsRef.current
    if (results && followLatest.current) results.scrollTop = results.scrollHeight
  }, [inlineHistory, state])

  // 顶栏和对话框共享查询，任一入口修改后都重置结果选择。
  useEffect(() => {
    const frameId = window.requestAnimationFrame(() => setSelectedIndex(0))
    return () => window.cancelAnimationFrame(frameId)
  }, [searchQuery])

  useEffect(() => {
    if (!open) {
      return
    }

    const frameId = window.requestAnimationFrame(() => {
      inputRef.current?.focus()
    })

    return () => window.cancelAnimationFrame(frameId)
  }, [open])

  const searchItemMap = useMemo(
    () => new Map(searchItems.map((item) => [item.path, item])),
    [searchItems]
  )

  const recentSearchItems = useMemo<SearchItem[]>(
    () =>
      recentSearchRoutes.flatMap((path) => {
        const item = searchItemMap.get(path)
        return item
          ? [
              {
                ...item,
                id: `recent:${item.path}`,
                icon: RecentIcon,
                category: t('search.recent'),
              },
            ]
          : []
      }),
    [recentSearchRoutes, searchItemMap, t]
  )

  // 过滤搜索结果
  const normalizedQuery = searchQuery.trim().toLowerCase()
  const candidateItems = normalizedQuery
    ? indexedItems
    : [...recentSearchItems, ...searchItems].filter(
        (item, index, all) => all.findIndex((candidate) => candidate.path === item.path) === index
      )
  const filteredItems = matchSearchItems(candidateItems, normalizedQuery, 80)

  const recommendations = aiSearchItems.map(({ item, reason }) => ({
    ...item,
    description: reason || item.description,
    category: t('search.aiRecommendation'),
  }))
  const recommendedIds = new Set(recommendations.map(({ id }) => id))
  const visibleItems = (
    inline
      ? recommendations
      : [...recommendations, ...filteredItems.filter(({ id }) => !recommendedIds.has(id))]
  ).slice(0, 80)

  const runAISearch = useCallback(
    (allowWrite = allowPluginConfigWrite) => {
      if (!normalizedQuery || configIndexLoading) return
      setAllowPluginConfigWrite(false)
      setSelectedIndex(0)
      void run(allowWrite)
    },
    [allowPluginConfigWrite, configIndexLoading, normalizedQuery, run]
  )

  // 等配置索引加载完成再消费顶栏提交，避免首次打开时漏搜配置项。
  useEffect(() => {
    if (!open) {
      handledAISearchRequestRef.current = aiSearchRequestId
      const frameId = window.requestAnimationFrame(() => setAllowPluginConfigWrite(false))
      return () => window.cancelAnimationFrame(frameId)
    }
    if (
      aiSearchRequestId <= handledAISearchRequestRef.current ||
      !configIndexReady ||
      configIndexLoading
    ) {
      return
    }
    const frameId = window.requestAnimationFrame(() => {
      handledAISearchRequestRef.current = aiSearchRequestId
      void runAISearch(false)
    })
    return () => window.cancelAnimationFrame(frameId)
  }, [aiSearchRequestId, configIndexLoading, configIndexReady, open, runAISearch])

  // 导航到页面
  const handleNavigate = useCallback(
    (item: SearchItem) => {
      const nextRoutes = [
        item.path,
        ...recentSearchRoutes.filter((recentPath) => recentPath !== item.path),
      ].slice(0, MAX_RECENT_SEARCH_ROUTES)
      setRecentSearchRoutes(nextRoutes)
      localStorage.setItem(RECENT_SEARCH_ROUTES_KEY, JSON.stringify(nextRoutes))
      navigate({ to: buildSearchNavigationPath(item.path, item.fieldPath) })
      setSelectedIndex(0)
    },
    [navigate, recentSearchRoutes]
  )

  // 键盘导航
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Escape' && !inline) {
        e.preventDefault()
        onOpenChange?.(false)
      } else if (e.key === 'Enter' && (helpSessionId || e.ctrlKey || e.metaKey)) {
        if (e.nativeEvent.isComposing) return
        e.preventDefault()
        if (!aiSearchLoading && (!helpSessionId || configIndexReady)) void runAISearch()
      } else if (inline) {
        return
      } else if (e.key === 'ArrowDown') {
        e.preventDefault()
        if (visibleItems.length === 0) return
        setSelectedIndex((prev) => (prev + 1) % visibleItems.length)
      } else if (e.key === 'ArrowUp') {
        e.preventDefault()
        if (visibleItems.length === 0) return
        setSelectedIndex((prev) => (prev - 1 + visibleItems.length) % visibleItems.length)
      } else if (e.key === 'Home') {
        e.preventDefault()
        setSelectedIndex(0)
      } else if (e.key === 'End') {
        e.preventDefault()
        setSelectedIndex(Math.max(0, visibleItems.length - 1))
      } else if (e.key === 'Enter' && visibleItems[selectedIndex]) {
        e.preventDefault()
        handleNavigate(visibleItems[selectedIndex])
      }
    },
    [
      aiSearchLoading,
      configIndexReady,
      handleNavigate,
      helpSessionId,
      inline,
      onOpenChange,
      runAISearch,
      selectedIndex,
      visibleItems,
    ]
  )

  const Panel = inline ? InlineSearchPanel : FloatingPanel
  const ResultsArea = inline ? InlineResultsArea : ScrollArea

  return (
    <Panel
      open={open}
      title={t('search.title')}
      onClose={() => onOpenChange?.(false)}
      closeLabel={t('search.close')}
      className="flex h-[min(calc(100vh-2rem),42rem)] w-[min(calc(100vw-2rem),42rem)] flex-col"
      initialWidth={672}
      initialHeight={680}
      initialTop={88}
      mobileFullscreen
    >
      {/* 手机上输入框沉到屏幕底部的玻璃条里，拇指可达 */}
      <div
        data-search-input-bar="true"
        className={
          inline
            ? 'relative order-last shrink-0 border-t px-3 py-2 sm:px-4'
            : 'px-4 pt-3 max-lg:order-last max-lg:px-4 max-lg:pt-2 max-lg:pb-[calc(env(safe-area-inset-bottom)+14px)]'
        }
      >
        {inline && !aiSearchLoading && aiSearchProgress.length === 0 && (
          <SearchSuggestions items={normalizedQuery ? filteredItems.slice(0, 6) : []} />
        )}
        <div
          className={cn(
            'flex items-center gap-2',
            !inline && 'max-lg:rounded-full max-lg:py-1 max-lg:pr-1 max-lg:pl-1',
            !inline && MOBILE_ONLY_GLASS_CLASS
          )}
        >
          <div className="relative min-w-0 flex-1">
            <StreamlineIcon
              name="search-bar-solid"
              fallback={Search}
              className="text-muted-foreground absolute top-1/2 left-3 h-5 w-5 -translate-y-1/2"
            />
            <Input
              ref={inputRef}
              value={searchQuery}
              onChange={(e) => {
                setSearchQuery(e.target.value)
              }}
              onKeyDown={handleKeyDown}
              placeholder={t(helpSessionId ? 'observation.askPlaceholder' : 'search.aiHint')}
              maxLength={helpSessionId ? 500 : undefined}
              readOnly={Boolean(helpSessionId && aiSearchLoading)}
              className="h-12 border-0 pl-11 text-base shadow-none focus-visible:ring-0"
            />
          </div>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-12 shrink-0 gap-1.5 px-3"
            disabled={
              !normalizedQuery ||
              aiSearchLoading ||
              configIndexLoading ||
              Boolean(helpSessionId && !configIndexReady)
            }
            onClick={() => void runAISearch()}
          >
            {aiSearchLoading
              ? t('search.aiSearching')
              : t(helpSessionId ? 'observation.ask' : 'search.aiSearch')}
          </Button>
        </div>
        {normalizedQuery && (
          <label
            className="text-muted-foreground flex w-fit cursor-pointer items-center gap-2 px-2 py-2 text-xs"
            title={t('search.allowPluginConfigWriteHint')}
          >
            <Checkbox
              size="sm"
              checked={allowPluginConfigWrite}
              onCheckedChange={(checked) => setAllowPluginConfigWrite(checked === true)}
              disabled={aiSearchLoading}
            />
            {t('search.allowPluginConfigWrite')}
          </label>
        )}
        {normalizedQuery && (
          <div className="flex min-h-10 flex-wrap items-center gap-2 border-t px-2 py-2">
            <span
              className={cn('text-muted-foreground min-w-0 flex-1 text-xs', !inline && 'truncate')}
            >
              {configIndexLoading
                ? t('search.indexLoading')
                : aiSearchError ||
                  (aiExpandedTerms.length > 0
                    ? t('search.aiUnderstood', { terms: aiExpandedTerms.join('、') })
                    : t('search.aiHint'))}
            </span>
            {!inline && (
              <span className="text-muted-foreground hidden text-xs sm:inline">
                {t('search.aiShortcut')}
              </span>
            )}
          </div>
        )}
      </div>

      <div
        ref={inline ? inlineResultsRef : undefined}
        data-search-results={inline ? undefined : 'true'}
        className={cn(
          'min-h-0 flex-1',
          inline && 'overflow-x-hidden overflow-y-auto overscroll-contain px-3 pt-3 sm:px-4'
        )}
        onScroll={
          inline
            ? ({ currentTarget }) => {
                followLatest.current =
                  currentTarget.scrollHeight -
                    currentTarget.scrollTop -
                    currentTarget.clientHeight <
                  48
              }
            : undefined
        }
      >
        <ResultsArea className="h-full" viewportClassName="px-0">
          {inline && inlineHistory}
          {visibleItems.length > 0 ||
          aiAnswer ||
          Boolean(artifacts?.length) ||
          aiSuggestions.length > 0 ||
          aiPlugins.length > 0 ||
          installRequests.length > 0 ||
          configUpdates.length > 0 ||
          helpPlan.length > 0 ||
          helpSkills.length > 0 ||
          aiSearchProgress.length > 0 ? (
            <div className="space-y-3 p-2">
              {inline && <HelpHarnessProgress plan={helpPlan} skills={helpSkills} />}
              <SearchProgress
                events={aiSearchProgress}
                loading={aiSearchLoading}
                open={state.progressOpen}
                onOpenChange={setProgressOpen}
              />
              {aiAnswer && (
                <section
                  data-search-answer="true"
                  className="border-border/70 bg-muted/30 rounded-lg border p-3"
                >
                  <div className="mb-2 flex items-center gap-2 text-sm font-medium">
                    <Sparkles className="text-primary h-4 w-4" />
                    {t('search.aiAnswer')}
                  </div>
                  <MarkdownRenderer
                    content={aiAnswer}
                    untrusted
                    className="text-muted-foreground text-sm [&_h1]:!mt-3 [&_h1]:!text-lg [&_h2]:!mt-3 [&_h2]:!text-base [&_h3]:!mt-2 [&_h3]:!text-sm"
                  />
                </section>
              )}
              <SearchArtifacts artifacts={artifacts} />
              <SearchPluginActions
                installRequests={installRequests}
                configUpdates={configUpdates}
                onOpenConfig={(pluginId) => {
                  onOpenChange?.(false)
                  void navigate({ to: '/plugin-config', search: { plugin: pluginId } })
                }}
              />
              {aiPlugins.length > 0 && (
                <section className="space-y-2" aria-label={t('search.pluginResults')}>
                  <div className="px-1 text-sm font-medium">{t('search.pluginResults')}</div>
                  <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
                    {aiPlugins.map((plugin) => (
                      <article
                        key={plugin.id}
                        data-search-plugin-card="true"
                        className="border-border/70 flex min-w-0 flex-col gap-2 rounded-lg border p-3"
                      >
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-sm font-medium">{plugin.name}</span>
                          {plugin.version && (
                            <span className="text-muted-foreground font-mono text-xs">
                              v{plugin.recommended_version ?? plugin.version}
                            </span>
                          )}
                        </div>
                        <p className="text-muted-foreground font-mono text-xs break-all">
                          {plugin.id}
                        </p>
                        <Badge
                          variant={plugin.compatibility === 'compatible' ? 'success' : 'outline'}
                          className="gap-1.5 self-start"
                        >
                          <StatusLight
                            status={
                              plugin.compatibility === 'compatible'
                                ? 'on'
                                : plugin.compatibility === 'incompatible'
                                  ? 'error'
                                  : 'off'
                            }
                            glow={false}
                            decorative
                          />
                          {t(`search.pluginCompatibility.${plugin.compatibility}`)}
                        </Badge>
                        {plugin.description && (
                          <p className="text-muted-foreground text-sm">{plugin.description}</p>
                        )}
                        {plugin.reasons.length > 0 && (
                          <p className="text-destructive text-xs">{plugin.reasons.join('；')}</p>
                        )}
                        <div className="mt-auto flex flex-wrap items-center gap-2 border-t border-dashed pt-2">
                          {plugin.recommended_version && (
                            <span className="text-muted-foreground text-xs">
                              {t('search.pluginRecommendedVersion', {
                                version: plugin.recommended_version,
                              })}
                            </span>
                          )}
                          <Button
                            variant="outline"
                            size="sm"
                            className="ml-auto"
                            onClick={() => {
                              onOpenChange?.(false)
                              void navigate({ to: '/plugins', search: { pluginId: plugin.id } })
                            }}
                          >
                            {t('search.pluginOpenStore')}
                          </Button>
                        </div>
                      </article>
                    ))}
                  </div>
                </section>
              )}
              {aiSuggestions.length > 0 && (
                <section className="border-border/70 rounded-lg border p-3">
                  <div className="mb-2 flex items-center gap-2 text-sm font-medium">
                    <Lightbulb className="h-4 w-4" />
                    {t('search.aiSuggestions')}
                  </div>
                  <ul className="text-muted-foreground space-y-1.5 text-sm">
                    {aiSuggestions.map((suggestion) => (
                      <li key={suggestion} className="flex gap-2">
                        <span aria-hidden>•</span>
                        <MarkdownRenderer
                          content={suggestion}
                          untrusted
                          className="min-w-0 text-sm [&_p]:!my-0"
                        />
                      </li>
                    ))}
                  </ul>
                </section>
              )}
              {aiSources.length > 0 && (
                <section className="flex flex-wrap items-center gap-2 px-1">
                  <span className="text-muted-foreground flex items-center gap-1 text-xs">
                    <BookOpen className="h-3.5 w-3.5" />
                    {t('search.aiSources')}
                  </span>
                  {aiSources.map((source) => (
                    <a
                      key={`${source.url}:${source.title}`}
                      href={source.url}
                      target="_blank"
                      rel="noreferrer"
                      className="bg-muted hover:bg-accent rounded px-2 py-1 text-xs underline-offset-2 hover:underline"
                    >
                      {source.title}
                    </a>
                  ))}
                </section>
              )}
              {visibleItems.map((item, index) => {
                const Icon = item.icon
                return (
                  <button
                    key={item.id}
                    onClick={() => handleNavigate(item)}
                    onMouseEnter={() => setSelectedIndex(index)}
                    title={`${item.title} · ${item.description} · ${item.path}`}
                    className={cn(
                      'flex w-full items-center gap-3 rounded-md px-3 py-2.5 text-left transition-colors',
                      index === selectedIndex
                        ? 'bg-accent text-accent-foreground'
                        : 'hover:bg-accent/50'
                    )}
                  >
                    <Icon className="h-5 w-5 shrink-0" />
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-medium">{item.title}</div>
                      <div className="text-muted-foreground truncate text-xs">
                        {item.description}
                      </div>
                    </div>
                    <div className="bg-muted text-muted-foreground max-w-28 shrink-0 truncate rounded px-2 py-1 text-xs">
                      {item.category}
                    </div>
                  </button>
                )
              })}
            </div>
          ) : !inline ? (
            <div className="flex flex-col items-center justify-center py-12 text-center">
              <StreamlineIcon
                name="search-bar-solid"
                fallback={Search}
                className="text-muted-foreground/50 mb-4 h-12 w-12"
              />
              <p className="text-muted-foreground text-sm">
                {searchQuery ? t('search.noResults') : t('search.startSearch')}
              </p>
            </div>
          ) : null}
        </ResultsArea>
      </div>
    </Panel>
  )
}
