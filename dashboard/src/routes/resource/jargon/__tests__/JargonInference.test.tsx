import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ComponentProps } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import * as api from '@/lib/jargon-inference-api'
import * as jargonApi from '@/lib/jargon-api'
import type { Jargon } from '@/types/jargon'
import type {
  JargonInferenceJob,
  JargonInferenceJobDetail,
  JargonInferencePreview,
  JargonInferenceSettings,
  JargonMeaningHistory,
} from '@/types/jargon-inference'
import { JargonInferenceDialog } from '../JargonInferenceDialog'
import { JargonInferenceHistory } from '../JargonInferenceHistory'
import { JargonInferenceMonitor } from '../JargonInferenceMonitor'
import { JargonManagementPage } from '..'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    /** Render default copy and interpolate counts without loading locale resources. */
    t: (key: string, fallback?: string | Record<string, unknown>) => {
      const value = typeof fallback === 'string' ? fallback : String(fallback?.defaultValue ?? key)
      return value.replace(/\{\{(\w+)\}\}/g, (_match, name: string) =>
        String(typeof fallback === 'object' ? (fallback[name] ?? '') : '')
      )
    },
  }),
}))

vi.mock('@/lib/jargon-inference-api', () => ({
  previewJargonInference: vi.fn(),
  startJargonInference: vi.fn(),
  getJargonInferenceJobs: vi.fn(),
  getJargonInferenceJob: vi.fn(),
  cancelJargonInference: vi.fn(),
  retryJargonInference: vi.fn(),
  getJargonInferenceSettings: vi.fn(),
  saveJargonInferenceSettings: vi.fn(),
  rescanJargonInference: vi.fn(),
  getJargonMeaningHistory: vi.fn(),
  restoreJargonMeaning: vi.fn(),
  lockJargonInference: vi.fn(),
}))
vi.mock('@/lib/jargon-api', async (importOriginal) => ({
  ...(await importOriginal<typeof jargonApi>()),
  getJargonDetail: vi.fn(),
  getJargonList: vi.fn(),
  getJargonStats: vi.fn(),
  getJargonGroups: vi.fn(),
  getJargonChatList: vi.fn(),
}))

const clients: QueryClient[] = []

function job(overrides: Partial<JargonInferenceJob> = {}): JargonInferenceJob {
  return {
    id: 'job-1',
    source: 'manual',
    status: 'completed',
    created_at: 1000,
    updated_at: 1001,
    total: 1,
    completed: 1,
    counts: { succeeded: 1 },
    ...overrides,
  }
}

function detail(currentJob: JargonInferenceJob): JargonInferenceJobDetail {
  return {
    job: currentJob,
    total: 1,
    items: [
      {
        jargon_id: 7,
        content: '测试词',
        session_name: '测试群',
        status: 'succeeded',
        reason: '',
        is_jargon: true,
        attempts: 1,
        requests: 3,
      },
    ],
  }
}

function preview(overrides: Partial<JargonInferencePreview> = {}): JargonInferencePreview {
  return { total: 1, eligible: 1, skipped: 0, reasons: {}, ...overrides }
}

function makeJargon(overrides: Partial<Jargon> = {}): Jargon {
  return {
    id: 7,
    content: '测试词',
    meaning: '当前含义',
    session_id: 'chat-1',
    session_ids: ['chat-1'],
    chat_name: '测试群',
    chat_names: ['测试群'],
    is_global: false,
    count: 4,
    is_jargon: true,
    is_legacy_empty_meaning: false,
    is_complete: false,
    created_by: 'AI',
    created_timestamp: '2026-11-09T00:00:00Z',
    updated_timestamp: '2026-11-09T01:00:00Z',
    inference_status: 'succeeded',
    inference_locked: false,
    ...overrides,
  }
}

const history: JargonMeaningHistory = {
  id: 81,
  jargon_id: 7,
  source: 'manual',
  previous_meaning: '旧含义',
  meaning: '当前含义',
  previous_is_jargon: false,
  is_jargon: true,
  created_at: 1000,
}

function settings(overrides: Partial<JargonInferenceSettings> = {}): JargonInferenceSettings {
  return {
    thresholds: [4, 8, 25, 100],
    stop_after_last_threshold: true,
    max_concurrency: 2,
    request_interval_seconds: 1,
    max_retries: 2,
    scheduled_enabled: false,
    scan_interval_hours: 24,
    refresh_interval_hours: 168,
    require_new_evidence: true,
    scheduled_scope: 'pending',
    scheduled_session_ids: ['chat-1'],
    max_items_per_scan: 100,
    evidence_limit: 50,
    history_limit: 20,
    job_retention_days: 7,
    ...overrides,
  }
}

