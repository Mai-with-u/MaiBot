import { useEffect, useState, type ReactNode } from 'react'
import type { HistoryState } from '@tanstack/history'
import { skipToken, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useLocation, useNavigate, useSearch } from '@tanstack/react-router'
import { Plus, Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { MarkdownRenderer } from '@/components/markdown-renderer'
import { SearchArtifacts } from '@/components/search-artifacts'
import { SearchWorkspace } from '@/components/search-dialog'
import { Alert, AlertDescription } from '@/components/ui/alert'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import { DataPanel, PageFrame } from '@/components/ui/surface'
import { useHostRuntime } from '@/host/state'
import {
  deleteAIHelpSession,
  getAIHelpSession,
  listAIHelpSessions,
  type AIHelpSession,
} from '@/lib/ai-search-api'

export function HelpHistoryPage() {
  const { t, i18n } = useTranslation()
  const { selected_bot_id: botId } = useHostRuntime()
  const { session: sessionId } = useSearch({ from: '/protected/help-history' })
  const draft = useLocation({ select: (location) => location.state.helpDraft })
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [pendingDelete, setPendingDelete] = useState<AIHelpSession>()
  const sessionsKey = ['help-history', botId] as const
  const sessions = useQuery({
    queryKey: sessionsKey,
    queryFn: ({ signal }) => listAIHelpSessions(signal),
    staleTime: 0,
  })
  const deletion = useMutation({
    mutationFn: deleteAIHelpSession,
    onSuccess: async (_, deletedId) => {
      await queryClient.cancelQueries({ queryKey: sessionsKey, exact: true })
      queryClient.setQueryData<AIHelpSession[]>(sessionsKey, (items) =>
        items?.filter(({ id }) => id !== deletedId)
      )
      if (sessionId === deletedId) {
        await navigate({ to: '/help-history', search: {}, replace: true })
      }
      queryClient.removeQueries({ queryKey: [...sessionsKey, deletedId], exact: true })
      setPendingDelete(undefined)
      void queryClient.invalidateQueries({ queryKey: sessionsKey, exact: true })
    },
  })
  const detail = useQuery({
    queryKey: ['help-history', botId, sessionId],
    queryFn: sessionId ? ({ signal }) => getAIHelpSession(sessionId, signal) : skipToken,
    staleTime: 0,
  })
  const dateFormat = new Intl.DateTimeFormat(i18n.language, {
    dateStyle: 'medium',
    timeStyle: 'short',
  })

  const transcript = detail.data && (
    <>
      <h2 className="mb-3 text-lg font-semibold break-words">{detail.data.title}</h2>
      <ol className="space-y-4 pb-4">
        {detail.data.turns.map(({ id, question, reply, error }) => (
          <li key={id} className="border-border space-y-2 border-t pt-4">
            <h3 className="font-medium break-words whitespace-pre-wrap">{question}</h3>
            {reply?.answer && <MarkdownRenderer content={reply.answer} untrusted />}
            <SearchArtifacts artifacts={reply?.artifacts} />
            {reply?.suggestions.map((suggestion, index) => (
              <MarkdownRenderer key={index} content={suggestion} untrusted />
            ))}
            {reply?.sources.map((source) => (
              <a
                key={source.url}
                href={source.url}
                target="_blank"
                rel="noreferrer"
                className="text-primary mr-3 text-sm break-words underline"
              >
                {source.title}
              </a>
            ))}
            {error && (
              <Alert variant="destructive">
                <AlertDescription className="break-words">{error}</AlertDescription>
              </Alert>
            )}
            {!reply && !error && (
              <p className="text-muted-foreground text-sm">{t('helpHistory.noReply')}</p>
            )}
          </li>
        ))}
      </ol>
    </>
  )

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 overflow-hidden px-2 py-3 sm:px-3">
      <header className="flex shrink-0 items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">{t('observation.helpHistory')}</h1>
          <p className="text-muted-foreground text-sm">{t('helpHistory.description')}</p>
        </div>
        <Button
          type="button"
          size="icon"
          aria-label={t('helpHistory.newSession')}
          title={t('helpHistory.newSession')}
          onClick={() =>
            void navigate({
              to: '/help-history',
              search: {},
              state: { helpDraft: { id: Date.now() } },
            })
          }
        >
          <Plus aria-hidden="true" />
        </Button>
      </header>

      <div className="grid min-h-0 flex-1 grid-rows-[minmax(0,25%)_minmax(0,1fr)] gap-3 md:grid-cols-[clamp(10.5rem,19vw,15.5rem)_minmax(0,1fr)] md:grid-rows-[minmax(0,1fr)]">
        <PageFrame className="flex min-h-0 min-w-0 flex-col overflow-hidden p-1.5">
          <DataPanel asChild padding="none">
            <nav
              aria-label={t('observation.helpHistory')}
              className="min-h-0 flex-1 overflow-x-hidden overflow-y-auto overscroll-contain"
            >
              {sessions.isPending && (
                <p role="status" className="p-3 text-sm">
                  {t('helpHistory.loading')}
                </p>
              )}
              {sessions.isError && (
                <div className="p-3">
                  <HelpHistoryLoadError onRetry={() => void sessions.refetch()} />
                </div>
              )}
              {sessions.data?.length === 0 && (
                <p className="text-muted-foreground p-3 text-sm">{t('helpHistory.empty')}</p>
              )}
              {sessions.data?.map((item) => (
                <div
                  key={item.id}
                  data-dashboard-list-item="true"
                  data-selected={sessionId === item.id}
                  className="flex min-w-0 items-center"
                >
                  <Link
                    to="/help-history"
                    search={{ session: item.id }}
                    aria-current={sessionId === item.id ? 'page' : undefined}
                    className="min-w-0 flex-1 px-2.5 py-2"
                  >
                    <div className="line-clamp-2 text-sm font-medium break-words">{item.title}</div>
                    <time
                      className="font-mono text-xs opacity-80"
                      dateTime={new Date(item.updated_at * 1000).toISOString()}
                    >
                      {dateFormat.format(item.updated_at * 1000)}
                    </time>
                  </Link>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-xs"
                    className="text-muted-foreground hover:text-destructive mr-1 shrink-0"
                    aria-label={t('helpHistory.deleteSession', { title: item.title })}
                    title={t('helpHistory.deleteTitle')}
                    onClick={() => {
                      deletion.reset()
                      setPendingDelete(item)
                    }}
                  >
                    <Trash2 aria-hidden="true" />
                  </Button>
                </div>
              ))}
            </nav>
          </DataPanel>
        </PageFrame>

        <PageFrame asChild>
          <section className="flex min-h-0 min-w-0 flex-col overflow-hidden">
            {!sessionId && !draft && (
              <p className="text-muted-foreground p-4">{t('helpHistory.select')}</p>
            )}
            {sessionId && detail.isPending && (
              <p role="status" className="p-4">
                {t('helpHistory.loading')}
              </p>
            )}
            {detail.isError && (
              <div className="p-4">
                <HelpHistoryLoadError onRetry={() => void detail.refetch()} />
              </div>
            )}
            {(draft || detail.data) && (
              <HelpSessionComposer
                key={draft?.id ?? sessionId}
                botId={botId}
                sessionId={sessionId}
                draft={draft}
              >
                {transcript}
              </HelpSessionComposer>
            )}
          </section>
        </PageFrame>
      </div>

      <AlertDialog
        open={Boolean(pendingDelete)}
        onOpenChange={(open) => {
          if (!open && !deletion.isPending) setPendingDelete(undefined)
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t('helpHistory.deleteTitle')}</AlertDialogTitle>
            <AlertDialogDescription>
              {t('helpHistory.deleteDescription', { title: pendingDelete?.title })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          {deletion.error && (
            <Alert variant="destructive">
              <AlertDescription>{deletion.error.message}</AlertDescription>
            </Alert>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deletion.isPending}>
              {t('common.cancel')}
            </AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={deletion.isPending}
              onClick={(event) => {
                event.preventDefault()
                if (pendingDelete) deletion.mutate(pendingDelete.id)
              }}
            >
              {t('helpHistory.deleteConfirm')}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

function HelpHistoryLoadError({ onRetry }: { onRetry: () => void }) {
  const { t } = useTranslation()
  return (
    <div className="space-y-2">
      <p role="alert">{t('helpHistory.loadError')}</p>
      <Button variant="outline" size="sm" onClick={onRetry}>
        {t('helpHistory.retryLoad')}
      </Button>
    </div>
  )
}

function HelpSessionComposer({
  botId,
  sessionId,
  draft,
  children,
}: {
  botId: string | null
  sessionId?: string
  draft?: HistoryState['helpDraft']
  children: ReactNode
}) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [autoSubmit] = useState(() => Boolean(draft?.question && !draft.submitted))

  useEffect(() => {
    if (!autoSubmit || !draft?.question || draft.submitted) return

    // 先标记首页草稿已领取，再启动请求。刷新和前进恢复草稿时只填入输入框。
    void navigate({
      to: '/help-history',
      search: { session: sessionId },
      state: { helpDraft: { ...draft, submitted: true } },
      replace: true,
      resetScroll: false,
    })
  }, [autoSubmit, draft, navigate, sessionId])

  return (
    <>
      {draft?.question && draft.submitted && !autoSubmit && (
        <p role="status" className="text-muted-foreground shrink-0 px-3 pt-3 text-sm">
          {t('helpHistory.draftRestored')}
        </p>
      )}
      <SearchWorkspace
        open={!autoSubmit || Boolean(draft?.submitted || sessionId)}
        helpSessionId={sessionId ?? 'new'}
        initialQuestion={draft?.question}
        inlineHistory={children}
        aiSearchRequestId={autoSubmit && draft?.submitted ? 1 : 0}
        onHelpSession={(id) => {
          if (id === sessionId) return
          void navigate({
            to: '/help-history',
            search: { session: id },
            state: { helpDraft: draft ? { id: draft.id } : undefined },
            replace: true,
            resetScroll: false,
          })
        }}
        onHelpSettled={() => {
          void queryClient.invalidateQueries({ queryKey: ['help-history', botId] })
        }}
      />
    </>
  )
}
