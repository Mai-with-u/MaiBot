import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { resolveApiPath } from '@/lib/api-base'
import { ApiError, backendApi } from '@/lib/http'

import {
  cancelDataExportJob,
  createDataExportJob,
  createDataImportJob,
  downloadDataExport,
  getDataTransferJob,
  type DataTransferJob,
} from '../data-transfer-api'

vi.mock('@/lib/http', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/http')>()
  return {
    ...actual,
    backendApi: {
      request: vi.fn(),
      get: vi.fn(),
      post: vi.fn(),
      put: vi.fn(),
      patch: vi.fn(),
      delete: vi.fn(),
    },
  }
})

vi.mock('@/lib/api-base', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api-base')>()
  return {
    ...actual,
    resolveApiPath: vi.fn(),
  }
})

const getMock = vi.mocked(backendApi.get)
const postMock = vi.mocked(backendApi.post)
const resolveApiPathMock = vi.mocked(resolveApiPath)

/** 构造一个完整的导出任务对象，便于按需覆盖字段 */
function makeJob(overrides: Partial<DataTransferJob> = {}): DataTransferJob {
  return {
    job_id: 'job-1',
    kind: 'export',
    status: 'completed',
    progress: 100,
    message: '导出完成',
    total_files: 3,
    processed_files: 3,
    total_bytes: 1024,
    processed_bytes: 1024,
    filename: 'maibot-backup.zip',
    download_url: '/api/webui/data-transfer/jobs/job-1/download',
    manifest: null,
    error: null,
    ...overrides,
  }
}

beforeEach(() => {
  getMock.mockReset()
  postMock.mockReset()
  resolveApiPathMock.mockReset()
})

describe('createDataExportJob', () => {
  it('把导出选项作为请求体提交到导出接口并返回任务', async () => {
    const job = makeJob({ status: 'pending', progress: 0 })
    postMock.mockResolvedValue(job)

    await expect(
      createDataExportJob({ include_plugins: true, include_logs: false })
    ).resolves.toBe(job)
    expect(postMock).toHaveBeenCalledWith('/api/webui/data-transfer/export', {
      body: { include_plugins: true, include_logs: false },
      errorMessage: '创建导出任务失败',
    })
  })

  it('后端失败时向上抛出 ApiError', async () => {
    postMock.mockRejectedValue(new ApiError('创建导出任务失败', { status: 500 }))

    await expect(
      createDataExportJob({ include_plugins: false, include_logs: false })
    ).rejects.toMatchObject({ status: 500 })
  })
})

describe('getDataTransferJob', () => {
  it('对任务 ID 做 URL 编码并以 no-store 读取进度', async () => {
    const job = makeJob({ job_id: 'job/特殊' })
    getMock.mockResolvedValue(job)

    await expect(getDataTransferJob('job/特殊')).resolves.toBe(job)
    expect(getMock).toHaveBeenCalledWith(
      `/api/webui/data-transfer/jobs/${encodeURIComponent('job/特殊')}`,
      {
        cache: 'no-store',
        errorMessage: '获取数据迁移任务进度失败',
      }
    )
  })

  it('任务不存在时向上抛出 ApiError', async () => {
    getMock.mockRejectedValue(new ApiError('获取数据迁移任务进度失败', { status: 404 }))

    await expect(getDataTransferJob('missing')).rejects.toBeInstanceOf(ApiError)
  })
})

describe('cancelDataExportJob', () => {
  it('对任务 ID 做 URL 编码并调用取消接口', async () => {
    const job = makeJob({ status: 'cancelled' })
    postMock.mockResolvedValue(job)

    await expect(cancelDataExportJob('job 1')).resolves.toBe(job)
    expect(postMock).toHaveBeenCalledWith(
      `/api/webui/data-transfer/export/${encodeURIComponent('job 1')}/cancel`,
      {
        errorMessage: '取消导出任务失败',
      }
    )
  })
})

describe('downloadDataExport', () => {
  it('没有 download_url 时直接报错且不发起请求', async () => {
    await expect(downloadDataExport(makeJob({ download_url: null }))).rejects.toThrow(
      '导出任务还没有可下载文件'
    )
    expect(getMock).not.toHaveBeenCalled()
  })

  it('以 blob 模式下载文件并通过临时链接触发保存', async () => {
    const blob = new Blob(['zip-bytes'])
    getMock.mockResolvedValue(blob)
    const createObjectUrlSpy = vi
      .spyOn(URL, 'createObjectURL')
      .mockReturnValue('blob:maibot-test/export')
    const revokeObjectUrlSpy = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
    // 捕获被点击的临时 <a> 元素，验证下载属性（mock.contexts 记录每次调用的 this）
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

    const job = makeJob()
    await downloadDataExport(job)

    expect(getMock).toHaveBeenCalledWith(job.download_url, {
      parse: 'blob',
      cache: 'no-store',
      errorMessage: '下载导出文件失败',
    })
    expect(createObjectUrlSpy).toHaveBeenCalledWith(blob)
    expect(clickSpy).toHaveBeenCalledTimes(1)
    const clickedLink = clickSpy.mock.contexts[0] as HTMLAnchorElement
    expect(clickedLink.getAttribute('href')).toBe('blob:maibot-test/export')
    expect(clickedLink.download).toBe('maibot-backup.zip')
    // 下载完成后临时链接被移除、对象 URL 被回收
    expect(document.body.contains(clickedLink)).toBe(false)
    expect(revokeObjectUrlSpy).toHaveBeenCalledWith('blob:maibot-test/export')
  })

  it('任务未提供文件名时使用默认文件名 maibot-data.zip', async () => {
    getMock.mockResolvedValue(new Blob(['zip-bytes']))
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:maibot-test/fallback')
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

    await downloadDataExport(makeJob({ filename: null }))

    const clickedLink = clickSpy.mock.contexts[0] as HTMLAnchorElement
    expect(clickedLink.download).toBe('maibot-data.zip')
  })
})