type DialogProps = ComponentProps<typeof JargonInferenceDialog>
function renderDialog(overrides: Partial<DialogProps> = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  })
  clients.push(client)
  const props: DialogProps = {
    open: true,
    onOpenChange: vi.fn(),
    selectedIds: [7, 19],
    pageIds: [7, 8],
    filters: { search: '测试', session_id: 'chat-1' },
    scopeValid: true,
    total: 75,
    chats: [],
    onChanged: vi.fn(),
    ...overrides,
  }
  const view = (next: DialogProps) => (
    <QueryClientProvider client={client}>
      <JargonInferenceDialog {...next} />
    </QueryClientProvider>
  )
  const rendered = render(view(props))
  return {
    ...rendered,
    props,
    client,
    rerenderProps: (next: Partial<DialogProps>) => rendered.rerender(view({ ...props, ...next })),
  }
}

async function choose(user: ReturnType<typeof userEvent.setup>, label: string, option: RegExp) {
  await user.click(screen.getByRole('combobox', { name: label }))
  await user.click(screen.getByRole('option', { name: option }))
}

beforeEach(() => {
  Element.prototype.hasPointerCapture = () => false
  Element.prototype.setPointerCapture = () => {}
  Element.prototype.releasePointerCapture = () => {}
  vi.mocked(api.previewJargonInference).mockResolvedValue(preview())
  vi.mocked(api.startJargonInference).mockResolvedValue(job())
  vi.mocked(api.getJargonInferenceJobs).mockResolvedValue({
    items: [job()],
    total: 1,
    page: 1,
    page_size: 50,
  })
  vi.mocked(api.getJargonInferenceJob).mockResolvedValue(detail(job()))
  vi.mocked(api.getJargonMeaningHistory).mockResolvedValue({ items: [history] })
  vi.mocked(api.restoreJargonMeaning).mockResolvedValue({ success: true })
  vi.mocked(api.lockJargonInference).mockResolvedValue({ success: true })
  vi.mocked(api.getJargonInferenceSettings).mockResolvedValue(settings())
  vi.mocked(api.saveJargonInferenceSettings).mockImplementation(
    async ({ expected_settings: _expected, ...value }) => value
  )
  vi.mocked(api.rescanJargonInference).mockResolvedValue(
    job({ id: 'rescan-job', source: 'rescan' })
  )
  vi.mocked(jargonApi.getJargonStats).mockResolvedValue({ success: true, data: {
    total: 1, confirmed_jargon: 1, confirmed_not_jargon: 0, manual_jargon: 0,
    global_count: 0, complete_count: 0, chat_count: 1, top_chats: {},
  } })
})

afterEach(() => {
  cleanup()
  clients.splice(0).forEach((client) => client.clear())
})

