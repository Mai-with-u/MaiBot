/**
 * DataTransferPage 数据迁移页测试。
 *
 * mock data-transfer-api 与 LocalCacheTab，验证导出/导入选项、任务创建、
 * 1200ms 进度轮询、完成下载、失败 toast、取消与刷新失败。
 */
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { DataTransferPage } from '../data-transfer'
import * as dataTransferApi from '@/lib/data-transfer-api'

import type { DataTransferJob } from '@/lib/data-transfer-api'

const { toastMock } = vi.hoisted(() => ({ toastMock: vi.fn() }))

vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: toastMock }) }))

vi.mock('@/lib/data-transfer-api', () => ({
  cancelDataExportJob: vi.fn(),
  createDataExportJob: vi.fn(),
  createDataImportJob: vi.fn(),
  downloadDataExport: vi.fn(),
  getDataTransferJob: vi.fn(),
}))

vi.mock('@/routes/settings/LocalCacheTab', () => ({
  LocalCacheTab: () => <div data-testid="local-cache-tab-stub">本地缓存桩</div>,
}))

const createDataExportJob = vi.mocked(dataTransferApi.createDataExportJob)
const getDataTransferJob = vi.mocked(dataTransferApi.getDataTransferJob)
const downloadDataExport = vi.mocked(dataTransferApi.downloadDataExport)
const cancelDataExportJob = vi.mocked(dataTransferApi.cancelDataExportJob)
const createDataImportJob = vi.mocked(dataTransferApi.createDataImportJob)

/** 构造完整的数据迁移任务，便于按需覆盖字段 */
function makeJob(overrides: Partial<DataTransferJob> = {}): DataTransferJob {
  return {
    job_id: 'job-1',
    kind: 'export',
    status: 'pending',
    progress: 0,
    message: '等待导出',
    total_files: 0,
    processed_files: 0,
    total_bytes: 0,
    processed_bytes: 0,
    filename: null,
    download_url: null,
    manifest: null,
    error: null,
    ...overrides,
  }
}

function checkboxById(id: string): HTMLElement {
  const node = document.getElementById(id)
  if (!(node instanceof HTMLElement)) {
    throw new Error(`未找到复选框 #${id}`)
  }
  return node
}

function getImportFileInput(): HTMLInputElement {
  const input = document.querySelector('input[type="file"]')
  if (!(input instanceof HTMLInputElement)) {
    throw new Error('未找到导入文件选择框')
  }
  return input
}

function sectionByHeading(name: string): HTMLElement {
  const heading = screen.getByRole('heading', { name })
  const section = heading.closest('.space-y-4')
  if (!(section instanceof HTMLElement)) {
    throw new Error(`未找到「${name}」区块`)
  }
  return section
}

function createDeferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

/** 冲刷创建任务等微任务，使页面状态落到最新 job */
async function flushPromises() {
  await act(async () => {
    await Promise.resolve()
  })
}

/** 推进数据迁移页 1200ms 轮询间隔，并冲刷 refresh 产生的微任务 */
async function advancePollInterval() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1200)
  })
}

afterEach(() => {
  cleanup()
  vi.clearAllTimers()
  vi.useRealTimers()
})

