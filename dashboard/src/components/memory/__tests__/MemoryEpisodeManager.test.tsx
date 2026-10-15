import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  discardMemoryEpisodeMigrationBackfill,
  getMemoryEpisode,
  getMemoryEpisodes,
  getMemoryEpisodeMigrationBackfill,
  getMemoryEpisodeStatus,
  processMemoryEpisodePending,
  rebuildMemoryEpisodes,
} from '@/lib/memory-api'

import { MemoryEpisodeManager } from '../MemoryEpisodeManager'

const toastMock = vi.hoisted(() => vi.fn())

vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: toastMock }) }))

vi.mock('@/lib/memory-api', () => ({
  getMemoryEpisode: vi.fn(),
  getMemoryEpisodes: vi.fn(),
  getMemoryEpisodeMigrationBackfill: vi.fn(),
  getMemoryEpisodeStatus: vi.fn(),
  discardMemoryEpisodeMigrationBackfill: vi.fn(),
  processMemoryEpisodePending: vi.fn(),
  rebuildMemoryEpisodes: vi.fn(),
}))

const episodeMock = vi.mocked(getMemoryEpisode)
const episodesMock = vi.mocked(getMemoryEpisodes)
const statusMock = vi.mocked(getMemoryEpisodeStatus)

beforeEach(() => {
  vi.clearAllMocks()
  statusMock.mockResolvedValue({ success: true, counts: {} })
  vi.mocked(getMemoryEpisodeMigrationBackfill).mockResolvedValue({
    success: true,
    dry_run: true,
    candidates: 0,
    discarded: 0,
    active_skipped: 0,
    by_status: {},
    sample_sources: [],
  })
  vi.mocked(processMemoryEpisodePending).mockResolvedValue({ success: true })
  vi.mocked(rebuildMemoryEpisodes).mockResolvedValue({ success: true })
  episodesMock.mockImplementation(async (params) => ({
    success: true,
    items: params?.source
      ? [
          {
            episode_id: 'episode-target',
            title: '目标情景',
            source: 'source-target',
          },
        ]
      : [
          {
            episode_id: 'episode-default',
            title: '默认情景',
            source: 'source-default',
          },
        ],
  }))
  episodeMock.mockImplementation(async (episodeId) => ({
    success: true,
    episode: {
      episode_id: episodeId,
      title: episodeId === 'episode-target' ? '目标情景' : '默认情景',
      summary: episodeId === 'episode-target' ? '目标情景详情' : '默认情景详情',
      source: episodeId === 'episode-target' ? 'source-target' : 'source-default',
    },
  }))
})

describe('MemoryEpisodeManager', () => {
  it('迁移任务接口返回失败时仍展示 Episode 列表和状态', async () => {
    vi.mocked(getMemoryEpisodeMigrationBackfill).mockResolvedValue({
      success: false,
      error: '不支持的 memory_episode_admin action',
    } as Awaited<ReturnType<typeof getMemoryEpisodeMigrationBackfill>>)

    render(<MemoryEpisodeManager />)

    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '加载迁移任务失败',
          description: '不支持的 memory_episode_admin action',
          variant: 'destructive',
        })
      )
    })
    expect(await screen.findByText('默认情景')).toBeInTheDocument()
    expect(toastMock).not.toHaveBeenCalledWith(expect.objectContaining({ title: '加载情节记忆失败' }))
  })

  it('状态接口返回失败时仍展示 Episode 列表和迁移任务', async () => {
    statusMock.mockResolvedValue({ success: false, error: '状态服务不可用' })

    render(<MemoryEpisodeManager />)

    expect(await screen.findByText('默认情景')).toBeInTheDocument()
    expect(await screen.findByText(/待重建 0、已完成 0、失败 0/)).toBeInTheDocument()
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({ title: '加载 Episode 状态失败', description: '状态服务不可用' })
    )
  })

  it('迁移任务操作位于页面末尾，使用项目确认框和 Toast 反馈', async () => {
    vi.mocked(getMemoryEpisodeMigrationBackfill).mockResolvedValue({
      success: true,
      dry_run: true,
      candidates: 2,
      discarded: 0,
      active_skipped: 0,
      by_status: { done: 2 },
      sample_sources: ['source-a'],
    })
    vi.mocked(discardMemoryEpisodeMigrationBackfill).mockResolvedValue({
      success: true,
      dry_run: false,
      candidates: 2,
      discarded: 2,
      active_skipped: 0,
      by_status: { done: 2 },
      sample_sources: ['source-a'],
    })

    render(<MemoryEpisodeManager />)

    const discardHeading = await screen.findByText('丢弃升级迁移的历史任务')
    const maintenanceHeading = screen.getByText('Episode 运维')
    expect(maintenanceHeading.compareDocumentPosition(discardHeading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '丢弃历史任务' }))
    expect(screen.getByText('确认丢弃历史 Episode 任务')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '取消' }))
    expect(discardMemoryEpisodeMigrationBackfill).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: '丢弃历史任务' }))
    fireEvent.click(screen.getByRole('button', { name: '确认丢弃' }))

    await waitFor(() => expect(discardMemoryEpisodeMigrationBackfill).toHaveBeenCalledOnce())
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '已丢弃历史 Episode 任务',
          description: expect.stringContaining('已丢弃 2 条迁移任务'),
        })
      )
    })
  })

  it('显式跳转目标不会被 Episode 列表默认项覆盖', async () => {
    render(
      <MemoryEpisodeManager
        initialEpisodeId="episode-target"
        initialSource="source-target"
      />
    )

    await waitFor(() => {
      expect(episodeMock).toHaveBeenCalledWith('episode-target')
    })
    expect(await screen.findByText('目标情景')).toBeInTheDocument()
    expect(episodeMock).not.toHaveBeenCalledWith('episode-default')
  })
})
