/**
 * LocalCacheTab 设置页集成测试（特征化）。
 *
 * 1600+ 行巨型页面，按主渲染 + 关键交互链路 + 错误分支覆盖：
 * 目录卡片统计、图片浏览对话框（分页 / 日期筛选 / 单删 / 区间删 / 保留天数删）、
 * 日志文件夹浏览与清理、data 目录导航与删除、数据库统计 / VACUUM / 按表清理。
 * 仅 mock system-api、toast 与图片预览用的 backendApi，不 mock 被测组件本身。
 */
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { LocalCacheTab } from '../LocalCacheTab'
import { backendApi } from '@/lib/http'
import * as systemApi from '@/lib/system-api'

import type {
  DatabaseStorageStats,
  LocalCacheCleanupResult,
  LocalCacheDataEntriesResponse,
  LocalCacheDatabaseVacuumResult,
  LocalCacheImageListResponse,
  LocalCacheLogDirectoryListResponse,
  LocalCacheStats,
} from '@/lib/system-api'

// Radix Select 在 jsdom 里会读 pointer capture；setup 未补，用普通函数避免 restoreMocks 清空
if (!Element.prototype.hasPointerCapture) {
  Element.prototype.hasPointerCapture = () => false
}
if (!Element.prototype.setPointerCapture) {
  Element.prototype.setPointerCapture = () => {}
}
if (!Element.prototype.releasePointerCapture) {
  Element.prototype.releasePointerCapture = () => {}
}

const toastMock = vi.fn()

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: toastMock }) }))

// 图片预览组件直接用 backendApi.get 拉 Blob，替换为桩避免真实请求
vi.mock('@/lib/http', () => ({ backendApi: { get: vi.fn() } }))

vi.mock('@/lib/system-api', () => ({
  cleanupLocalCache: vi.fn(),
  deleteLocalCacheDataEntry: vi.fn(),
  deleteLocalCacheImage: vi.fn(),
  deleteLocalCacheImagesByDateRange: vi.fn(),
  deleteLocalCacheImagesOlderThanRecentDays: vi.fn(),
  deleteLocalCacheLogDirectory: vi.fn(),
  getLocalCacheDataEntries: vi.fn(),
  getLocalCacheDatabaseStats: vi.fn(),
  getLocalCacheImagePreviewUrl: vi.fn(),
  getLocalCacheImages: vi.fn(),
  getLocalCacheLogDirectories: vi.fn(),
  getLocalCacheStats: vi.fn(),
  vacuumLocalCacheDatabase: vi.fn(),
}))

// ---------- 测试数据工厂 ----------

function makeDatabaseStats(): DatabaseStorageStats {
  return {
    files: [{ path: 'data/MaiBot.db', exists: true, size: 10_485_760 }],
    tables: [
      {
        name: 'messages',
        rows: 1200,
        size: 8_388_608,
        size_source: 'dbstat',
        label: '消息记录',
        category: '日志',
        description: '聊天消息历史记录',
        cleanup_supported: true,
        cleanup_date_column: 'time',
      },
      {
        name: 'person_info',
        rows: 34,
        size: 65_536,
        size_source: 'estimated',
        label: '人物信息',
        category: '资源',
        description: '人物资料，不开放通用清理',
        cleanup_supported: false,
        cleanup_date_column: null,
      },
    ],
    total_size: 10_485_760,
    page_size: 4096,
    page_count: 2560,
    freelist_count: 128,
    free_size: 524_288,
  }
}

function makeStats(): LocalCacheStats {
  return {
    directories: [
      {
        key: 'images',
        label: '图片缓存目录',
        path: 'data/images',
        exists: true,
        file_count: 12,
        total_size: 2048,
        db_records: 10,
      },
      {
        key: 'emoji',
        label: '表情包缓存目录',
        path: 'data/emoji_registed',
        exists: true,
        file_count: 3,
        total_size: 1024,
        db_records: 3,
      },
      {
        key: 'logs',
        label: '日志目录',
        path: 'logs',
        exists: false,
        file_count: 0,
        total_size: 0,
        db_records: 0,
      },
    ],
    database: makeDatabaseStats(),
  }
}