describe('DataTransferPage 初始渲染与导出', () => {
  it('渲染标题、默认选项、导出导入分区和本地缓存桩', () => {
    render(<DataTransferPage />)

    expect(screen.getByText('MaiBot 数据导入导出')).toBeInTheDocument()
    expect(screen.getByText('config 与 data 默认包含，插件和日志可按需选择')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '导出数据' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '导入数据' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '开始导出' })).toBeEnabled()
    expect(screen.getByRole('button', { name: '开始导入' })).toBeEnabled()

    const coreCheckbox = checkboxById('export-core-data')
    expect(coreCheckbox).toBeChecked()
    expect(coreCheckbox).toBeDisabled()
    expect(checkboxById('export-plugins')).not.toBeChecked()
    expect(checkboxById('export-logs')).not.toBeChecked()
    expect(checkboxById('import-config')).toBeChecked()
    expect(checkboxById('import-data')).toBeChecked()
    expect(checkboxById('import-plugins')).not.toBeChecked()
    expect(checkboxById('import-logs')).not.toBeChecked()
    expect(screen.getByTestId('local-cache-tab-stub')).toBeInTheDocument()
  })

  it('勾选导出插件与日志后创建任务，并展示 pending 进度', async () => {
    const user = userEvent.setup()
    const pending = makeJob({
      message: '排队导出',
      processed_files: 0,
      total_files: 8,
      processed_bytes: 0,
      total_bytes: 512,
    })
    createDataExportJob.mockResolvedValue(pending)

    render(<DataTransferPage />)
    await user.click(checkboxById('export-plugins'))
    await user.click(checkboxById('export-logs'))
    await user.click(screen.getByRole('button', { name: '开始导出' }))

    await waitFor(() => {
      expect(createDataExportJob).toHaveBeenCalledWith({
        include_plugins: true,
        include_logs: true,
      })
    })
    expect(toastMock).toHaveBeenCalledWith({ title: '已开始导出 MaiBot 数据' })
    const exportSection = sectionByHeading('导出数据')
    expect(within(exportSection).getByText('排队导出')).toBeInTheDocument()
    expect(within(exportSection).getByText('pending')).toBeInTheDocument()
    expect(within(exportSection).getByText('0/8 个文件 · 0 B/512 B')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '开始导出' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '取消导出' })).toBeInTheDocument()
  })

  it('创建导出任务失败时弹出错误 toast', async () => {
    const user = userEvent.setup()
    createDataExportJob.mockRejectedValue(new Error('后端拒绝导出'))

    render(<DataTransferPage />)
    await user.click(screen.getByRole('button', { name: '开始导出' }))

    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '创建导出任务失败',
        description: '后端拒绝导出',
        variant: 'destructive',
      })
    })
    expect(screen.queryByRole('button', { name: '取消导出' })).not.toBeInTheDocument()
  })

  it('创建导出抛出非 Error 时使用默认失败文案', async () => {
    const user = userEvent.setup()
    createDataExportJob.mockRejectedValue('export-down')

    render(<DataTransferPage />)
    await user.click(screen.getByRole('button', { name: '开始导出' }))

    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '创建导出任务失败',
        description: '无法创建导出任务',
        variant: 'destructive',
      })
    })
  })

  it('创建即完成的导出可下载压缩包', async () => {
    const user = userEvent.setup()
    const completed = makeJob({
      status: 'completed',
      progress: 100,
      message: '导出完成',
      processed_files: 5,
      total_files: 5,
      processed_bytes: 1024,
      total_bytes: 10240,
      filename: 'maibot-backup.zip',
      download_url: '/api/webui/data-transfer/jobs/job-1/download',
    })
    createDataExportJob.mockResolvedValue(completed)
    downloadDataExport.mockResolvedValue(undefined)

    render(<DataTransferPage />)
    await user.click(screen.getByRole('button', { name: '开始导出' }))

    const exportSection = sectionByHeading('导出数据')
    expect(await within(exportSection).findByText('导出完成')).toBeInTheDocument()
    expect(within(exportSection).getByText('5/5 个文件 · 1.0 KB/10 KB')).toBeInTheDocument()
    expect(within(exportSection).getByText('completed')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '取消导出' })).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '下载压缩包' }))
    expect(downloadDataExport).toHaveBeenCalledWith(completed)
  })

  it('下载失败时弹出错误 toast', async () => {
    const user = userEvent.setup()
    createDataExportJob.mockResolvedValue(
      makeJob({
        status: 'completed',
        progress: 100,
        message: '导出完成',
        filename: 'maibot-backup.zip',
        download_url: '/download',
      })
    )
    downloadDataExport.mockRejectedValue(new Error('磁盘已满'))

    render(<DataTransferPage />)
    await user.click(screen.getByRole('button', { name: '开始导出' }))
    await user.click(await screen.findByRole('button', { name: '下载压缩包' }))

    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '下载失败',
        description: '磁盘已满',
        variant: 'destructive',
      })
    })
  })

  it('下载抛出非 Error 时使用默认失败文案', async () => {
    const user = userEvent.setup()
    createDataExportJob.mockResolvedValue(
      makeJob({
        status: 'completed',
        progress: 100,
        message: '导出完成',
        filename: 'maibot-backup.zip',
        download_url: '/download',
      })
    )
    downloadDataExport.mockRejectedValue('blob-missing')

    render(<DataTransferPage />)
    await user.click(screen.getByRole('button', { name: '开始导出' }))
    await user.click(await screen.findByRole('button', { name: '下载压缩包' }))

    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '下载失败',
        description: '无法下载导出文件',
        variant: 'destructive',
      })
    })
  })

  it('创建即失败的导出展示错误文案且不可取消或下载', async () => {
    const user = userEvent.setup()
    createDataExportJob.mockResolvedValue(
      makeJob({
        status: 'failed',
        progress: 12,
        message: '导出中断',
        error: '打包失败：权限不足',
        processed_bytes: Number.NaN,
        total_bytes: -1,
      })
    )

    render(<DataTransferPage />)
    await user.click(screen.getByRole('button', { name: '开始导出' }))

    const exportSection = sectionByHeading('导出数据')
    expect(await within(exportSection).findByText('导出中断')).toBeInTheDocument()
    expect(within(exportSection).getByText('failed')).toBeInTheDocument()
    expect(within(exportSection).getByText('打包失败：权限不足')).toBeInTheDocument()
    expect(within(exportSection).getByText('0/0 个文件 · 0 B/0 B')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '取消导出' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '下载压缩包' })).not.toBeInTheDocument()
  })

  it('运行中的导出可以取消', async () => {
    const user = userEvent.setup()
    createDataExportJob.mockResolvedValue(makeJob({ status: 'running', message: '正在打包' }))
    cancelDataExportJob.mockResolvedValue(
      makeJob({ status: 'cancelled', message: '已取消导出', progress: 30 })
    )

    render(<DataTransferPage />)
    await user.click(screen.getByRole('button', { name: '开始导出' }))
    await user.click(await screen.findByRole('button', { name: '取消导出' }))

    await waitFor(() => {
      expect(cancelDataExportJob).toHaveBeenCalledWith('job-1')
    })
    expect(toastMock).toHaveBeenCalledWith({ title: '正在取消导出' })
    expect(screen.getByText('已取消导出')).toBeInTheDocument()
    expect(screen.getByText('cancelled')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '取消导出' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '开始导出' })).toBeEnabled()
  })

  it('取消导出失败时弹出错误 toast', async () => {
    const user = userEvent.setup()
    createDataExportJob.mockResolvedValue(makeJob({ status: 'pending', message: '排队导出' }))
    cancelDataExportJob.mockRejectedValue(new Error('任务已进入不可取消阶段'))

    render(<DataTransferPage />)
    await user.click(screen.getByRole('button', { name: '开始导出' }))
    await user.click(await screen.findByRole('button', { name: '取消导出' }))

    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '取消导出失败',
        description: '任务已进入不可取消阶段',
        variant: 'destructive',
      })
    })
    expect(screen.getByText('排队导出')).toBeInTheDocument()
  })

  it('取消导出抛出非 Error 时使用默认失败文案', async () => {
    const user = userEvent.setup()
    createDataExportJob.mockResolvedValue(makeJob({ status: 'pending' }))
    cancelDataExportJob.mockRejectedValue('cancel-refused')

    render(<DataTransferPage />)
    await user.click(screen.getByRole('button', { name: '开始导出' }))
    await user.click(await screen.findByRole('button', { name: '取消导出' }))

    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '取消导出失败',
        description: '无法取消当前导出任务',
        variant: 'destructive',
      })
    })
  })
})