describe('Jargon inference scope and preview', () => {
  it('submits the selected IDs across pages without applying the visible-page filters', async () => {
    const user = userEvent.setup()
    renderDialog()
    await waitFor(() => expect(screen.getByRole('button', { name: '开始批量推断' })).toBeEnabled())
    expect(api.previewJargonInference).toHaveBeenCalledWith({
      ids: [7, 19],
      mode: 'missing',
      force: true,
    })
    await user.click(screen.getByRole('button', { name: '开始批量推断' }))
    await waitFor(() =>
      expect(api.startJargonInference).toHaveBeenCalledWith({
        ids: [7, 19],
        mode: 'missing',
        force: true,
      })
    )
    expect(await screen.findByText('测试词')).toBeInTheDocument()
  })

  it('distinguishes the current page from all matching results and preserves the group/search filter', async () => {
    const user = userEvent.setup()
    const filters = { search: '测试', session_ids: ['chat-1', 'chat-2'], is_complete: true }
    renderDialog({ selectedIds: [], filters })
    await waitFor(() =>
      expect(api.previewJargonInference).toHaveBeenCalledWith({
        ids: [7, 8],
        mode: 'missing',
        force: true,
      })
    )
    await choose(user, '处理范围', /全部结果/)
    await choose(user, '推断方式', /重新推断/)
    await choose(user, '上次推断状态', /信息不足/)
    await user.click(screen.getByRole('checkbox', { name: /忽略自动次数阈值/ }))
    const expected = { filters, mode: 'all', force: false, last_status: 'insufficient_info' }
    await waitFor(() => expect(api.previewJargonInference).toHaveBeenLastCalledWith(expected))
    await waitFor(() => expect(screen.getByRole('button', { name: '开始批量推断' })).toBeEnabled())
    await user.click(screen.getByRole('button', { name: '开始批量推断' }))
    await waitFor(() => expect(api.startJargonInference).toHaveBeenCalledWith(expected))
  })

  it('does not turn an emptied selection into an unfiltered request', async () => {
    const { rerenderProps } = renderDialog()
    await waitFor(() => expect(screen.getByRole('button', { name: '开始批量推断' })).toBeEnabled())
    const calls = vi.mocked(api.previewJargonInference).mock.calls.length
    rerenderProps({ selectedIds: [] })
    expect(screen.getByRole('button', { name: '开始批量推断' })).toBeDisabled()
    expect(screen.getByText(/当前范围没有可处理词条/)).toBeInTheDocument()
    expect(api.previewJargonInference).toHaveBeenCalledTimes(calls)
    expect(api.startJargonInference).not.toHaveBeenCalled()
  })

  it('blocks selected batches above the API limit before making a preview request', () => {
    renderDialog({ selectedIds: Array.from({ length: 5001 }, (_, index) => index + 1) })
    expect(screen.getByRole('alert')).toHaveTextContent('每批最多选择 5,000 条')
    expect(screen.getByRole('button', { name: '开始批量推断' })).toBeDisabled()
    expect(api.previewJargonInference).not.toHaveBeenCalled()
  })

  it('requires a fresh preview after changing scope, even while an older request resolves', async () => {
    const user = userEvent.setup()
    let resolveOld!: (result: JargonInferencePreview) => void
    let resolveNew!: (result: JargonInferencePreview) => void
    vi.mocked(api.previewJargonInference)
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveOld = resolve
          })
      )
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveNew = resolve
          })
      )
    renderDialog()
    await waitFor(() => expect(api.previewJargonInference).toHaveBeenCalledTimes(1))
    await choose(user, '处理范围', /全部结果/)
    await waitFor(() => expect(api.previewJargonInference).toHaveBeenCalledTimes(2))
    await act(async () => {
      resolveOld(preview())
    })
    expect(screen.getByRole('button', { name: '开始批量推断' })).toBeDisabled()
    await act(async () => {
      resolveNew(preview({ eligible: 0, skipped: 1, reasons: { manual: 1 } }))
    })
    await waitFor(() => expect(screen.getByText(/可执行 0 条/)).toBeInTheDocument())
    expect(screen.getByRole('button', { name: '开始批量推断' })).toBeDisabled()
    expect(api.startJargonInference).not.toHaveBeenCalled()
  })

  it('keeps a failed preview visible and blocks launching inference', async () => {
    vi.mocked(api.previewJargonInference).mockRejectedValue(new Error('候选查询失败'))
    renderDialog()
    expect(await screen.findByRole('alert')).toHaveTextContent('候选查询失败')
    expect(screen.getByRole('button', { name: '开始批量推断' })).toBeDisabled()
  })

  it('disables the all-results option for an unresolved group and does not query empty page IDs', async () => {
    const user = userEvent.setup()
    renderDialog({ selectedIds: [], pageIds: [], scopeValid: false, total: 0 })
    expect(screen.getByRole('button', { name: '开始批量推断' })).toBeDisabled()
    await user.click(screen.getByRole('combobox', { name: '处理范围' }))
    expect(screen.getByRole('option', { name: /全部结果/ })).toHaveAttribute(
      'aria-disabled',
      'true'
    )
    expect(api.previewJargonInference).not.toHaveBeenCalled()
  })
})