function makeImageList(): LocalCacheImageListResponse {
  return {
    success: true,
    target: 'images',
    total: 41,
    page: 1,
    page_size: 40,
    total_size: 51_200,
    data: [
      {
        relative_path: '2026-05-01/a.png',
        file_name: 'a.png',
        full_path: '/app/data/images/2026-05-01/a.png',
        size: 2048,
        modified_time: 1_750_000_000,
        format: 'png',
        db_id: 7,
        image_hash: 'hash-a',
        description: '一张测试图片',
        is_registered: null,
        is_banned: false,
        no_file_flag: null,
      },
      {
        relative_path: '2026-05-02/b.jpg',
        file_name: 'b.jpg',
        full_path: '/app/data/images/2026-05-02/b.jpg',
        size: 1024,
        modified_time: 1_750_000_000,
        format: 'jpg',
        db_id: null,
        image_hash: null,
        description: '',
        is_registered: null,
        is_banned: true,
        no_file_flag: null,
      },
    ],
    date_groups: [
      { date: '2026-05-01', file_count: 20, total_size: 20_480 },
      { date: '2026-05-02', file_count: 21, total_size: 30_720 },
    ],
  }
}

function makeLogDirectories(): LocalCacheLogDirectoryListResponse {
  return {
    success: true,
    total: 2,
    data: [
      {
        relative_path: '',
        name: 'logs',
        full_path: '/app/logs',
        depth: 0,
        file_count: 3,
        total_size: 4096,
        modified_time: 1_750_000_000,
        root_files_only: true,
      },
      {
        relative_path: 'app',
        name: 'app',
        full_path: '/app/logs/app',
        depth: 1,
        file_count: 0,
        total_size: 0,
        modified_time: 0,
        root_files_only: false,
      },
    ],
  }
}

function makeDataRootEntries(): LocalCacheDataEntriesResponse {
  return {
    success: true,
    root_path: '/app/data',
    relative_path: '',
    current_path: '/app/data',
    parent_path: null,
    file_count: 5,
    total_size: 4096,
    total: 2,
    data: [
      {
        relative_path: 'images',
        name: 'images',
        full_path: '/app/data/images',
        kind: 'directory',
        file_count: 4,
        total_size: 3072,
        modified_time: 1_750_000_000,
        protected: false,
        protection_reason: null,
      },
      {
        relative_path: 'MaiBot.db',
        name: 'MaiBot.db',
        full_path: '/app/data/MaiBot.db',
        kind: 'file',
        file_count: 1,
        total_size: 1024,
        modified_time: 1_750_000_000,
        protected: true,
        protection_reason: '数据库主文件不允许删除',
      },
    ],
  }
}

function makeDataImagesEntries(): LocalCacheDataEntriesResponse {
  return {
    success: true,
    root_path: '/app/data',
    relative_path: 'images',
    current_path: '/app/data/images',
    parent_path: '',
    file_count: 1,
    total_size: 512,
    total: 1,
    data: [
      {
        relative_path: 'images/cache1.png',
        name: 'cache1.png',
        full_path: '/app/data/images/cache1.png',
        kind: 'file',
        file_count: 1,
        total_size: 512,
        modified_time: 1_750_000_000,
        protected: false,
        protection_reason: null,
      },
    ],
  }
}

function makeCleanupResult(
  overrides: Partial<LocalCacheCleanupResult> = {}
): LocalCacheCleanupResult {
  return {
    success: true,
    message: '清理完成',
    target: 'images',
    removed_files: 3,
    removed_bytes: 2048,
    removed_records: 2,
    vacuumed: false,
    database_size_before: null,
    database_size_after: null,
    reclaimed_bytes: 0,
    ...overrides,
  }
}

function makeVacuumResult(): LocalCacheDatabaseVacuumResult {
  return {
    success: true,
    message: 'VACUUM 完成',
    database_size_before: 10_485_760,
    database_size_after: 9_437_184,
    reclaimed_bytes: 1_048_576,
    checkpoint_busy: 0,
    checkpoint_log: 0,
    checkpointed: 0,
  }
}

