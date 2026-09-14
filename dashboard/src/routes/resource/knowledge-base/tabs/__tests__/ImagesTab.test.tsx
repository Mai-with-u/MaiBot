import { Tabs } from '@/components/ui/tabs'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { MemoryImageAssetPayload, MemoryImageDetailPayload } from '@/lib/memory-api'

import { ImagesTab } from '../ImagesTab'

const api = vi.hoisted(() => ({
  getDetail: vi.fn(),
  getJobs: vi.fn(),
  getList: vi.fn(),
  getStatus: vi.fn(),
}))

vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: vi.fn() }) }))
vi.mock('@/lib/memory-api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/memory-api')>()
  return {
    ...actual,
    getMemoryImage: api.getDetail,
    getMemoryImageContentUrl: (assetId: string) => `/memory-images/${assetId}`,
    getMemoryImageJobs: api.getJobs,
    getMemoryImages: api.getList,
    getMemoryImageStatus: api.getStatus,
  }
})

function asset(assetId: string, contentHash: string): MemoryImageAssetPayload {
  return {
    asset_id: assetId,
    content_hash: contentHash.padEnd(64, contentHash),
    mime_type: 'image/png',
    byte_size: 128,
    width: 32,
    height: 24,
    status: 'active',
    created_at: 1,
    updated_at: 2,
    occurrence_count: 1,
  }
}

function detail(item: MemoryImageAssetPayload, text: string): MemoryImageDetailPayload {
  return {
    success: true,
    asset: item,
    occurrences: [],
    observations: [
      {
        observation_id: `observation-${item.asset_id}`,
        occurrence_id: `occurrence-${item.asset_id}`,
        text,
        source_kind: 'model_description',
        confirm_status: 'unconfirmed',
        version: 1,
      },
    ],
    links: [],
  }
}

const firstAsset = asset('asset-a', 'aaaa')
const secondAsset = asset('asset-b', 'bbbb')

beforeEach(() => {
  vi.clearAllMocks()
  api.getStatus.mockResolvedValue({ success: true, status: 'ready' })
  api.getList.mockResolvedValue({ success: true, items: [firstAsset, secondAsset] })
  api.getJobs.mockResolvedValue({ success: true, items: [], total: 0 })
})

afterEach(() => cleanup())

function renderTab() {
  render(
    <Tabs value="images">
      <ImagesTab />
    </Tabs>
  )
}

describe('ImagesTab 图片详情请求顺序', () => {
  it('较早的成功响应不会覆盖最后选择的图片详情', async () => {
    let resolveFirst!: (payload: MemoryImageDetailPayload) => void
    api.getDetail.mockImplementation((assetId: string) => {
      if (assetId === firstAsset.asset_id) {
        return new Promise((resolve) => {
          resolveFirst = resolve
        })
      }
      return Promise.resolve(detail(secondAsset, '第二张图片详情'))
    })
    renderTab()

    const assetButtons = await screen.findAllByRole('button', { name: /图片记忆/ })
    fireEvent.click(assetButtons[0])
    fireEvent.click(assetButtons[1])
    expect(await screen.findByText('第二张图片详情')).toBeInTheDocument()

    await act(async () => {
      resolveFirst(detail(firstAsset, '第一张图片迟到详情'))
    })

    expect(screen.getByText('第二张图片详情')).toBeInTheDocument()
    expect(screen.queryByText('第一张图片迟到详情')).not.toBeInTheDocument()
  })

  it('较早的失败响应不会污染最后一次成功选择', async () => {
    let rejectFirst!: (reason: Error) => void
    api.getDetail.mockImplementation((assetId: string) => {
      if (assetId === firstAsset.asset_id) {
        return new Promise((_, reject) => {
          rejectFirst = reject
        })
      }
      return Promise.resolve(detail(secondAsset, '当前图片详情'))
    })
    renderTab()

    const assetButtons = await screen.findAllByRole('button', { name: /图片记忆/ })
    fireEvent.click(assetButtons[0])
    fireEvent.click(assetButtons[1])
    expect(await screen.findByText('当前图片详情')).toBeInTheDocument()

    await act(async () => {
      rejectFirst(new Error('旧请求失败'))
    })

    expect(screen.getByText('当前图片详情')).toBeInTheDocument()
    expect(screen.queryByText('旧请求失败')).not.toBeInTheDocument()
  })
})