describe('Jargon inference task controls', () => {
  it('shows each recorded classification independently of the attempt status', async () => {
    const user = userEvent.setup()
    const completed = job({ total: 4, completed: 4, counts: { succeeded: 3, insufficient_info: 1 } })
    const initialDetail = detail(completed)
    const entry = initialDetail.items[0]
    vi.mocked(api.getJargonInferenceJobs).mockResolvedValue({
      items: [completed],
      total: 1,
      page: 1,
      page_size: 50,
    })
    vi.mocked(api.getJargonInferenceJob).mockResolvedValue({
      ...initialDetail,
      total: 4,
      items: [
        { ...entry, content: '群内黑话' },
        { ...entry, jargon_id: 8, content: '普通词语', is_jargon: false },
        {
          ...entry,
          jargon_id: 9,
          content: '证据不足的词语',
          status: 'insufficient_info',
          is_jargon: null,
        },
        { ...entry, jargon_id: 10, content: '无历史判定快照', is_jargon: null },
      ],
    })
    renderDialog()
    await user.click(screen.getByRole('tab', { name: '任务与进度' }))

    const results = await screen.findByRole('list', { name: '词条处理结果' })
    const rows = within(results).getAllByRole('listitem')
    const expected = [
      ['群内黑话', '推断成功', '黑话'],
      ['普通词语', '推断成功', '非黑话'],
      ['证据不足的词语', '信息不足', '无判定结果'],
      ['无历史判定快照', '推断成功', '无判定结果'],
    ]
    expect(rows).toHaveLength(expected.length)
    for (const [index, [content, status, classification]] of expected.entries()) {
      const row = within(rows[index])
      expect(row.getByText(content)).toBeVisible()
      expect(row.getByText(status)).toBeVisible()
      expect(row.getByRole('term')).toHaveTextContent('黑话判定')
      expect(row.getByRole('definition').textContent).toBe(classification)
    }
  })

  it('cancels pending items of the selected running job while preserving completed progress', async () => {
    const user = userEvent.setup()
    const running = job({
      status: 'running',
      total: 4,
      completed: 1,
      counts: { succeeded: 1, running: 1, queued: 2 },
    })
    vi.mocked(api.startJargonInference).mockResolvedValue(running)
    vi.mocked(api.getJargonInferenceJob).mockResolvedValue(detail(running))
    vi.mocked(api.cancelJargonInference).mockResolvedValue(job({ status: 'cancelling' }))
    renderDialog()
    await waitFor(() => expect(screen.getByRole('button', { name: '开始批量推断' })).toBeEnabled())
    await user.click(screen.getByRole('button', { name: '开始批量推断' }))
    const cancel = await screen.findByRole('button', { name: '取消未开始的词条' })
    expect(cancel).toBeEnabled()
    expect(screen.getByText('1 / 4')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '重试失败项' })).toBeDisabled()
    await user.click(cancel)
    await waitFor(() => expect(api.cancelJargonInference).toHaveBeenCalledWith('job-1'))
  })

  it('keeps older manual tasks accessible through source filtering and pagination', async () => {
    const user = userEvent.setup()
    vi.mocked(api.getJargonInferenceJobs).mockImplementation(async (options) => ({
      items: [job({ id: options?.page === 2 ? 'older-manual' : 'job-1' })],
      total: 75,
      page: options?.page ?? 1,
      page_size: 50,
    }))
    renderDialog()
    await user.click(screen.getByRole('tab', { name: '任务与进度' }))
    await waitFor(() =>
      expect(api.getJargonInferenceJobs).toHaveBeenCalledWith({ page: 1, source: 'manual' })
    )
    await user.click(screen.getAllByRole('button', { name: '下一页' })[0])
    await waitFor(() => expect(api.getJargonInferenceJob).toHaveBeenCalledWith('older-manual', 1))
    await choose(user, '任务来源', /全部来源/)
    await waitFor(() =>
      expect(api.getJargonInferenceJobs).toHaveBeenCalledWith({ page: 1, source: undefined })
    )
  })

  it('shows cancelled entries without offering a retry for cancelled-only results', async () => {
    const user = userEvent.setup()
    const cancelled = job({ counts: { cancelled: 1 } })
    vi.mocked(api.getJargonInferenceJob).mockResolvedValue({
      ...detail(cancelled),
      items: [{ ...detail(cancelled).items[0], status: 'cancelled', is_jargon: null }],
    })
    renderDialog()
    await user.click(screen.getByRole('tab', { name: '任务与进度' }))
    expect(await screen.findByText('已取消')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '重试失败项' })).toBeDisabled()
  })

  it('renders coded skip explanations through the locale labels', async () => {
    const user = userEvent.setup()
    const current = job({ counts: { protected: 1 } })
    vi.mocked(api.getJargonInferenceJob).mockResolvedValue({
      ...detail(current),
      items: [{ ...detail(current).items[0], status: 'protected', reason_code: 'manual', reason: '人工维护或已锁定' }],
    })
    renderDialog()
    await user.click(screen.getByRole('tab', { name: '任务与进度' }))
    expect(await screen.findByText('手动维护')).toBeInTheDocument()
    expect(screen.queryByText('人工维护或已锁定')).not.toBeInTheDocument()
  })

  it('keeps readable explanations from legacy tasks without a reason code', async () => {
    const user = userEvent.setup()
    const current = job({ counts: { failed: 1 } })
    vi.mocked(api.getJargonInferenceJob).mockResolvedValue({
      ...detail(current),
      items: [{ ...detail(current).items[0], status: 'failed', reason: '供应商请求超时' }],
    })
    renderDialog()
    await user.click(screen.getByRole('tab', { name: '任务与进度' }))
    expect(await screen.findByText('供应商请求超时')).toBeInTheDocument()
  })

  it('retries a terminal job with failures and displays the new job', async () => {
    const user = userEvent.setup()
    const failed = job({ counts: { failed: 1 } })
    vi.mocked(api.startJargonInference).mockResolvedValue(failed)
    vi.mocked(api.getJargonInferenceJob).mockImplementation(async (id) =>
      detail(job({ id, counts: id === 'job-1' ? { failed: 1 } : { succeeded: 1 } }))
    )
    vi.mocked(api.retryJargonInference).mockResolvedValue(job({ id: 'job-2' }))
    renderDialog()
    await waitFor(() => expect(screen.getByRole('button', { name: '开始批量推断' })).toBeEnabled())
    await user.click(screen.getByRole('button', { name: '开始批量推断' }))
    const retry = await screen.findByRole('button', { name: '重试失败项' })
    expect(retry).toBeEnabled()
    expect(screen.getByRole('button', { name: '取消未开始的词条' })).toBeDisabled()
    await user.click(retry)
    await waitFor(() => expect(api.retryJargonInference).toHaveBeenCalledWith('job-1'))
    await waitFor(() => expect(api.getJargonInferenceJob).toHaveBeenCalledWith('job-2', 1))
  })
})

