import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { EmojiCacheMaintenancePanel } from '../EmojiCacheMaintenancePanel'
import * as systemApi from '@/lib/system-api'
import type {
  LocalCacheCleanupResult,
  LocalCacheImageItem,
  LocalCacheImageListResponse,
} from '@/lib/system-api'
import type { ImageDateFilters } from '@/components/local-cache-image-utils'

const toastMock = vi.hoisted(() => vi.fn())
vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: toastMock }) }))

vi.mock('@/lib/system-api', () => ({
  cleanupLocalCache: vi.fn(),
  deleteLocalCacheImage: vi.fn(),
  deleteLocalCacheImagesByDateRange: vi.fn(),
  deleteLocalCacheImagesOlderThanRecentDays: vi.fn(),
  getLocalCacheImages: vi.fn(),
}))

// 面板桩的回调形状（与 CacheImageListPanel 的 props 对齐，只保留测试要驱动的部分）
interface PanelStubProps {
  list: LocalCacheImageListResponse | null
  filters: ImageDateFilters
  onDelete: (target: string, item: LocalCacheImageItem) => void
  onDeleteAll: (target: string) => void
  onDeleteDateRange: (target: string) => void
  onDeleteOlderThanRecentDays: (target: string, days: 1 | 7 | 30) => void
  onFilterChange: (target: string, filters: ImageDateFilters) => void
  onFilterClear: (target: string) => void
  onFilterSubmit: (target: string, filters?: ImageDateFilters) => void
  onPageChange: (target: string, page: number) => void
  onRefresh: (target: string) => void
}

// 缓存图片浏览面板桩：把父组件传入的回调暴露为按钮，驱动维护编排逻辑
vi.mock('@/components/local-cache-image-browser', () => ({
  CacheImageListPanel: (props: PanelStubProps) => (
    <div data-testid="cache-panel">
      <span data-testid="panel-total">{props.list ? props.list.total : 'null'}</span>
      <button type="button" onClick={() => props.onPageChange('emoji', 2)}>
        stub-page-2
      </button>
      <button
        type="button"
        onClick={() => props.list && props.onDelete('emoji', props.list.data[0])}
      >
        stub-delete-item
      </button>
      <button type="button" onClick={() => props.onDeleteAll('emoji')}>
        stub-delete-all
      </button>
      <button
        type="button"
        onClick={() =>
          props.onFilterChange('emoji', { startDate: '2026-01-01', endDate: '2026-01-31' })
        }
      >
        stub-set-filters
      </button>
      <button type="button" onClick={() => props.onFilterSubmit('emoji')}>
        stub-filter-submit
      </button>
      <button type="button" onClick={() => props.onFilterClear('emoji')}>
        stub-filter-clear
      </button>
      <button type="button" onClick={() => props.onDeleteDateRange('emoji')}>
        stub-delete-range
      </button>
      <button type="button" onClick={() => props.onDeleteOlderThanRecentDays('emoji', 7)}>
        stub-delete-old
      </button>
    </div>
  ),
}))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

function makeItem(overrides: Partial<LocalCacheImageItem> = {}): LocalCacheImageItem {
  return {
    relative_path: '2026-01/a.png',
    file_name: 'a.png',
    full_path: '/cache/2026-01/a.png',
    size: 1024,
    modified_time: 1_760_000_000,
    format: 'png',
    db_id: 1,
    image_hash: 'hash-a',
    description: '缓存图 A',
    is_registered: true,
    is_banned: false,
    no_file_flag: false,
    ...overrides,
  }
}

function makeListResponse(
  overrides: Partial<LocalCacheImageListResponse> = {}
): LocalCacheImageListResponse {
  return {
    success: true,
    target: 'emoji',
    total: 3,
    page: 1,
    page_size: 40,
    total_size: 2048,
    data: [makeItem()],
    date_groups: [],
    ...overrides,
  }
}

