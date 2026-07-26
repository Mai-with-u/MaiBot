import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { CacheImageListPanel } from '../local-cache-image-browser'

import { backendApi } from '@/lib/http'
import type {
  LocalCacheImageItem,
  LocalCacheImageListResponse,
} from '@/lib/system-api'

// 只替换 backendApi.get（预览图请求），其余导出保持原样
vi.mock('@/lib/http', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/http')>()
  return {
    ...actual,
    backendApi: { get: vi.fn() },
  }
})

function makeItem(overrides: Partial<LocalCacheImageItem> = {}): LocalCacheImageItem {
  return {
    relative_path: '2026-01/cat.png',
    file_name: 'cat.png',
    full_path: '/data/cache/2026-01/cat.png',
    size: 2048,
    modified_time: 1767225600,
    format: 'png',
    db_id: 7,
    image_hash: 'abc123',
    description: '一张猫图',
    is_registered: null,
    is_banned: false,
    no_file_flag: null,
    ...overrides,
  }
}

function makeList(
  overrides: Partial<LocalCacheImageListResponse> = {}
): LocalCacheImageListResponse {
  return {
    success: true,
    target: 'images',
    total: 1,
    page: 1,
    page_size: 40,
    total_size: 2048,
    data: [makeItem()],
    date_groups: [],
    ...overrides,
  }
}

/** 每个用例内新建回调集合，避免 mockReset 清空实现造成状态串扰 */
function makeProps() {
  return {
    cleanupDisabled: false,
    deletingKey: null as string | null,
    filters: { startDate: '', endDate: '' },
    isLoading: false,
    list: makeList() as LocalCacheImageListResponse | null,
    onDelete: vi.fn(),
    onDeleteAll: vi.fn(),
    onDeleteDateRange: vi.fn(),
    onDeleteOlderThanRecentDays: vi.fn(),
    onFilterChange: vi.fn(),
    onFilterClear: vi.fn(),
    onFilterSubmit: vi.fn(),
    onPageChange: vi.fn(),
    onRefresh: vi.fn(),
    target: 'images' as const,
  }
}