beforeEach(() => {
  vi.mocked(systemApi.getLocalCacheStats).mockResolvedValue(makeStats())
  vi.mocked(systemApi.getLocalCacheDatabaseStats).mockResolvedValue(makeDatabaseStats())
  vi.mocked(systemApi.getLocalCacheDataEntries).mockImplementation(async (relativePath = '') =>
    relativePath === 'images' ? makeDataImagesEntries() : makeDataRootEntries()
  )
  vi.mocked(systemApi.getLocalCacheImages).mockResolvedValue(makeImageList())
  vi.mocked(systemApi.getLocalCacheLogDirectories).mockResolvedValue(makeLogDirectories())
  vi.mocked(systemApi.getLocalCacheImagePreviewUrl).mockImplementation(
    (target, relativePath) => `/preview/${target}/${relativePath}`
  )
  vi.mocked(systemApi.cleanupLocalCache).mockResolvedValue(makeCleanupResult())
  vi.mocked(systemApi.deleteLocalCacheImage).mockResolvedValue(makeCleanupResult())
  vi.mocked(systemApi.deleteLocalCacheImagesByDateRange).mockResolvedValue(makeCleanupResult())
  vi.mocked(systemApi.deleteLocalCacheImagesOlderThanRecentDays).mockResolvedValue(
    makeCleanupResult()
  )
  vi.mocked(systemApi.deleteLocalCacheLogDirectory).mockResolvedValue(
    makeCleanupResult({ target: 'log_files' })
  )
  vi.mocked(systemApi.deleteLocalCacheDataEntry).mockResolvedValue(
    makeCleanupResult({ target: 'data' })
  )
  vi.mocked(systemApi.vacuumLocalCacheDatabase).mockResolvedValue(makeVacuumResult())
  vi.mocked(backendApi.get).mockResolvedValue(new Blob(['fake-image']) as never)
})

// 渲染并等待目录统计加载完成
async function renderTab() {
  render(<LocalCacheTab />)
  await screen.findByText('图片缓存目录')
}

// 打开图片缓存浏览对话框并等待列表加载
async function openImageBrowser(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: '浏览图片' }))
  const dialog = await screen.findByRole('dialog')
  await within(dialog).findByText('a.png')
  return dialog
}