describe('Jargon inference protection and history', () => {
  it('requires confirmation before restoring the previous meaning, then reads protected server state', async () => {
    const user = userEvent.setup()
    const onChanged = vi.fn()
    const restored = makeJargon({
      meaning: '旧含义',
      is_jargon: false,
      created_by: 'MANUAL',
      inference_locked: true,
    })
    vi.mocked(jargonApi.getJargonDetail).mockResolvedValue({ success: true, data: restored })
    render(
      <JargonInferenceHistory
        jargon={makeJargon()}
        dirty={false}
        onChanged={onChanged}
        onLockChanged={onChanged}
      />
    )
    await user.click(screen.getByRole('button', { name: '查看含义变更历史' }))
    expect(await screen.findByText('旧含义')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '恢复变更前' }))
    const confirmation = screen.getByRole('alertdialog')
    expect(confirmation).toHaveTextContent('标记为手动维护、锁定')
    expect(api.restoreJargonMeaning).not.toHaveBeenCalled()
    await user.click(within(confirmation).getByRole('button', { name: '恢复变更前' }))
    await waitFor(() => expect(api.restoreJargonMeaning).toHaveBeenCalledWith(81))
    await waitFor(() => expect(onChanged).toHaveBeenCalledWith(restored))
  })

  it('prevents lock and history changes while the detail form has unsaved edits', async () => {
    const user = userEvent.setup()
    render(
      <JargonInferenceHistory
        jargon={makeJargon()}
        dirty
        onChanged={vi.fn()}
        onLockChanged={vi.fn()}
      />
    )
    expect(screen.getByRole('switch', { name: '锁定，阻止 AI 推断覆盖' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: '查看含义变更历史' }))
    expect(await screen.findByRole('button', { name: '恢复变更前' })).toBeDisabled()
    expect(api.lockJargonInference).not.toHaveBeenCalled()
    expect(api.restoreJargonMeaning).not.toHaveBeenCalled()
  })

  it('removing an explicit lock preserves manual ownership and shows how to resume AI updates', async () => {
    const user = userEvent.setup()
    const onChanged = vi.fn()
    const manual = makeJargon({ created_by: 'MANUAL', inference_locked: true })
    render(
      <JargonInferenceHistory
        jargon={manual}
        dirty={false}
        onChanged={onChanged}
        onLockChanged={onChanged}
      />
    )
    expect(screen.getByText(/取消固定/)).toBeInTheDocument()
    await user.click(screen.getByRole('switch', { name: '锁定，阻止 AI 推断覆盖' }))
    await waitFor(() => expect(api.lockJargonInference).toHaveBeenCalledWith(7, false))
    expect(onChanged).toHaveBeenCalledWith(7, false)
    expect(jargonApi.getJargonDetail).not.toHaveBeenCalled()
  })

  it('shows history loading errors without mutating the current record', async () => {
    const user = userEvent.setup()
    const onChanged = vi.fn()
    vi.mocked(api.getJargonMeaningHistory).mockRejectedValue(new Error('历史读取失败'))
    render(
      <JargonInferenceHistory
        jargon={makeJargon()}
        dirty={false}
        onChanged={onChanged}
        onLockChanged={onChanged}
      />
    )
    await user.click(screen.getByRole('button', { name: '查看含义变更历史' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('历史读取失败')
    expect(onChanged).not.toHaveBeenCalled()
  })
})

describe('Jargon inference settings', () => {
  it.each(['8, 4', '1, 1', '0, 4', 'word, 4'])(
    'blocks invalid threshold sequence %s before saving',
    async (value) => {
      const user = userEvent.setup()
      renderDialog()
      await user.click(screen.getByRole('tab', { name: '触发与定时设置' }))
      const thresholds = await screen.findByLabelText('累计命中次数阈值')
      await user.clear(thresholds)
      await user.type(thresholds, value)
      await user.click(screen.getByRole('button', { name: '保存设置' }))
      expect(await screen.findByRole('alert')).toHaveTextContent('严格递增的正整数')
      expect(api.saveJargonInferenceSettings).not.toHaveBeenCalled()
    }
  )

  it('saves schedule choices and changed limits while retaining the complete existing configuration', async () => {
    const user = userEvent.setup()
    renderDialog({
      chats: [{ session_id: 'chat-1', chat_name: '测试群', platform: 'qq', is_group: true }],
    })
    await user.click(screen.getByRole('tab', { name: '触发与定时设置' }))
    const thresholds = await screen.findByLabelText('累计命中次数阈值')
    await user.clear(thresholds)
    await user.type(thresholds, '1, 4, 10')
    await user.click(screen.getByRole('switch', { name: '启用定期重新推断' }))
    await user.click(screen.getByRole('switch', { name: '定期更新仅处理有新证据的词条' }))
    await choose(user, '定期处理范围', /全部 AI 词条/)
    const interval = screen.getByRole('spinbutton', { name: '定时扫描周期（小时）' })
    await user.clear(interval)
    await user.type(interval, '12')
    await user.click(screen.getByRole('button', { name: '保存设置' }))
    await waitFor(() =>
      expect(vi.mocked(api.saveJargonInferenceSettings).mock.calls.at(-1)?.[0]).toEqual({
        ...settings({
          thresholds: [1, 4, 10],
          scheduled_enabled: true,
          require_new_evidence: false,
          scheduled_scope: 'all',
          scan_interval_hours: 12,
        }),
        expected_settings: settings(),
      })
    )
    expect(await screen.findByText('设置已保存并生效。')).toHaveAttribute('role', 'status')
  })

  it('merges untouched remote settings but blocks conflicting edits until explicitly reloaded', async () => {
    const user = userEvent.setup()
    const { client } = renderDialog()
    await user.click(screen.getByRole('tab', { name: '触发与定时设置' }))
    const thresholds = await screen.findByLabelText('累计命中次数阈值')
    await user.clear(thresholds)
    await user.type(thresholds, '1, 3, 9')
    await act(async () => {
      client.setQueryData(['jargon-inference', 'settings'], settings({ scan_interval_hours: 12 }))
    })
    expect(thresholds).toHaveValue('1, 3, 9')
    await waitFor(() =>
      expect(screen.getByRole('spinbutton', { name: '定时扫描周期（小时）' })).toHaveValue(12)
    )
    await act(async () => {
      client.setQueryData(
        ['jargon-inference', 'settings'],
        settings({ thresholds: [2, 6], scan_interval_hours: 12 })
      )
    })
    expect(thresholds).toHaveValue('1, 3, 9')
    await waitFor(() => expect(screen.getByRole('button', { name: '保存设置' })).toBeDisabled())
    vi.mocked(api.getJargonInferenceSettings).mockResolvedValue(
      settings({ thresholds: [2, 6], scan_interval_hours: 12 })
    )
    await user.click(screen.getByRole('button', { name: '放弃本地编辑并重新加载' }))
    await waitFor(() => expect(thresholds).toHaveValue('2, 6'))
    await user.click(screen.getByRole('button', { name: '保存设置' }))
    await waitFor(() =>
      expect(vi.mocked(api.saveJargonInferenceSettings).mock.calls.at(-1)?.[0]).toEqual({
        ...settings({ thresholds: [2, 6], scan_interval_hours: 12 }),
        expected_settings: settings({ thresholds: [2, 6], scan_interval_hours: 12 }),
      })
    )
  })

  it('disables all configuration controls during a save so its result cannot overwrite later edits', async () => {
    const user = userEvent.setup()
    let finish!: (value: JargonInferenceSettings) => void
    vi.mocked(api.saveJargonInferenceSettings).mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve
        })
    )
    renderDialog()
    await user.click(screen.getByRole('tab', { name: '触发与定时设置' }))
    const thresholds = await screen.findByLabelText('累计命中次数阈值')
    await user.click(screen.getByRole('button', { name: '保存设置' }))
    expect(thresholds).toBeDisabled()
    expect(screen.getByRole('switch', { name: '启用定期重新推断' })).toBeDisabled()
    expect(screen.getByRole('combobox', { name: '定期处理范围' })).toBeDisabled()
    await act(async () => finish(settings()))
    await waitFor(() => expect(thresholds).toBeEnabled())
  })

  it('starts a scan with saved configuration and navigates to its progress without saving local edits', async () => {
    const user = userEvent.setup()
    renderDialog()
    await user.click(screen.getByRole('tab', { name: '触发与定时设置' }))
    const thresholds = await screen.findByLabelText('累计命中次数阈值')
    await user.clear(thresholds)
    await user.type(thresholds, '1')
    await user.click(screen.getByRole('button', { name: '按已保存阈值检查存量词条' }))
    await waitFor(() => expect(api.rescanJargonInference).toHaveBeenCalled())
    await waitFor(() => expect(api.getJargonInferenceJob).toHaveBeenCalledWith('rescan-job', 1))
    expect(screen.getByRole('tab', { name: '任务与进度' })).toHaveAttribute('aria-selected', 'true')
    expect(api.saveJargonInferenceSettings).not.toHaveBeenCalled()
  })
})