describe('CacheImageListPanel', () => {
  beforeEach(() => {
    vi.mocked(backendApi.get).mockResolvedValue(new Blob(['img']))
  })

  it('加载中且无数据时渲染骨架屏', () => {
    const props = makeProps()
    const { container } = render(
      <CacheImageListPanel {...props} isLoading list={null} />
    )

    expect(container.querySelectorAll('.animate-pulse').length).toBeGreaterThan(0)
    expect(screen.queryByText('暂无图片缓存')).not.toBeInTheDocument()
    expect(screen.getByText(/当前列表/)).toHaveTextContent('当前列表共 0 个文件，占用 0 B')
  })

  it('空列表时展示空态提示且不渲染分页', () => {
    const props = makeProps()
    render(
      <CacheImageListPanel
        {...props}
        list={makeList({ total: 0, total_size: 0, data: [] })}
      />
    )

    expect(screen.getByText('暂无图片缓存')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /上一页/ })).not.toBeInTheDocument()
    // 无日期分组时显示占位文案
    expect(screen.getByText('暂无可按日期浏览的缓存文件')).toBeInTheDocument()
  })

  it('渲染表情包条目的徽章、描述与预览图', async () => {
    const props = makeProps()
    const item = makeItem({
      db_id: null,
      is_registered: true,
      is_banned: true,
    })
    render(
      <CacheImageListPanel
        {...props}
        target="emoji"
        list={makeList({ target: 'emoji', data: [item] })}
      />
    )

    // 标题按 target 切换为表情包缓存
    expect(screen.getByText('表情包缓存列表')).toBeInTheDocument()
    expect(screen.getByText('cat.png')).toBeInTheDocument()
    expect(screen.getByText('PNG')).toBeInTheDocument()
    expect(screen.getByText('仅文件')).toBeInTheDocument()
    expect(screen.getByText('已注册')).toBeInTheDocument()
    expect(screen.getByText('已禁用')).toBeInTheDocument()
    expect(screen.getByText('一张猫图')).toBeInTheDocument()
    expect(screen.getByText('hash: abc123')).toBeInTheDocument()

    // 预览图通过 backendApi.get 拉取 blob 并生成对象 URL
    const image = await screen.findByAltText('cat.png')
    expect(image.getAttribute('src')).toMatch(/^blob:/)
    expect(vi.mocked(backendApi.get).mock.calls[0][0]).toContain(
      'relative_path=2026-01%2Fcat.png'
    )
  })

  it('预览加载失败时显示占位图标', async () => {
    vi.mocked(backendApi.get).mockRejectedValue(new Error('加载失败'))
    const props = makeProps()
    const { container } = render(<CacheImageListPanel {...props} />)

    await waitFor(() => {
      expect(container.querySelector('.lucide-image-off')).toBeInTheDocument()
    })
    expect(screen.queryByAltText('cat.png')).not.toBeInTheDocument()
  })

  it('修改时间无效时显示横杠占位', () => {
    const props = makeProps()
    render(
      <CacheImageListPanel
        {...props}
        list={makeList({ data: [makeItem({ modified_time: 0 })] })}
      />
    )

    expect(screen.getByText('-')).toBeInTheDocument()
  })

  it('刷新与日期筛选控件按目标透传回调', () => {
    const props = makeProps()
    render(<CacheImageListPanel {...props} />)

    fireEvent.click(screen.getByRole('button', { name: '刷新列表' }))
    expect(props.onRefresh).toHaveBeenCalledWith('images')

    fireEvent.change(document.getElementById('images-cache-start-date') as HTMLInputElement, {
      target: { value: '2026-01-01' },
    })
    expect(props.onFilterChange).toHaveBeenCalledWith('images', {
      startDate: '2026-01-01',
      endDate: '',
    })

    fireEvent.click(screen.getByRole('button', { name: '按日期浏览' }))
    expect(props.onFilterSubmit).toHaveBeenCalledWith('images')

    // 无日期筛选时清空按钮禁用
    expect(screen.getByRole('button', { name: '清空日期' })).toBeDisabled()
  })

  it('有日期筛选时可清空日期且文案切换为日期范围', () => {
    const props = makeProps()
    render(
      <CacheImageListPanel
        {...props}
        filters={{ startDate: '2026-01-01', endDate: '2026-01-31' }}
      />
    )

    expect(screen.getByText(/当前日期范围内/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '清空日期' }))
    expect(props.onFilterClear).toHaveBeenCalledWith('images')
  })

  it('点击日期分组按钮时同时更新筛选并提交', () => {
    const props = makeProps()
    render(
      <CacheImageListPanel
        {...props}
        list={makeList({
          date_groups: [{ date: '2026-01-05', file_count: 2, total_size: 4096 }],
        })}
      />
    )

    const groupButton = screen.getByRole('button', { name: /2026-01-05/ })
    expect(groupButton).toHaveTextContent('2 个 / 4.0 KB')

    fireEvent.click(groupButton)
    const nextFilters = { startDate: '2026-01-05', endDate: '2026-01-05' }
    expect(props.onFilterChange).toHaveBeenCalledWith('images', nextFilters)
    expect(props.onFilterSubmit).toHaveBeenCalledWith('images', nextFilters)
  })

  it('分页信息与翻页回调按当前页计算', () => {
    const props = makeProps()
    render(
      <CacheImageListPanel
        {...props}
        list={makeList({
          total: 100,
          page: 2,
          page_size: 40,
          data: [makeItem()],
        })}
      />
    )

    expect(screen.getByText('显示 41 到 80，共 100 个')).toBeInTheDocument()
    expect(screen.getByText('2 / 3')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /上一页/ }))
    expect(props.onPageChange).toHaveBeenCalledWith('images', 1)

    fireEvent.click(screen.getByRole('button', { name: /下一页/ }))
    expect(props.onPageChange).toHaveBeenCalledWith('images', 3)
  })

  it('确认删除单张图片时回调携带条目', async () => {
    const props = makeProps()
    render(<CacheImageListPanel {...props} />)

    fireEvent.click(screen.getByRole('button', { name: '删除' }))
    expect(await screen.findByText('确认删除这张图片？')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '确认删除' }))
    expect(props.onDelete).toHaveBeenCalledWith('images', makeItem())
  })

  it('确认全部删除时触发 onDeleteAll', async () => {
    const props = makeProps()
    render(<CacheImageListPanel {...props} />)

    fireEvent.click(screen.getByRole('button', { name: '全部删除' }))
    expect(await screen.findByText('确认删除全部图片缓存？')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '确认删除' }))
    expect(props.onDeleteAll).toHaveBeenCalledWith('images')
  })

  it('确认清理旧缓存时携带保留天数', async () => {
    const props = makeProps()
    render(<CacheImageListPanel {...props} />)

    fireEvent.click(screen.getByRole('button', { name: '删除最近 7 天以外' }))
    expect(await screen.findByText('确认清理旧图片缓存？')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '确认删除' }))
    expect(props.onDeleteOlderThanRecentDays).toHaveBeenCalledWith('images', 7)
  })

  it('cleanupDisabled 时删除入口全部禁用，删除中的条目显示加载态', () => {
    const props = makeProps()
    render(
      <CacheImageListPanel
        {...props}
        cleanupDisabled
        deletingKey="images:2026-01/cat.png"
        filters={{ startDate: '2026-01-01', endDate: '2026-01-31' }}
      />
    )

    expect(screen.getByRole('button', { name: '全部删除' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '删除区间' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '删除最近 1 天以外' })).toBeDisabled()
    const deleteItemButton = screen.getByRole('button', { name: '删除' })
    expect(deleteItemButton).toBeDisabled()
    expect(deleteItemButton.querySelector('.animate-spin')).toBeInTheDocument()
  })

  it('有日期筛选时可确认删除区间', async () => {
    const props = makeProps()
    render(
      <CacheImageListPanel
        {...props}
        filters={{ startDate: '2026-01-01', endDate: '' }}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: '删除区间' }))
    // 结束日期为空时提示文案回退为「最晚」
    expect(
      await screen.findByText(/这会删除 2026-01-01 到 最晚 之间的缓存文件/)
    ).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '确认删除' }))
    expect(props.onDeleteDateRange).toHaveBeenCalledWith('images')
  })
})
