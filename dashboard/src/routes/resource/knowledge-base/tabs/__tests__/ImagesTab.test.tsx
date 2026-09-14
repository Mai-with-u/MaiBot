import { Tabs } from '@/components/ui/tabs'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { MemoryImageAssetPayload, MemoryImageDetailPayload } from '@/lib/memory-api'

import { ImagesTab } from '../ImagesTab'

const api = vi.hoisted(() => ({
  getDetail: vi.fn(),
  getJobs: vi.fn(),
  getWritebackJobs: vi.fn(),
  retryWriteback: vi.fn(),
  retryImageJobs: vi.fn(),
  getList: vi.fn(),
  getStatus: vi.fn(),
  search: vi.fn(),
  saveObservation: vi.fn(),
}))

vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: vi.fn() }) }))
vi.mock('@/lib/memory-api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/memory-api')>()
  return {
    ...actual,
    getMemoryImage: api.getDetail,
    getMemoryImageContentUrl: (assetId: string) => `/memory-images/${assetId}`,
    getMemoryImageJobs: api.getJobs,
    getImageWritebackJobs: api.getWritebackJobs,
    retryImageWritebackJobs: api.retryWriteback,
    retryMemoryImageJobs: api.retryImageJobs,
    getMemoryImages: api.getList,
    getMemoryImageStatus: api.getStatus,
    searchMemoryImages: api.search,
    saveMemoryImageObservation: api.saveObservation,
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
  api.getWritebackJobs.mockResolvedValue({ success: true, items: [], total: 0 })
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
  it('重新排队失败的嵌入与描述任务，并刷新任务列表', async () => {
    api.retryImageJobs.mockResolvedValue({ success: true, count: 2 })
    renderTab()
    const button = await screen.findByRole('button', {
      name: '重试失败的嵌入与描述任务',
      hidden: true,
    })
    const previousCalls = api.getJobs.mock.calls.length
    fireEvent.click(button)
    await waitFor(() => expect(api.retryImageJobs).toHaveBeenCalledOnce())
    await waitFor(() => expect(api.getJobs.mock.calls.length).toBeGreaterThan(previousCalls))
    expect(api.retryWriteback).not.toHaveBeenCalled()
  })

  it('默认收起技术操作，空状态可打开维护入口', async () => {
    api.getList.mockResolvedValue({ success: true, items: [] })
    const scroll = vi.fn()
    vi.spyOn(Element.prototype, 'scrollIntoView').mockImplementation(scroll)
    renderTab()
    const entry = await screen.findByRole('button', { name: '查看历史图片回填' })
    const maintenance = screen.getByText('高级维护 · 历史图片回填与故障处理').closest('details')
    expect(maintenance).not.toHaveAttribute('open')
    expect(screen.getByText('调整匹配范围').closest('details')).not.toHaveAttribute('open')
    fireEvent.click(entry)
    expect(maintenance).toHaveAttribute('open')
    expect(scroll).toHaveBeenCalled()
    vi.restoreAllMocks()
  })

  it('说明修正显示原文和范围提示，并保存新说明', async () => {
    api.getDetail.mockResolvedValue(detail(firstAsset, '旧说明'))
    api.saveObservation.mockImplementation(async () => {
      api.getDetail.mockResolvedValue(detail(firstAsset, '新的图片说明'))
      return { success: true }
    })
    renderTab()
    fireEvent.click((await screen.findAllByRole('button', { name: /图片记忆/ }))[0])
    fireEvent.click(await screen.findByRole('button', { name: '说明有误' }))
    expect(screen.getByText('原说明：旧说明')).toBeInTheDocument()
    expect(screen.getByText(/本次只修改图片说明，不会自动改写关联知识/)).toBeInTheDocument()
    fireEvent.change(screen.getByRole('textbox', { name: '修正后的图片说明' }), {
      target: { value: '新的图片说明' },
    })
    fireEvent.click(screen.getByRole('button', { name: '保存图片认知修正' }))
    await waitFor(() => {
      expect(screen.queryByRole('textbox', { name: '修正后的图片说明' })).not.toBeInTheDocument()
      expect(screen.getByText('新的图片说明')).toBeInTheDocument()
    })
    expect(api.saveObservation).toHaveBeenCalledWith({
      occurrence_id: 'occurrence-asset-a',
      supersedes_id: 'observation-asset-a',
      confirm_status: 'confirmed',
      text: '新的图片说明',
    })
  })

  it('显示入库失败来源并允许重新排队', async () => {
    api.getWritebackJobs.mockResolvedValue({
      success: true,
      total: 1,
      items: [
        {
          session_id: 'real',
          message_id: 'm1',
          chat_name: '测试读书会',
          attempts: 3,
          last_error: '原图缓存已缺失',
        },
      ],
    })
    api.retryWriteback.mockImplementation(async () => {
      api.getWritebackJobs.mockResolvedValue({ success: true, items: [], total: 0 })
      return { success: true, count: 1 }
    })
    renderTab()
    fireEvent.click(screen.getByText('高级维护 · 历史图片回填与故障处理'))
    fireEvent.click(await screen.findByText('入库失败任务 · 1'))
    expect(screen.getByText(/测试读书会 · 消息 m1/)).toBeInTheDocument()
    expect(screen.getByText('原图缓存已缺失')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '重试全部失败入库任务' }))
    await screen.findByText('入库失败任务 · 0')
    expect(api.retryWriteback).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: '重试全部失败入库任务' })).toBeDisabled()
  })

  it('查看相似匹配详情后保留检索结果供继续对比', async () => {
    api.getDetail.mockImplementation((id: string) =>
      Promise.resolve(detail(id === firstAsset.asset_id ? firstAsset : secondAsset, `详情-${id}`))
    )
    api.search.mockResolvedValue({
      success: true,
      status: 'ready',
      related_memory_count: 0,
      timings_ms: {},
      hits: [
        {
          asset_id: secondAsset.asset_id,
          match_kind: 'visual_similar',
          similarity: 0.9,
          occurrences: [],
          observations: [],
          related_memories: [],
        },
      ],
    })
    renderTab()
    const assets = await screen.findAllByRole('button', { name: /图片记忆/ })
    fireEvent.click(assets[0])
    await screen.findByText('详情-asset-a')
    fireEvent.click(screen.getByRole('button', { name: '查找同图与相似图' }))
    fireEvent.click(await screen.findByRole('button', { name: '检索匹配图片' }))
    expect(await screen.findByText('详情-asset-b')).toBeInTheDocument()
    expect(screen.getByText('相似图片 · 0.9000')).toBeInTheDocument()
  })

  it('先展示资产选择与相似检索，再展示次级维护区', async () => {
    api.getDetail.mockResolvedValue(detail(firstAsset, '第一张图片详情'))
    renderTab()

    const assetHeading = (await screen.findAllByText('已记住的图片'))[1]
    const searchHeading = screen.getByText('相似图片检索')
    const maintenanceHeading = screen.getByText('图片维护')

    expect(
      assetHeading.compareDocumentPosition(searchHeading) & Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy()
    expect(
      searchHeading.compareDocumentPosition(maintenanceHeading) & Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy()
    expect(screen.getByRole('button', { name: '查找同图与相似图' })).toBeDisabled()
  })

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
