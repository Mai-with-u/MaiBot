import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import * as api from '@/lib/chat-migration-api'
import * as chats from '@/lib/chat-management-api'
import { ChatMigrationWorkspace } from '../data-transfer/ChatMigrationWorkspace'

vi.mock('@/lib/chat-migration-api', () => ({
  previewMigration: vi.fn(),
  getMigration: vi.fn(),
  listMigrations: vi.fn(),
  executeMigration: vi.fn(),
  recoverMigration: vi.fn(),
}))
vi.mock('@/lib/chat-management-api', () => ({ getAllChatStreams: vi.fn() }))

const job: api.MigrationJob = {
  id: 'job',
  source: 'A',
  target: 'B',
  status: 'preview',
  error: '',
  created_at: '2026-11-10T10:00:00',
  counts: [],
  memory: { source: 10, target: 20 },
  identities: {
    A: { group_name: '来源群', platform: 'qq' },
    B: { user_nickname: '目标', platform: 'webui' },
  },
  conflicts: [
    {
      id: 'expression:1',
      kind: 'expressions',
      label: '问候方式',
      source: { style: '你好' },
      target: { style: '您好' },
      choices: ['source', 'target', 'both'],
    },
  ],
}

beforeEach(() => {
  Element.prototype.hasPointerCapture = () => false
  Element.prototype.setPointerCapture = () => {}
  Element.prototype.releasePointerCapture = () => {}
  Element.prototype.scrollIntoView = () => {}
  vi.clearAllMocks()
  vi.mocked(chats.getAllChatStreams).mockResolvedValue([])
  vi.mocked(api.listMigrations).mockResolvedValue({ items: [job] })
  vi.mocked(api.getMigration).mockResolvedValue(job)
  vi.mocked(api.executeMigration).mockResolvedValue({})
})
afterEach(cleanup)

async function openJob() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <ChatMigrationWorkspace />
    </QueryClientProvider>
  )
  await userEvent.click(
    await screen.findByRole('button', { name: '已删除的聊天流 → 已删除的聊天流' })
  )
  await screen.findByRole('heading', { name: /逐条处理冲突/ })
}

it('每项冲突和长期记忆覆盖都确认后才能执行', async () => {
  await openJob()
  const execute = screen.getByRole('button', { name: '执行迁移' })
  expect(execute).toBeDisabled()
  await userEvent.click(screen.getByRole('checkbox', { name: /我确认目标聊天流/ }))
  expect(execute).toBeDisabled()
  await userEvent.click(screen.getByRole('button', { name: '两者保留' }))
  expect(execute).toBeEnabled()
  await userEvent.click(execute)
  await waitFor(() =>
    expect(api.executeMigration).toHaveBeenCalledWith('job', { 'expression:1': 'both' }, true)
  )
})

it('服务端检测到过期预览时显示原因，不误报成功', async () => {
  vi.mocked(api.executeMigration).mockRejectedValue(new Error('聊天数据已变化，请重新预览'))
  await openJob()
  await userEvent.click(screen.getByRole('checkbox', { name: /我确认目标聊天流/ }))
  await userEvent.click(screen.getByRole('button', { name: '保留目标' }))
  await userEvent.click(screen.getByRole('button', { name: '执行迁移' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('聊天数据已变化，请重新预览')
  expect(screen.queryByText('迁移完成')).not.toBeInTheDocument()
})

it('证据默认折叠且展开后分页，不挤占冲突操作区', async () => {
  vi.mocked(api.getMigration).mockResolvedValue({
    ...job,
    conflicts: [
      {
        ...job.conflicts[0],
        kind: 'jargons',
        source: {
          content: '测试黑话',
          meaning: '用于测试的含义',
          evidence_messages: JSON.stringify(
            Array.from({ length: 23 }, (_, index) => [
              { platform: 'qq', message_id: `证据编号-${index + 1}` },
            ])
          ),
        },
      },
    ],
  })
  await openJob()
  expect(screen.getByText('用于测试的含义')).toBeVisible()
  expect(screen.queryByText('证据编号-1')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: '采用来源' })).toBeVisible()
  await userEvent.click(screen.getByText('证据消息 · 23 条'))
  expect(await screen.findByText('证据编号-1')).toBeVisible()
  expect(screen.getByText('证据编号-5')).toBeVisible()
  expect(screen.queryByText('证据编号-6')).not.toBeInTheDocument()
})

it('冲突分页且切换未处理筛选保留选择', async () => {
  vi.mocked(api.getMigration).mockResolvedValue({
    ...job,
    conflicts: Array.from({ length: 12 }, (_, index) => ({
      ...job.conflicts[0],
      id: `conflict-${index}`,
      label: `问候方式${index + 1}`,
    })),
  })
  await openJob()
  expect(screen.getByRole('heading', { name: '表达：问候方式5' })).toBeVisible()
  expect(screen.queryByRole('heading', { name: '表达：问候方式6' })).not.toBeInTheDocument()
  await userEvent.click(screen.getAllByRole('button', { name: '采用来源' })[0])
  await userEvent.click(screen.getByRole('checkbox', { name: '只看未处理' }))
  expect(screen.queryByRole('heading', { name: '表达：问候方式1' })).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('checkbox', { name: '只看未处理' }))
  expect(screen.getAllByRole('button', { name: '采用来源' })[0]).toHaveAttribute(
    'aria-pressed',
    'true'
  )
})