function makeCleanupResult(
  overrides: Partial<LocalCacheCleanupResult> = {}
): LocalCacheCleanupResult {
  return {
    success: true,
    message: '清理完成',
    target: 'emoji',
    removed_files: 2,
    removed_bytes: 2048,
    removed_records: 0,
    vacuumed: false,
    database_size_before: null,
    database_size_after: null,
    reclaimed_bytes: 0,
    ...overrides,
  }
}

beforeEach(() => {
  vi.mocked(systemApi.getLocalCacheImages).mockResolvedValue(makeListResponse())
  vi.mocked(systemApi.cleanupLocalCache).mockResolvedValue(makeCleanupResult())
  vi.mocked(systemApi.deleteLocalCacheImage).mockResolvedValue(makeCleanupResult())
  vi.mocked(systemApi.deleteLocalCacheImagesByDateRange).mockResolvedValue(makeCleanupResult())
  vi.mocked(systemApi.deleteLocalCacheImagesOlderThanRecentDays).mockResolvedValue(
    makeCleanupResult()
  )
})

// 打开维护对话框并等待面板桩就绪
async function openPanel() {
  fireEvent.click(screen.getByRole('button', { name: '打开缓存维护' }))
  return await screen.findByTestId('cache-panel')
}

describe('EmojiCacheMaintenancePanel', () => {
  it('初始只展示统计占位，不发起缓存列表请求', () => {
    render(<EmojiCacheMaintenancePanel onCacheChanged={vi.fn()} />)
    expect(screen.getByText('表情包缓存维护')).toBeInTheDocument()
    expect(screen.getByText('0')).toBeInTheDocument()
    expect(screen.getByText('0 B')).toBeInTheDocument()
    expect(systemApi.getLocalCacheImages).not.toHaveBeenCalled()
  })

  it('打开缓存维护：按 emoji 目标拉取第一页并更新统计卡片', async () => {
    render(<EmojiCacheMaintenancePanel onCacheChanged={vi.fn()} />)
    await openPanel()
    await waitFor(() =>
      expect(systemApi.getLocalCacheImages).toHaveBeenCalledWith({
        target: 'emoji',
        page: 1,
        page_size: 40,
        start_date: undefined,
        end_date: undefined,
      })
    )
    // 统计卡片显示 total 与格式化后的占用空间（'3' 同时出现在面板桩里，需按相邻结构定位）
    await waitFor(() =>
      expect(screen.getByText('缓存文件').nextElementSibling).toHaveTextContent('3')
    )
    expect(screen.getByText('2.0 KB')).toBeInTheDocument()
    expect(screen.getByTestId('panel-total')).toHaveTextContent('3')
  })

  it('卡片上的“刷新缓存”按钮请求当前页', async () => {
    render(<EmojiCacheMaintenancePanel onCacheChanged={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: /刷新缓存/ }))
    await waitFor(() => expect(systemApi.getLocalCacheImages).toHaveBeenCalledTimes(1))
  })

  it('获取缓存列表失败时弹出错误 toast', async () => {
    vi.mocked(systemApi.getLocalCacheImages).mockRejectedValue(new Error('磁盘炸了'))
    render(<EmojiCacheMaintenancePanel onCacheChanged={vi.fn()} />)
    await openPanel()
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '获取表情包缓存失败',
          description: '磁盘炸了',
          variant: 'destructive',
        })
      )
    )
  })

  it('清空全部：调用 cleanupLocalCache 并通知外部刷新', async () => {
    const onCacheChanged = vi.fn()
    render(<EmojiCacheMaintenancePanel onCacheChanged={onCacheChanged} />)
    await openPanel()
    fireEvent.click(screen.getByText('stub-delete-all'))
    await waitFor(() => expect(systemApi.cleanupLocalCache).toHaveBeenCalledWith('emoji'))
    await waitFor(() => expect(onCacheChanged).toHaveBeenCalledTimes(1))
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '清理完成',
        description: '删除 2 个文件，释放 2.0 KB。',
      })
    )
  })

  it('删除单个文件：按 relative_path 删除并回落页码后刷新', async () => {
    const onCacheChanged = vi.fn()
    render(<EmojiCacheMaintenancePanel onCacheChanged={onCacheChanged} />)
    await openPanel()
    // 等首屏列表落定，面板桩里才有可删除的条目
    await waitFor(() => expect(screen.getByTestId('panel-total')).toHaveTextContent('3'))
    fireEvent.click(screen.getByText('stub-delete-item'))
    await waitFor(() =>
      expect(systemApi.deleteLocalCacheImage).toHaveBeenCalledWith('emoji', '2026-01/a.png')
    )
    // 删除后重新拉取（打开 1 次 + 删除后 1 次）
    await waitFor(() => expect(systemApi.getLocalCacheImages).toHaveBeenCalledTimes(2))
    expect(onCacheChanged).toHaveBeenCalledTimes(1)
  })

  it('按日期区间删除：使用面板回传的筛选值', async () => {
    render(<EmojiCacheMaintenancePanel onCacheChanged={vi.fn()} />)
    await openPanel()
    fireEvent.click(screen.getByText('stub-set-filters'))
    fireEvent.click(screen.getByText('stub-delete-range'))
    await waitFor(() =>
      expect(systemApi.deleteLocalCacheImagesByDateRange).toHaveBeenCalledWith(
        'emoji',
        '2026-01-01',
        '2026-01-31'
      )
    )
  })

  it('筛选提交与清空分别用对应日期参数重新拉取第一页', async () => {
    render(<EmojiCacheMaintenancePanel onCacheChanged={vi.fn()} />)
    await openPanel()
    fireEvent.click(screen.getByText('stub-set-filters'))
    fireEvent.click(screen.getByText('stub-filter-submit'))
    await waitFor(() =>
      expect(systemApi.getLocalCacheImages).toHaveBeenLastCalledWith({
        target: 'emoji',
        page: 1,
        page_size: 40,
        start_date: '2026-01-01',
        end_date: '2026-01-31',
      })
    )
    fireEvent.click(screen.getByText('stub-filter-clear'))
    await waitFor(() =>
      expect(systemApi.getLocalCacheImages).toHaveBeenLastCalledWith({
        target: 'emoji',
        page: 1,
        page_size: 40,
        start_date: undefined,
        end_date: undefined,
      })
    )
  })

  it('清理最近 N 天以外的缓存：透传天数', async () => {
    render(<EmojiCacheMaintenancePanel onCacheChanged={vi.fn()} />)
    await openPanel()
    fireEvent.click(screen.getByText('stub-delete-old'))
    await waitFor(() =>
      expect(systemApi.deleteLocalCacheImagesOlderThanRecentDays).toHaveBeenCalledWith('emoji', 7)
    )
  })

  it('面板翻页触发对应页码请求', async () => {
    render(<EmojiCacheMaintenancePanel onCacheChanged={vi.fn()} />)
    await openPanel()
    fireEvent.click(screen.getByText('stub-page-2'))
    await waitFor(() =>
      expect(systemApi.getLocalCacheImages).toHaveBeenLastCalledWith(
        expect.objectContaining({ page: 2 })
      )
    )
  })

  it('清理失败时弹出错误 toast 且不通知外部', async () => {
    const onCacheChanged = vi.fn()
    vi.mocked(systemApi.cleanupLocalCache).mockRejectedValue(new Error('清理失败了'))
    render(<EmojiCacheMaintenancePanel onCacheChanged={onCacheChanged} />)
    await openPanel()
    fireEvent.click(screen.getByText('stub-delete-all'))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '清理表情包缓存失败',
          description: '清理失败了',
          variant: 'destructive',
        })
      )
    )
    expect(onCacheChanged).not.toHaveBeenCalled()
  })
})