describe('DataTransferPage 任务轮询', () => {
  beforeEach(() => {
    // 只伪造 timeout，避免把 React 的微任务调度一起假掉
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  })

  it('pending 经 running 轮询到 completed 后可下载', async () => {
    const pending = makeJob({ message: '排队导出' })
    const running = makeJob({
      status: 'running',
      progress: 40,
      message: '正在打包',
      processed_files: 2,
      total_files: 5,
      processed_bytes: 2048,
      total_bytes: 1_099_511_627_776,
    })
    const completed = makeJob({
      status: 'completed',
      progress: 100,
      message: '导出完成',
      processed_files: 5,
      total_files: 5,
      processed_bytes: 10240,
      total_bytes: 10240,
      filename: 'maibot-backup.zip',
      download_url: '/download/job-1',
    })
    createDataExportJob.mockResolvedValue(pending)
    getDataTransferJob.mockResolvedValueOnce(running).mockResolvedValueOnce(completed)
    downloadDataExport.mockResolvedValue(undefined)

    render(<DataTransferPage />)
    fireEvent.click(screen.getByRole('button', { name: '开始导出' }))
    await flushPromises()

    expect(screen.getByText('排队导出')).toBeInTheDocument()
    expect(getDataTransferJob).not.toHaveBeenCalled()

    await advancePollInterval()
    expect(getDataTransferJob).toHaveBeenCalledWith('job-1')
    expect(screen.getByText('正在打包')).toBeInTheDocument()
    expect(screen.getByText('running')).toBeInTheDocument()
    expect(screen.getByText('2/5 个文件 · 2.0 KB/1.0 TB')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '取消导出' })).toBeInTheDocument()

    await advancePollInterval()
    expect(getDataTransferJob).toHaveBeenCalledTimes(2)
    expect(screen.getByText('导出完成')).toBeInTheDocument()
    expect(screen.getByText('completed')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '取消导出' })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '下载压缩包' }))
    await flushPromises()
    expect(downloadDataExport).toHaveBeenCalledWith(completed)
  })

  it('轮询到导出失败时弹出失败 toast 并展示错误', async () => {
    createDataExportJob.mockResolvedValue(makeJob({ status: 'running', message: '正在打包' }))
    getDataTransferJob.mockResolvedValue(
      makeJob({
        status: 'failed',
        progress: 55,
        message: '导出中断',
        error: '磁盘写入失败',
      })
    )

    render(<DataTransferPage />)
    fireEvent.click(screen.getByRole('button', { name: '开始导出' }))
    await flushPromises()
    await advancePollInterval()

    expect(toastMock).toHaveBeenCalledWith({
      title: '导出失败',
      description: '磁盘写入失败',
      variant: 'destructive',
    })
    expect(screen.getByText('磁盘写入失败')).toBeInTheDocument()
    expect(screen.getByText('failed')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '取消导出' })).not.toBeInTheDocument()
  })

  it('刷新任务进度失败时弹出错误 toast 并继续轮询', async () => {
    createDataExportJob.mockResolvedValue(makeJob({ status: 'pending', message: '排队导出' }))
    getDataTransferJob
      .mockRejectedValueOnce(new Error('网关超时'))
      .mockResolvedValueOnce(
        makeJob({
          status: 'failed',
          error: null,
          message: '上游中断',
        })
      )

    render(<DataTransferPage />)
    fireEvent.click(screen.getByRole('button', { name: '开始导出' }))
    await flushPromises()
    await advancePollInterval()

    expect(toastMock).toHaveBeenCalledWith({
      title: '任务进度刷新失败',
      description: '网关超时',
      variant: 'destructive',
    })
    expect(screen.getByText('排队导出')).toBeInTheDocument()

    await advancePollInterval()
    expect(getDataTransferJob).toHaveBeenCalledTimes(2)
    expect(toastMock).toHaveBeenCalledWith({
      title: '导出失败',
      description: '上游中断',
      variant: 'destructive',
    })
  })

  it('刷新进度抛出非 Error 时使用默认文案', async () => {
    createDataExportJob.mockResolvedValue(makeJob({ status: 'running' }))
    getDataTransferJob.mockRejectedValue('not-an-error')

    render(<DataTransferPage />)
    fireEvent.click(screen.getByRole('button', { name: '开始导出' }))
    await flushPromises()
    await advancePollInterval()

    expect(toastMock).toHaveBeenCalledWith({
      title: '任务进度刷新失败',
      description: '无法读取数据迁移任务状态',
      variant: 'destructive',
    })
  })

  it('卸载页面时停止轮询', async () => {
    createDataExportJob.mockResolvedValue(makeJob({ status: 'pending' }))
    getDataTransferJob.mockResolvedValue(makeJob({ status: 'running' }))

    const { unmount } = render(<DataTransferPage />)
    fireEvent.click(screen.getByRole('button', { name: '开始导出' }))
    await flushPromises()
    unmount()

    await advancePollInterval()
    expect(getDataTransferJob).not.toHaveBeenCalled()
  })

  it('导入任务轮询失败时弹出导入失败 toast', async () => {
    const file = new File(['zip-bytes'], 'backup.zip', { type: 'application/zip' })
    createDataImportJob.mockResolvedValue({ job_id: 'import-1', status: 'pending' })
    getDataTransferJob
      .mockResolvedValueOnce(
        makeJob({
          job_id: 'import-1',
          kind: 'import',
          status: 'running',
          message: '正在导入',
          processed_files: 1,
          total_files: 4,
          processed_bytes: 512,
          total_bytes: 2048,
        })
      )
      .mockResolvedValueOnce(
        makeJob({
          job_id: 'import-1',
          kind: 'import',
          status: 'failed',
          error: null,
          message: '空间不足',
        })
      )

    render(<DataTransferPage />)
    fireEvent.change(getImportFileInput(), { target: { files: [file] } })
    fireEvent.click(screen.getByRole('button', { name: '开始导入' }))
    await flushPromises()

    expect(screen.getByText('正在导入')).toBeInTheDocument()
    expect(toastMock).toHaveBeenCalledWith({ title: '已开始导入 MaiBot 数据' })

    await advancePollInterval()
    expect(toastMock).toHaveBeenCalledWith({
      title: '导入失败',
      description: '空间不足',
      variant: 'destructive',
    })
    expect(screen.getByText('空间不足')).toBeInTheDocument()
  })
})