describe('LocalCacheTab 特征化', () => {
  it('初始加载：拉取三类统计并渲染目录卡片、数据库面板与 data 条目，过滤表情包目录', async () => {
    await renderTab()
    await screen.findByText('messages')
    // 名称与相对路径都会渲染 MaiBot.db，故用 AllBy 变体等待
    await screen.findAllByText('MaiBot.db')

    // 挂载后并行拉取目录统计、数据库统计与 data 根目录
    expect(systemApi.getLocalCacheStats).toHaveBeenCalledTimes(1)
    expect(systemApi.getLocalCacheDatabaseStats).toHaveBeenCalledTimes(1)
    expect(systemApi.getLocalCacheDataEntries).toHaveBeenCalledWith('')

    // 表情包目录不单独出卡片（与图片缓存共用浏览入口）
    expect(screen.getByText('日志目录')).toBeInTheDocument()
    expect(screen.queryByText('表情包缓存目录')).not.toBeInTheDocument()

    // 图片目录卡片：文件数 / 占用空间（格式化字节）/ 目录状态
    expect(screen.getByText('12')).toBeInTheDocument()
    expect(screen.getByText('2.0 KB')).toBeInTheDocument()
    expect(screen.getByText('存在')).toBeInTheDocument()
    expect(screen.getByText('未创建')).toBeInTheDocument()

    // 数据库面板：总体大小 / 可回收空闲页 / 总记录数与表行状态
    expect(screen.getByText('10 MB')).toBeInTheDocument()
    expect(screen.getByText('512 KB')).toBeInTheDocument()
    expect(screen.getByText('1234')).toBeInTheDocument()
    expect(screen.getByText('person_info')).toBeInTheDocument()
    expect(screen.getByText('可清理')).toBeInTheDocument()
    expect(screen.getByText('仅查看')).toBeInTheDocument()

    // data 面板：目录 / 文件徽标与受保护标记
    expect(screen.getByText('文件夹')).toBeInTheDocument()
    expect(screen.getByText('受保护')).toBeInTheDocument()
    expect(screen.getByText('数据库主文件不允许删除')).toBeInTheDocument()
  })

  it('初始加载失败：目录统计报错时弹出破坏性 toast', async () => {
    vi.mocked(systemApi.getLocalCacheStats).mockRejectedValue(new Error('后端连接失败'))
    render(<LocalCacheTab />)

    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '获取本地缓存失败',
        description: '后端连接失败',
        variant: 'destructive',
      })
    )
  })

  it('刷新按钮：重新拉取目录、数据库与 data 目录统计', async () => {
    const user = userEvent.setup()
    await renderTab()
    // 名称与相对路径都会渲染 MaiBot.db，故用 AllBy 变体等待
    await screen.findAllByText('MaiBot.db')

    const refreshButton = screen.getAllByRole('button', { name: '刷新' })[0]
    await waitFor(() => expect(refreshButton).toBeEnabled())
    await user.click(refreshButton)

    await waitFor(() => expect(systemApi.getLocalCacheStats).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(systemApi.getLocalCacheDatabaseStats).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(systemApi.getLocalCacheDataEntries).toHaveBeenCalledTimes(2))
    expect(systemApi.getLocalCacheDataEntries).toHaveBeenLastCalledWith('')
  })

  it('浏览图片：打开对话框并按默认参数拉取列表，渲染条目徽标与日期分组', async () => {
    const user = userEvent.setup()
    await renderTab()

    await user.click(screen.getByRole('button', { name: '浏览图片' }))
    const dialog = await screen.findByRole('dialog')

    expect(systemApi.getLocalCacheImages).toHaveBeenCalledWith({
      target: 'images',
      page: 1,
      page_size: 40,
      start_date: undefined,
      end_date: undefined,
    })

    await within(dialog).findByText('a.png')
    expect(within(dialog).getByText('PNG')).toBeInTheDocument()
    expect(within(dialog).getByText('数据库记录')).toBeInTheDocument()
    expect(within(dialog).getByText('仅文件')).toBeInTheDocument()
    expect(within(dialog).getByText('已禁用')).toBeInTheDocument()
    expect(within(dialog).getByText('一张测试图片')).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: /2026-05-01/ })).toBeInTheDocument()

    // 预览图通过 backendApi 以 Blob 方式拉取
    expect(backendApi.get).toHaveBeenCalledWith('/preview/images/2026-05-01/a.png', {
      parse: 'blob',
      errorMessage: '图片预览加载失败',
    })
  })

  it('图片列表空态：无数据时展示占位文案并禁用全部删除', async () => {
    vi.mocked(systemApi.getLocalCacheImages).mockResolvedValue({
      ...makeImageList(),
      total: 0,
      total_size: 0,
      data: [],
      date_groups: [],
    })
    const user = userEvent.setup()
    await renderTab()

    await user.click(screen.getByRole('button', { name: '浏览图片' }))
    const dialog = await screen.findByRole('dialog')

    await within(dialog).findByText('暂无图片缓存')
    expect(within(dialog).getByText('暂无可按日期浏览的缓存文件')).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: '全部删除' })).toBeDisabled()
  })

  it('图片分页：第一页禁用上一页，点击下一页请求第二页', async () => {
    const user = userEvent.setup()
    await renderTab()
    const dialog = await openImageBrowser(user)

    expect(within(dialog).getByRole('button', { name: '上一页' })).toBeDisabled()
    await user.click(within(dialog).getByRole('button', { name: '下一页' }))

    await waitFor(() =>
      expect(systemApi.getLocalCacheImages).toHaveBeenLastCalledWith(
        expect.objectContaining({ target: 'images', page: 2 })
      )
    )
  })

  it('日期筛选：提交起止日期后按条件拉取，清空后恢复无筛选', async () => {
    const user = userEvent.setup()
    await renderTab()
    const dialog = await openImageBrowser(user)

    // 未设置日期时区间删除与清空按钮不可用
    expect(within(dialog).getByRole('button', { name: '删除区间' })).toBeDisabled()
    expect(within(dialog).getByRole('button', { name: '清空日期' })).toBeDisabled()

    fireEvent.change(within(dialog).getByLabelText('开始日期'), {
      target: { value: '2026-05-01' },
    })
    fireEvent.change(within(dialog).getByLabelText('结束日期'), {
      target: { value: '2026-05-02' },
    })
    await user.click(within(dialog).getByRole('button', { name: '按日期浏览' }))

    await waitFor(() =>
      expect(systemApi.getLocalCacheImages).toHaveBeenLastCalledWith({
        target: 'images',
        page: 1,
        page_size: 40,
        start_date: '2026-05-01',
        end_date: '2026-05-02',
      })
    )

    await user.click(within(dialog).getByRole('button', { name: '清空日期' }))
    await waitFor(() =>
      expect(systemApi.getLocalCacheImages).toHaveBeenLastCalledWith({
        target: 'images',
        page: 1,
        page_size: 40,
        start_date: undefined,
        end_date: undefined,
      })
    )
  })

  it('日期分组：点击分组按钮后以该日期作为起止条件拉取', async () => {
    const user = userEvent.setup()
    await renderTab()
    const dialog = await openImageBrowser(user)

    await user.click(within(dialog).getByRole('button', { name: /2026-05-02/ }))

    await waitFor(() =>
      expect(systemApi.getLocalCacheImages).toHaveBeenLastCalledWith({
        target: 'images',
        page: 1,
        page_size: 40,
        start_date: '2026-05-02',
        end_date: '2026-05-02',
      })
    )
  })

  it('单图删除：确认后调用删除接口并 toast 清理结果', async () => {
    const user = userEvent.setup()
    await renderTab()
    const dialog = await openImageBrowser(user)

    await user.click(within(dialog).getAllByTitle('删除')[0])
    const alert = await screen.findByRole('alertdialog')
    expect(within(alert).getByText('确认删除这张图片？')).toBeInTheDocument()
    await user.click(within(alert).getByRole('button', { name: '确认删除' }))

    await waitFor(() =>
      expect(systemApi.deleteLocalCacheImage).toHaveBeenCalledWith('images', '2026-05-01/a.png')
    )
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '清理完成',
        description: '删除 3 个文件，释放 2.0 KB，移除 2 条记录。',
      })
    )
  })

  it('单图删除失败：弹出破坏性 toast 并展示错误信息', async () => {
    vi.mocked(systemApi.deleteLocalCacheImage).mockRejectedValue(new Error('文件被占用'))
    const user = userEvent.setup()
    await renderTab()
    const dialog = await openImageBrowser(user)

    await user.click(within(dialog).getAllByTitle('删除')[0])
    const alert = await screen.findByRole('alertdialog')
    await user.click(within(alert).getByRole('button', { name: '确认删除' }))

    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '删除图片失败',
        description: '文件被占用',
        variant: 'destructive',
      })
    )
  })

  it('目录卡片全部删除：确认后触发清理并提示无可清理内容', async () => {
    vi.mocked(systemApi.cleanupLocalCache).mockResolvedValue(
      makeCleanupResult({
        message: '图片缓存已是最新',
        removed_files: 0,
        removed_bytes: 0,
        removed_records: 0,
      })
    )
    const user = userEvent.setup()
    await renderTab()

    await user.click(screen.getByRole('button', { name: '全部删除' }))
    const alert = await screen.findByRole('alertdialog')
    expect(within(alert).getByText('确认全部删除图片缓存目录？')).toBeInTheDocument()
    await user.click(within(alert).getByRole('button', { name: '确认全部删除' }))

    await waitFor(() => expect(systemApi.cleanupLocalCache).toHaveBeenCalledWith('images'))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '图片缓存已是最新',
        description: '没有可清理的内容。',
      })
    )
  })

  it('删除区间：设置日期后确认调用区间删除接口', async () => {
    const user = userEvent.setup()
    await renderTab()
    const dialog = await openImageBrowser(user)

    fireEvent.change(within(dialog).getByLabelText('开始日期'), {
      target: { value: '2026-05-01' },
    })
    fireEvent.change(within(dialog).getByLabelText('结束日期'), {
      target: { value: '2026-05-02' },
    })

    await user.click(within(dialog).getByRole('button', { name: '删除区间' }))
    const alert = await screen.findByRole('alertdialog')
    expect(within(alert).getByText('确认删除当前日期区间内的图片缓存？')).toBeInTheDocument()
    await user.click(within(alert).getByRole('button', { name: '确认删除' }))

    await waitFor(() =>
      expect(systemApi.deleteLocalCacheImagesByDateRange).toHaveBeenCalledWith(
        'images',
        '2026-05-01',
        '2026-05-02'
      )
    )
  })

  it('保留天数清理：确认删除最近 7 天以外的图片缓存', async () => {
    const user = userEvent.setup()
    await renderTab()
    const dialog = await openImageBrowser(user)

    await user.click(within(dialog).getByRole('button', { name: '删除最近 7 天以外' }))
    const alert = await screen.findByRole('alertdialog')
    expect(within(alert).getByText('确认清理旧图片缓存？')).toBeInTheDocument()
    await user.click(within(alert).getByRole('button', { name: '确认删除' }))

    await waitFor(() =>
      expect(systemApi.deleteLocalCacheImagesOlderThanRecentDays).toHaveBeenCalledWith('images', 7)
    )
  })

  it('日志文件夹：浏览列表并清理根目录日志，空目录禁止清理', async () => {
    const user = userEvent.setup()
    await renderTab()

    await user.click(screen.getByRole('button', { name: '浏览文件夹' }))
    await screen.findByText('/app/logs')
    expect(systemApi.getLocalCacheLogDirectories).toHaveBeenCalledTimes(1)
    expect(screen.getByText('根目录')).toBeInTheDocument()
    expect(screen.getByText('空')).toBeInTheDocument()

    // 空目录行的清理按钮不可用
    const emptyRow = screen.getByText('/app/logs/app').closest('.rounded-md') as HTMLElement
    expect(within(emptyRow).getByRole('button', { name: '清理' })).toBeDisabled()

    // 根目录行确认清理后按相对路径调用接口
    const rootRow = screen.getByText('/app/logs').closest('.rounded-md') as HTMLElement
    await user.click(within(rootRow).getByRole('button', { name: '清理' }))
    const alert = await screen.findByRole('alertdialog')
    expect(within(alert).getByText('确认清理logs？')).toBeInTheDocument()
    await user.click(within(alert).getByRole('button', { name: '确认清理' }))

    await waitFor(() => expect(systemApi.deleteLocalCacheLogDirectory).toHaveBeenCalledWith(''))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '清理完成',
        description: '删除 3 个文件，释放 2.0 KB，移除 2 条记录。',
      })
    )
  })

  it('data 目录：进入子目录后可返回上级', async () => {
    const user = userEvent.setup()
    await renderTab()
    // 名称与相对路径都会渲染 MaiBot.db，故用 AllBy 变体等待
    await screen.findAllByText('MaiBot.db')

    // 根目录时上级按钮不可用
    expect(screen.getByRole('button', { name: '上级' })).toBeDisabled()

    await user.click(screen.getByRole('button', { name: '打开' }))
    await screen.findByText('cache1.png')
    expect(systemApi.getLocalCacheDataEntries).toHaveBeenNthCalledWith(2, 'images')

    await user.click(screen.getByRole('button', { name: '上级' }))
    await waitFor(() => expect(systemApi.getLocalCacheDataEntries).toHaveBeenNthCalledWith(3, ''))
  })

  it('data 目录：受保护条目禁止删除，普通目录确认后删除', async () => {
    const user = userEvent.setup()
    await renderTab()
    // 名称与相对路径都会渲染 MaiBot.db，故用 AllBy 变体等待
    await screen.findAllByText('MaiBot.db')

    const deleteButtons = screen.getAllByRole('button', { name: '删除' })
    expect(deleteButtons).toHaveLength(2)
    // MaiBot.db 受保护，删除按钮禁用
    expect(deleteButtons[1]).toBeDisabled()

    await user.click(deleteButtons[0])
    const alert = await screen.findByRole('alertdialog')
    expect(within(alert).getByText('确认删除 images？')).toBeInTheDocument()
    await user.click(within(alert).getByRole('button', { name: '确认删除' }))

    await waitFor(() => expect(systemApi.deleteLocalCacheDataEntry).toHaveBeenCalledWith('images'))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '清理完成',
        description: '删除 3 个文件，释放 2.0 KB，移除 2 条记录。',
      })
    )
  })

  it('数据库 VACUUM：确认执行并 toast 释放空间', async () => {
    const user = userEvent.setup()
    await renderTab()
    await screen.findByText('messages')

    await user.click(screen.getByRole('button', { name: 'VACUUM' }))
    const alert = await screen.findByRole('alertdialog')
    await user.click(within(alert).getByRole('button', { name: '确认执行' }))

    await waitFor(() => expect(systemApi.vacuumLocalCacheDatabase).toHaveBeenCalledTimes(1))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: 'VACUUM 完成',
        description: '释放 1.0 MB，当前数据库占用 9.0 MB。',
      })
    )
  })

  it('数据库 VACUUM 失败：弹出破坏性 toast', async () => {
    vi.mocked(systemApi.vacuumLocalCacheDatabase).mockRejectedValue(new Error('数据库被锁定'))
    const user = userEvent.setup()
    await renderTab()
    await screen.findByText('messages')

    await user.click(screen.getByRole('button', { name: 'VACUUM' }))
    const alert = await screen.findByRole('alertdialog')
    await user.click(within(alert).getByRole('button', { name: '确认执行' }))

    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '数据库 VACUUM 失败',
        description: '数据库被锁定',
        variant: 'destructive',
      })
    )
  })

  it('数据库清理：勾选可清理表并取消清理后 VACUUM，按参数提交', async () => {
    const user = userEvent.setup()
    await renderTab()
    await screen.findByText('messages')

    await user.click(screen.getByRole('button', { name: '清理记录' }))
    const alert = await screen.findByRole('alertdialog')

    // 未勾选任何表时禁止提交
    expect(within(alert).getByRole('button', { name: '确认清理' })).toBeDisabled()

    // 复选框依次为：清理后 VACUUM（默认勾选）与唯一可清理表 messages
    const checkboxes = within(alert).getAllByRole('checkbox')
    expect(checkboxes).toHaveLength(2)
    expect(checkboxes[0]).toBeChecked()

    await user.click(checkboxes[1])
    await user.click(checkboxes[0])
    await user.click(within(alert).getByRole('button', { name: '确认清理' }))

    await waitFor(() =>
      expect(systemApi.cleanupLocalCache).toHaveBeenCalledWith('database_logs', ['messages'], {
        database_mode: 'older_than_days',
        older_than_days: 90,
        vacuum_after_cleanup: false,
      })
    )
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '清理完成',
        description: '删除 3 个文件，释放 2.0 KB，移除 2 条记录。',
      })
    )
  })

  it('图片预览失败时展示 ImageOff 占位', async () => {
    vi.mocked(backendApi.get).mockRejectedValue(new Error('preview broken'))
    const user = userEvent.setup()
    await renderTab()
    const dialog = await openImageBrowser(user)

    await waitFor(() => {
      expect(dialog.querySelector('.lucide-image-off')).not.toBeNull()
    })
    expect(dialog.querySelector('img')).toBeNull()
  })

  it('关闭对话框时取消未完成的预览请求，成功回调不再挂载图片', async () => {
    let resolvePreview: ((blob: Blob) => void) | undefined
    vi.mocked(backendApi.get).mockImplementation(
      () =>
        new Promise((resolve) => {
          resolvePreview = resolve
        }) as never
    )
    const user = userEvent.setup()
    await renderTab()

    await user.click(screen.getByRole('button', { name: '浏览图片' }))
    const dialog = await screen.findByRole('dialog')
    expect(dialog.querySelector('img')).toBeNull()

    await user.click(within(dialog).getByRole('button', { name: '关闭' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())

    await act(async () => {
      resolvePreview?.(new Blob(['late']))
    })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('刷新图片列表：再次按当前页请求缓存', async () => {
    const user = userEvent.setup()
    await renderTab()
    const dialog = await openImageBrowser(user)
    expect(systemApi.getLocalCacheImages).toHaveBeenCalledTimes(1)

    await user.click(within(dialog).getByRole('button', { name: '刷新列表' }))
    await waitFor(() => expect(systemApi.getLocalCacheImages).toHaveBeenCalledTimes(2))
    expect(systemApi.getLocalCacheImages).toHaveBeenLastCalledWith({
      target: 'images',
      page: 1,
      page_size: 40,
      start_date: undefined,
      end_date: undefined,
    })
  })

  it('获取图片列表失败：弹出破坏性 toast', async () => {
    vi.mocked(systemApi.getLocalCacheImages).mockRejectedValue(new Error('图片接口挂了'))
    const user = userEvent.setup()
    await renderTab()

    await user.click(screen.getByRole('button', { name: '浏览图片' }))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '获取图片列表失败',
        description: '图片接口挂了',
        variant: 'destructive',
      })
    )
  })

  it('获取日志文件夹失败：弹出破坏性 toast', async () => {
    vi.mocked(systemApi.getLocalCacheLogDirectories).mockRejectedValue('boom')
    const user = userEvent.setup()
    await renderTab()

    await user.click(screen.getByRole('button', { name: '浏览文件夹' }))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '获取日志文件夹失败',
        description: '请稍后重试',
        variant: 'destructive',
      })
    )
  })

  it('获取数据库统计失败：弹出破坏性 toast', async () => {
    vi.mocked(systemApi.getLocalCacheDatabaseStats).mockRejectedValue(new Error('统计超时'))
    render(<LocalCacheTab />)

    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '获取数据库统计失败',
        description: '统计超时',
        variant: 'destructive',
      })
    )
  })

  it('获取 data 目录失败：弹出破坏性 toast', async () => {
    vi.mocked(systemApi.getLocalCacheDataEntries).mockRejectedValue(new Error('data 不可读'))
    render(<LocalCacheTab />)

    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '获取 data 目录失败',
        description: 'data 不可读',
        variant: 'destructive',
      })
    )
  })

  it('目录清理失败：弹出破坏性 toast', async () => {
    vi.mocked(systemApi.cleanupLocalCache).mockRejectedValue(new Error('磁盘只读'))
    const user = userEvent.setup()
    await renderTab()

    await user.click(screen.getByRole('button', { name: '全部删除' }))
    const alert = await screen.findByRole('alertdialog')
    await user.click(within(alert).getByRole('button', { name: '确认全部删除' }))

    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '清理失败',
        description: '磁盘只读',
        variant: 'destructive',
      })
    )
  })

  it('日志目录清理成功：展示 VACUUM 释放文案并刷新文件夹列表', async () => {
    vi.mocked(systemApi.cleanupLocalCache).mockResolvedValue(
      makeCleanupResult({
        target: 'log_files',
        vacuumed: true,
        reclaimed_bytes: 1024,
      })
    )
    const user = userEvent.setup()
    await renderTab()

    await user.click(screen.getByRole('button', { name: '浏览文件夹' }))
    await screen.findByText('/app/logs')
    expect(systemApi.getLocalCacheLogDirectories).toHaveBeenCalledTimes(1)

    const logCard = screen.getByText('日志目录').closest('.rounded-lg') as HTMLElement
    await user.click(within(logCard).getByRole('button', { name: '清理' }))
    const alert = await screen.findByRole('alertdialog')
    expect(within(alert).getByText('确认清理日志目录？')).toBeInTheDocument()
    await user.click(within(alert).getByRole('button', { name: '确认清理' }))

    await waitFor(() => expect(systemApi.cleanupLocalCache).toHaveBeenCalledWith('log_files'))
    await waitFor(() => expect(systemApi.getLocalCacheLogDirectories).toHaveBeenCalledTimes(2))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '清理完成',
        description: '删除 3 个文件，释放 2.0 KB，移除 2 条记录，VACUUM 释放 1.0 KB。',
      })
    )
  })

  it('数据库清理：修改保留天数后按新天数提交', async () => {
    const user = userEvent.setup({ pointerEventsCheck: 0 })
    await renderTab()
    await screen.findByText('messages')

    await user.click(screen.getByRole('button', { name: '清理记录' }))
    const alert = await screen.findByRole('alertdialog')
    const comboboxes = within(alert).getAllByRole('combobox')

    await user.click(comboboxes[1])
    await user.click(await screen.findByRole('option', { name: '30 天' }))

    await user.click(within(alert).getAllByRole('checkbox')[1])
    await user.click(within(alert).getByRole('button', { name: '确认清理' }))

    await waitFor(() =>
      expect(systemApi.cleanupLocalCache).toHaveBeenCalledWith('database_logs', ['messages'], {
        database_mode: 'older_than_days',
        older_than_days: 30,
        vacuum_after_cleanup: true,
      })
    )
  })

  it('数据库清理：切换为清空所选表后禁用保留天数并提交 all 模式', async () => {
    const user = userEvent.setup({ pointerEventsCheck: 0 })
    await renderTab()
    await screen.findByText('messages')

    await user.click(screen.getByRole('button', { name: '清理记录' }))
    const alert = await screen.findByRole('alertdialog')
    const comboboxes = within(alert).getAllByRole('combobox')

    await user.click(comboboxes[0])
    await user.click(await screen.findByRole('option', { name: '清空所选表' }))

    expect(comboboxes[1]).toBeDisabled()
    expect(within(alert).getByText(/预计删除/)).toBeInTheDocument()

    await user.click(within(alert).getAllByRole('checkbox')[1])
    await user.click(within(alert).getByRole('button', { name: '确认清理' }))

    await waitFor(() =>
      expect(systemApi.cleanupLocalCache).toHaveBeenCalledWith('database_logs', ['messages'], {
        database_mode: 'all',
        older_than_days: undefined,
        vacuum_after_cleanup: true,
      })
    )
  })
})
