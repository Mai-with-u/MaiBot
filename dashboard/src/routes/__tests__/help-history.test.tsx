import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  RouterProvider,
} from '@tanstack/react-router'
import { render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { HelpHistoryPage } from '../help-history'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key, i18n: { language: 'zh' } }),
}))

vi.mock('@/components/markdown-renderer', () => ({
  MarkdownRenderer: () => null,
}))

vi.mock('@/components/search-dialog', () => ({
  SearchWorkspace: ({
    open,
    initialQuestion,
    aiSearchRequestId,
  }: {
    open: boolean
    initialQuestion?: string
    aiSearchRequestId: number
  }) => (
    <div data-testid="help-workspace" data-open={open} data-auto-submit={aiSearchRequestId}>
      {initialQuestion}
    </div>
  ),
}))

vi.mock('@/lib/ai-search-api', () => ({
  getAIHelpSession: vi.fn(),
  listAIHelpSessions: vi.fn().mockResolvedValue([]),
}))

function renderHelpHistory(draft: { id: number; question: string; submitted?: boolean }) {
  const root = createRootRoute()
  const protectedRoute = createRoute({ getParentRoute: () => root, id: 'protected' })
  const helpRoute = createRoute({
    getParentRoute: () => protectedRoute,
    path: '/help-history',
    component: HelpHistoryPage,
  })
  const history = createMemoryHistory({ initialEntries: ['/help-history'] })
  history.replace('/help-history', { helpDraft: draft })
  const router = createRouter({
    routeTree: root.addChildren([protectedRoute.addChildren([helpRoute])]),
    history,
  })
  const queryClient = new QueryClient()

  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  )
  return history
}

describe('HelpHistoryPage draft recovery', () => {
  it('marks a homepage question as submitted before starting the workspace run', async () => {
    const draft = { id: 1, question: '插件如何配置？' }
    const history = renderHelpHistory(draft)

    await waitFor(() => {
      expect(screen.getByTestId('help-workspace')).toHaveAttribute('data-auto-submit', '1')
    })
    expect(history.location.state.helpDraft).toEqual({ ...draft, submitted: true })
    expect(screen.getByText(draft.question)).toBeInTheDocument()
  })

  it('restores a previously submitted question after reload without replaying the run', async () => {
    const draft = { id: 1, question: '插件如何配置？', submitted: true }
    renderHelpHistory(draft)

    expect(await screen.findByTestId('help-workspace')).toHaveAttribute('data-auto-submit', '0')
    expect(screen.getByTestId('help-workspace')).toHaveAttribute('data-open', 'true')
    expect(screen.getByText(draft.question)).toBeInTheDocument()
    expect(screen.getByText('helpHistory.draftRestored')).toBeInTheDocument()
  })
})