describe('DataTransferPage 导入', () => {
  it('未选择文件时提示选择压缩包', async () => {
    const user = userEvent.setup()
    render(<DataTransferPage />)

    await user.click(screen.getByRole('button', { name: '开始导入' }))

    expect(toastMock).toHaveBeenCalledWith({
      title: '请选择要导入的压缩包',
      variant: 'destructive',
    })
    expect(createDataImportJob).not.toHaveBeenCalled()
  })

  it('未选择任何导入范围时提示至少选一个', async () => {
    const user = userEvent.setup()
    const file = new File(['zip-bytes'], 'backup.zip', { type: 'application/zip' })

    render(<DataTransferPage />)
    await user.upload(getImportFileInput(), file)
    await user.click(checkboxById('import-config'))
    await user.click(checkboxById('import-data'))
    await user.click(screen.getByRole('button', { name: '开始导入' }))

    expect(toastMock).toHaveBeenCalledWith({
      title: '请至少选择一个导入范围',
      variant: 'destructive',
    })
    expect(createDataImportJob).not.toHaveBeenCalled()
  })

  it('选择文件后展示上传进度，完成后进入任务状态', async () => {
    const user = userEvent.setup()
    const file = new File(['zip-bytes'], 'backup.zip', { type: 'application/zip' })
    const upload = createDeferred<{ job_id: string; status: 'pending' }>()
    createDataImportJob.mockImplementation(async (_file, _options, onProgress) => {
      onProgress?.(35)
      return upload.promise
    })
    getDataTransferJob.mockResolvedValue(
      makeJob({
        job_id: 'import-1',
        kind: 'import',
        status: 'running',
        message: '正在写入数据',
        progress: 25,
        processed_files: 1,
        total_files: 4,
        processed_bytes: 512,
        total_bytes: 2048,
      })
    )

    render(<DataTransferPage />)
    await user.upload(getImportFileInput(), file)
    await user.click(checkboxById('import-plugins'))
    await user.click(checkboxById('import-logs'))
    await user.click(screen.getByRole('button', { name: '开始导入' }))

    expect(await screen.findByText('正在上传数据包')).toBeInTheDocument()
    expect(screen.getByText('35%')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '开始导入' })).toBeDisabled()
    expect(createDataImportJob).toHaveBeenCalledWith(
      file,
      {
        import_config: true,
        import_data: true,
        import_plugins: true,
        import_logs: true,
      },
      expect.any(Function)
    )

    await act(async () => {
      upload.resolve({ job_id: 'import-1', status: 'pending' })
    })

    expect(await screen.findByText('正在写入数据')).toBeInTheDocument()
    expect(getDataTransferJob).toHaveBeenCalledWith('import-1')
    expect(screen.getByText('1/4 个文件 · 512 B/2.0 KB')).toBeInTheDocument()
    expect(toastMock).toHaveBeenCalledWith({ title: '已开始导入 MaiBot 数据' })
  })

  it('创建导入任务失败时弹出错误 toast', async () => {
    const user = userEvent.setup()
    const file = new File(['zip-bytes'], 'backup.zip', { type: 'application/zip' })
    createDataImportJob.mockRejectedValue(new Error('导入包格式不正确'))

    render(<DataTransferPage />)
    await user.upload(getImportFileInput(), file)
    await user.click(screen.getByRole('button', { name: '开始导入' }))

    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '创建导入任务失败',
        description: '导入包格式不正确',
        variant: 'destructive',
      })
    })
    expect(screen.queryByText('正在上传数据包')).not.toBeInTheDocument()
  })

  it('创建导入抛出非 Error 时使用默认失败文案', async () => {
    const user = userEvent.setup()
    const file = new File(['zip-bytes'], 'backup.zip', { type: 'application/zip' })
    createDataImportJob.mockRejectedValue('upload-down')

    render(<DataTransferPage />)
    await user.upload(getImportFileInput(), file)
    await user.click(screen.getByRole('button', { name: '开始导入' }))

    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '创建导入任务失败',
        description: '无法上传或导入数据包',
        variant: 'destructive',
      })
    })
  })

  it('导入任务自身带 error 时直接展示错误文案', async () => {
    const user = userEvent.setup()
    const file = new File(['zip-bytes'], 'backup.zip', { type: 'application/zip' })
    createDataImportJob.mockResolvedValue({ job_id: 'import-9', status: 'failed' })
    getDataTransferJob.mockResolvedValue(
      makeJob({
        job_id: 'import-9',
        kind: 'import',
        status: 'failed',
        message: '导入中断',
        error: '清单不匹配',
        processed_files: 0,
        total_files: 3,
      })
    )

    render(<DataTransferPage />)
    await user.upload(getImportFileInput(), file)
    await user.click(screen.getByRole('button', { name: '开始导入' }))

    expect(await screen.findByText('导入中断')).toBeInTheDocument()
    expect(screen.getByText('清单不匹配')).toBeInTheDocument()
    expect(screen.getByText('failed')).toBeInTheDocument()
  })
})