describe('Jargon background progress', () => {
  it.each([false, true])('shares stable group identity with cross-page inference (global=%s)', async (isGlobal) => {
    const user = userEvent.setup()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    clients.push(client)
    const members = isGlobal ? [] : ['chat-1', 'chat-2']
    vi.mocked(jargonApi.getJargonList).mockResolvedValue({ success: true, data: [makeJargon()], total: 1, page: 1, page_size: 20 })
    vi.mocked(jargonApi.getJargonChatList).mockResolvedValue({ success: true, data: [] })
    vi.mocked(jargonApi.getJargonGroups).mockResolvedValue({ success: true, data: [{
      id: 'stable-jargon-group', index: 17, name: '相通测试组', color: '#39845A', jargon_count: 1,
      session_ids: members, members: [], is_global: isGlobal,
    }] })
    render(<QueryClientProvider client={client}><JargonManagementPage /></QueryClientProvider>)
    await screen.findAllByText('测试词')
    await user.click(screen.getByRole('button', { name: /相通测试组/ }))
    await waitFor(() => expect(jargonApi.getJargonList).toHaveBeenLastCalledWith(expect.objectContaining({
      session_ids: isGlobal ? undefined : members,
    })))
    await user.click(screen.getByRole('button', { name: '黑话 AI 推断' }))
    await choose(user, '处理范围', /当前筛选下的全部结果/)
    const expected = { filters: expect.objectContaining({ session_ids: isGlobal ? undefined : members }), mode: 'missing', force: true }
    await waitFor(() => expect(api.previewJargonInference).toHaveBeenLastCalledWith(expected))
    await user.click(screen.getByRole('button', { name: '开始批量推断' }))
    await waitFor(() => expect(api.startJargonInference).toHaveBeenCalledWith(expected))
  })

  it('does not widen an empty stable group into an unfiltered batch', async () => {
    const user = userEvent.setup()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    clients.push(client)
    vi.mocked(jargonApi.getJargonList).mockResolvedValue({ success: true, data: [makeJargon()], total: 1, page: 1, page_size: 20 })
    vi.mocked(jargonApi.getJargonChatList).mockResolvedValue({ success: true, data: [] })
    vi.mocked(jargonApi.getJargonGroups).mockResolvedValue({ success: true, data: [{
      id: 'empty-stable-group', index: 3, name: '空相通组', color: '#39845A', jargon_count: 0,
      session_ids: [], members: [], is_global: false,
    }] })
    render(<QueryClientProvider client={client}><JargonManagementPage /></QueryClientProvider>)
    await screen.findAllByText('测试词')
    await user.click(screen.getByRole('button', { name: /空相通组/ }))
    await waitFor(() => expect(screen.queryAllByText('测试词')).toHaveLength(0))
    await user.click(screen.getByRole('button', { name: '黑话 AI 推断' }))
    expect(screen.getByRole('button', { name: '开始批量推断' })).toBeDisabled()
    await user.click(screen.getByRole('combobox', { name: '处理范围' }))
    expect(screen.getByRole('option', { name: /当前筛选下的全部结果/ })).toHaveAttribute('aria-disabled', 'true')
    expect(api.previewJargonInference).not.toHaveBeenCalled()
    expect(api.startJargonInference).not.toHaveBeenCalled()
  })

  it('keeps the current-page batch available while job progress refreshes the list', async () => {
    const user = userEvent.setup()
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: Infinity } },
    })
    clients.push(client)
    const response = { success: true, data: [makeJargon()], total: 1, page: 1, page_size: 20 }
    vi.mocked(jargonApi.getJargonList).mockResolvedValue(response)
    vi.mocked(jargonApi.getJargonChatList).mockResolvedValue({ success: true, data: [] })
    vi.mocked(jargonApi.getJargonGroups).mockResolvedValue({ success: true, data: [] })
    vi.mocked(jargonApi.getJargonStats).mockResolvedValue({
      success: true,
      data: {
        total: 1,
        confirmed_jargon: 1,
        confirmed_not_jargon: 0,
        manual_jargon: 0,
        global_count: 0,
        complete_count: 0,
        chat_count: 1,
        top_chats: {},
      },
    })
    client.setQueryData(['jargon-inference', 'monitor'], { items: [job()] })
    render(
      <QueryClientProvider client={client}>
        <JargonManagementPage />
      </QueryClientProvider>
    )
    await user.click(screen.getByRole('button', { name: '黑话 AI 推断' }))
    const start = screen.getByRole('button', { name: '开始批量推断' })
    await waitFor(() => expect(start).toBeEnabled())

    let finishRefresh!: (value: typeof response) => void
    const refresh = new Promise<typeof response>((resolve) => {
      finishRefresh = resolve
    })
    vi.mocked(jargonApi.getJargonList).mockReturnValueOnce(refresh)
    await act(async () => {
      client.setQueryData(['jargon-inference', 'monitor'], {
        items: [job({ status: 'running', completed: 0 })],
      })
    })
    await waitFor(() => expect(client.isFetching({ queryKey: ['jargon', 'list'] })).toBe(1))
    await user.click(start)
    await waitFor(() =>
      expect(api.startJargonInference).toHaveBeenCalledWith({
        ids: [7],
        mode: 'missing',
        force: true,
      })
    )
    await act(async () => finishRefresh(response))
  })

  it('refreshes records during activity and on completion even with no dialog mounted', async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: Infinity } },
    })
    clients.push(client)
    const snapshot = {
      items: [job({ status: 'running', completed: 0 })],
      total: 80,
      page: 1,
      page_size: 1,
    }
    client.setQueryData(['jargon-inference', 'monitor'], snapshot, { updatedAt: 100 })
    const invalidate = vi.spyOn(client, 'invalidateQueries')
    render(
      <QueryClientProvider client={client}>
        <JargonInferenceMonitor />
      </QueryClientProvider>
    )
    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ['jargon'] }))
    invalidate.mockClear()
    await act(async () => {
      client.setQueryData(['jargon-inference', 'monitor'], snapshot, { updatedAt: 200 })
    })
    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ['jargon'] }))
    invalidate.mockClear()
    await act(async () => {
      client.setQueryData(
        ['jargon-inference', 'monitor'],
        { ...snapshot, items: [job()] },
        { updatedAt: 300 }
      )
    })
    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ['jargon'] }))
    invalidate.mockClear()
    await act(async () => {
      client.setQueryData(
        ['jargon-inference', 'monitor'],
        { ...snapshot, items: [job()] },
        { updatedAt: 400 }
      )
    })
    expect(invalidate).not.toHaveBeenCalled()
  })
})
