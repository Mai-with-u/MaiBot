import { useCallback, useEffect, useReducer, useRef } from 'react'
import { useTranslation } from 'react-i18next'

import type { SearchItem } from '@/hooks/use-search-index'
import { searchWithAIStream } from '@/lib/ai-search-api'
import type {
  AIHelpPlanItem,
  AIHelpSkill,
  AISearchConfigUpdate,
  AISearchInstallRequest,
  AISearchProgressEvent,
  AISearchResponse,
} from '@/lib/ai-search-api'

// 与后端 AI_SEARCH_MAX_CANDIDATES 保持一致，需容纳全部页面与配置项
const AI_SEARCH_MAX_CANDIDATES = 2000
const PROGRESS_LIMIT = 24

interface SearchMatch {
  item: SearchItem
  reason: string
}

interface SearchRun {
  loading: boolean
  response: AISearchResponse | null
  matches: SearchMatch[]
  progress: AISearchProgressEvent[]
  progressOpen: boolean
  plan: AIHelpPlanItem[]
  skills: AIHelpSkill[]
  installRequests: AISearchInstallRequest[]
  configUpdates: AISearchConfigUpdate[]
  error: string
}

const idleRun: SearchRun = {
  loading: false,
  response: null,
  matches: [],
  progress: [],
  progressOpen: true,
  plan: [],
  skills: [],
  installRequests: [],
  configUpdates: [],
  error: '',
}

type SearchRunAction =
  | { type: 'reset' }
  | { type: 'start' }
  | { type: 'progress'; event: AISearchProgressEvent }
  | { type: 'progress_open'; open: boolean }
  | { type: 'finish'; response: AISearchResponse; matches: SearchMatch[]; error: string }
  | { type: 'fail'; error: string }

function appendUnique<T extends { id: string }>(items: T[], item: T | null | undefined): T[] {
  return item && !items.some(({ id }) => id === item.id) ? [...items, item] : items
}

function reduceSearchRun(state: SearchRun, action: SearchRunAction): SearchRun {
  switch (action.type) {
    case 'reset':
      return idleRun
    case 'start':
      return { ...idleRun, loading: true }
    case 'progress': {
      const { event } = action
      if (event.stage === 'plan') {
        return event.plan ? { ...state, plan: event.plan } : state
      }
      if (event.stage === 'skill') {
        const { skill } = event
        return event.status === 'completed' && skill && !state.skills.includes(skill)
          ? { ...state, skills: [...state.skills, skill] }
          : state
      }
      // 操作记录随工具完成立即显示，即使后续答案生成失败也保留真实结果。
      return {
        ...state,
        progress: [...state.progress, event].slice(-PROGRESS_LIMIT),
        installRequests: appendUnique(state.installRequests, event.install_request),
        configUpdates: appendUnique(state.configUpdates, event.config_update),
      }
    }
    case 'progress_open':
      return { ...state, progressOpen: action.open }
    case 'finish':
      return {
        ...state,
        loading: false,
        response: action.response,
        matches: action.matches,
        progressOpen: !action.response.answer && state.progressOpen,
        error: action.error,
      }
    case 'fail':
      return {
        ...state,
        loading: false,
        error: action.error,
        progress:
          state.progress.at(-1)?.stage === 'failed'
            ? state.progress
            : [
                ...state.progress,
                {
                  type: 'progress',
                  stage: 'failed',
                  status: 'failed',
                  error: action.error,
                } satisfies AISearchProgressEvent,
              ].slice(-PROGRESS_LIMIT),
      }
  }
}

interface SearchRunOptions {
  query: string
  items: SearchItem[]
  helpSessionId?: string
  onHelpSession?: (id: string) => void
  onHelpSettled?: () => void
}

export function useAISearchRun({
  query,
  items,
  helpSessionId,
  onHelpSession,
  onHelpSettled,
}: SearchRunOptions) {
  const { i18n, t } = useTranslation()
  const [state, dispatch] = useReducer(reduceSearchRun, idleRun)
  const sessionRef = useRef(helpSessionId)
  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    abortRef.current?.abort()
    const frameId = window.requestAnimationFrame(() => dispatch({ type: 'reset' }))
    return () => window.cancelAnimationFrame(frameId)
  }, [query])

  useEffect(() => () => abortRef.current?.abort(), [])

  const run = useCallback(
    async (allowPluginConfigWrite: boolean) => {
      const question = query.trim()
      if (!question) return

      abortRef.current?.abort()
      const controller = new AbortController()
      abortRef.current = controller
      dispatch({ type: 'start' })

      const candidates = items.slice(0, AI_SEARCH_MAX_CANDIDATES).map((item, index) => ({
        id: `c${index}`,
        title: item.title.slice(0, 120),
        description: item.description.slice(0, 240),
        category: item.category.slice(0, 80),
        document: (item.schemaDefault === undefined
          ? item.keywords
          : `Schema default (${item.fieldPath}): ${JSON.stringify(item.schemaDefault)}. ${item.keywords}`
        ).slice(0, 2000),
      }))
      const itemsById = new Map(candidates.map(({ id }, index) => [id, items[index]]))

      try {
        const response = await searchWithAIStream(
          {
            query: question,
            language: i18n.language,
            help_session_id: sessionRef.current,
            ...(allowPluginConfigWrite ? { allow_plugin_config_write: true } : {}),
            candidates,
          },
          (event) => {
            if (controller.signal.aborted) return
            if (event.type === 'session') {
              sessionRef.current = event.id
              onHelpSession?.(event.id)
              return
            }
            dispatch({ type: 'progress', event })
          },
          controller.signal
        )
        if (controller.signal.aborted) return

        const matches = response.results.flatMap(({ id, reason }) => {
          const item = itemsById.get(id)
          return item ? [{ item, reason }] : []
        })
        const hasResults =
          response.answer ||
          response.artifacts?.length ||
          matches.length ||
          response.plugins.length ||
          response.install_requests.length ||
          response.config_updates.length
        dispatch({
          type: 'finish',
          response,
          matches,
          error: response.grounding_error || (hasResults ? '' : t('search.aiNoResults')),
        })
      } catch (error) {
        if (!controller.signal.aborted) {
          dispatch({
            type: 'fail',
            error: error instanceof Error ? error.message : t('search.aiFailed'),
          })
        }
      } finally {
        if (!controller.signal.aborted) onHelpSettled?.()
      }
    },
    [i18n.language, items, onHelpSession, onHelpSettled, query, t]
  )

  const setProgressOpen = useCallback((open: boolean) => {
    dispatch({ type: 'progress_open', open })
  }, [])

  return { state, run, setProgressOpen }
}