describe('createDataImportJob', () => {
  /** 进度回调事件的最小形状 */
  type ProgressHandler = (event: ProgressEvent) => void

  /** 可控的 XMLHttpRequest 桩：记录请求参数并允许测试手动触发回调 */
  class FakeXMLHttpRequest {
    static instances: FakeXMLHttpRequest[] = []
    method = ''
    url = ''
    withCredentials = false
    status = 0
    statusText = ''
    responseText = ''
    sentBody: FormData | null = null
    upload: { onprogress: ProgressHandler | null } = { onprogress: null }
    onload: (() => void) | null = null
    onerror: (() => void) | null = null

    open(method: string, url: string): void {
      this.method = method
      this.url = url
    }

    send(body: FormData): void {
      this.sentBody = body
      FakeXMLHttpRequest.instances.push(this)
    }
  }

  const importOptions = {
    import_config: true,
    import_data: false,
    import_plugins: true,
    import_logs: false,
  }

  /** 发起导入并等待 XHR 桩收到请求 */
  async function startImport(onUploadProgress?: (progress: number) => void) {
    const file = new File(['zip-bytes'], 'backup.zip', { type: 'application/zip' })
    const promise = createDataImportJob(file, importOptions, onUploadProgress)
    // resolveApiPath 是异步的，send 发生在微任务之后
    await vi.waitFor(() => {
      expect(FakeXMLHttpRequest.instances).toHaveLength(1)
    })
    return { file, promise, request: FakeXMLHttpRequest.instances[0] }
  }

  beforeEach(() => {
    FakeXMLHttpRequest.instances = []
    resolveApiPathMock.mockImplementation(async (path: string) => path)
    vi.stubGlobal('XMLHttpRequest', FakeXMLHttpRequest)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('以 multipart 表单上传文件与导入选项并解析成功响应', async () => {
    const { file, promise, request } = await startImport()

    expect(resolveApiPathMock).toHaveBeenCalledWith('/api/webui/data-transfer/import')
    expect(request.method).toBe('POST')
    expect(request.url).toBe('/api/webui/data-transfer/import')
    expect(request.withCredentials).toBe(true)
    expect(request.sentBody).toBeInstanceOf(FormData)
    expect(request.sentBody!.get('file')).toBe(file)
    expect(request.sentBody!.get('import_config')).toBe('true')
    expect(request.sentBody!.get('import_data')).toBe('false')
    expect(request.sentBody!.get('import_plugins')).toBe('true')
    expect(request.sentBody!.get('import_logs')).toBe('false')

    request.status = 200
    request.responseText = JSON.stringify({ job_id: 'import-1', status: 'pending' })
    request.onload!()

    await expect(promise).resolves.toEqual({ job_id: 'import-1', status: 'pending' })
  })

  it('上传进度可计算时按百分比回调，不可计算时不回调', async () => {
    const onUploadProgress = vi.fn()
    const { promise, request } = await startImport(onUploadProgress)

    request.upload.onprogress!({ lengthComputable: true, loaded: 30, total: 120 } as ProgressEvent)
    expect(onUploadProgress).toHaveBeenCalledWith(25)

    request.upload.onprogress!({ lengthComputable: false, loaded: 0, total: 0 } as ProgressEvent)
    expect(onUploadProgress).toHaveBeenCalledTimes(1)

    request.status = 200
    request.responseText = JSON.stringify({ job_id: 'import-2', status: 'running' })
    request.onload!()
    await expect(promise).resolves.toEqual({ job_id: 'import-2', status: 'running' })
  })

  it('后端返回错误体时用其中的 detail 文案拒绝', async () => {
    const { promise, request } = await startImport()

    request.status = 400
    request.responseText = JSON.stringify({ detail: '导入包格式不正确' })
    request.onload!()

    await expect(promise).rejects.toThrow('导入包格式不正确')
  })

  it('错误响应不是 JSON 时回退到 statusText 文案', async () => {
    const { promise, request } = await startImport()

    request.status = 502
    request.statusText = 'Bad Gateway'
    request.responseText = '<html>gateway error</html>'
    request.onload!()

    await expect(promise).rejects.toThrow('Bad Gateway')
  })

  it('错误响应既无 detail 也无 statusText 时使用默认文案', async () => {
    const { promise, request } = await startImport()

    request.status = 500
    request.statusText = ''
    request.responseText = ''
    request.onload!()

    await expect(promise).rejects.toThrow('上传导入包失败')
  })

  it('网络层失败时以固定文案拒绝', async () => {
    const { promise, request } = await startImport()

    request.onerror!()

    await expect(promise).rejects.toThrow('网络请求失败：无法上传导入包')
  })
})
