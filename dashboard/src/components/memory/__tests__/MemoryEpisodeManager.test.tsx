import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  getMemoryEpisode,
  getMemoryEpisodes,
  getMemoryEpisodeStatus,
  processMemoryEpisodePending,
  rebuildMemoryEpisodes,
} from '@/lib/memory-api'

import { MemoryEpisodeManager } from '../MemoryEpisodeManager'

vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: vi.fn() }) }))

vi.mock('@/lib/memory-api', () => ({
  getMemoryEpisode: vi.fn(),
  getMemoryEpisodes: vi.fn(),
  getMemoryEpisodeStatus: vi.fn(),
  processMemoryEpisodePending: vi.fn(),
  rebuildMemoryEpisodes: vi.fn(),
}))

const episodeMock = vi.mocked(getMemoryEpisode)
const episodesMock = vi.mocked(getMemoryEpisodes)
const statusMock = vi.mocked(getMemoryEpisodeStatus)

beforeEach(() => {
  vi.clearAllMocks()
  statusMock.mockResolvedValue({ success: true, counts: {} })
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
