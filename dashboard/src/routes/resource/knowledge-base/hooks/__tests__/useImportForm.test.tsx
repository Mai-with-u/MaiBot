import type { ReactNode } from 'react'

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useImportForm } from '../useImportForm'

const toastMock = vi.hoisted(() => vi.fn())

vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: toastMock }) }))

vi.mock('@/lib/memory-api', () => ({
  getMemoryImportSettings: vi.fn(),
  getMemoryImportPathAliases: vi.fn(),
  getMemoryImportChatTargets: vi.fn(),
  createMemoryUploadImport: vi.fn(),
  createMemoryPasteImport: vi.fn(),
  createMemoryRawScanImport: vi.fn(),
  createMemoryLpmmOpenieImport: vi.fn(),
  createMemoryLpmmConvertImport: vi.fn(),
  resolveMemoryImportPath: vi.fn(),
}))

import * as memoryApi from '@/lib/memory-api'

function makeWrapper() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
}

function renderForm(onCreated = vi.fn()) {
  return {
    onCreated,
    ...renderHook(() => useImportForm({ active: true, onCreated }), { wrapper: makeWrapper() }),
  }
}

beforeEach(() => {
  vi.mocked(memoryApi.getMemoryImportSettings).mockResolvedValue({ success: true, settings: {} } as never)
  vi.mocked(memoryApi.getMemoryImportPathAliases).mockResolvedValue({ success: true, path_aliases: {} } as never)
  vi.mocked(memoryApi.getMemoryImportChatTargets).mockResolvedValue({ success: true, data: [] } as never)
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('useImportForm', () => {
  describe('默认值 seed', () => {
    it('settings 到达后把公共参数 seed 为服务端默认值（用户未改时）', async () => {
      vi.mocked(memoryApi.getMemoryImportSettings).mockResolvedValue({
        success: true,
        settings: { default_file_concurrency: 8 },
      } as never)
      const { result } = renderForm()

      // 初始值为 '2'，settings 解析后被 seed 为 '8'
      expect(result.current.importCommonFileConcurrency).toBe('2')
      await waitFor(() => expect(result.current.importCommonFileConcurrency).toBe('8'))
    })

    it('用户已改过的字段不被服务端默认值覆盖', async () => {
      vi.mocked(memoryApi.getMemoryImportSettings).mockResolvedValue({
        success: true,
        settings: { default_file_concurrency: 8 },
      } as never)
      // settings 延迟解析，给用户先改值的时间窗口
      let resolveSettings: (v: unknown) => void = () => {}
      vi.mocked(memoryApi.getMemoryImportSettings).mockReturnValue(
        new Promise((resolve) => {
          resolveSettings = resolve
        }) as never,
      )
      const { result } = renderForm()

      act(() => result.current.setImportCommonFileConcurrency('99'))
      await act(async () => {
        resolveSettings({ success: true, settings: { default_file_concurrency: 8 } })
      })

      // 用户改成的 '99' 不被 seed 的 '8' 覆盖
      await waitFor(() => expect(memoryApi.getMemoryImportSettings).toHaveBeenCalled())
      expect(result.current.importCommonFileConcurrency).toBe('99')
    })
  })

  describe('submitImportByMode 按模式分派', () => {
    beforeEach(() => {
      vi.mocked(memoryApi.getMemoryImportSettings).mockResolvedValue({ success: true, settings: {} } as never)
    })

    it('文本模式调用 createMemoryPasteImport 并在成功后回调 onCreated', async () => {
      vi.mocked(memoryApi.createMemoryPasteImport).mockResolvedValue({
        success: true,
        task: { task_id: 'task-paste-1' },
      } as never)
      const { result, onCreated } = renderForm()

      act(() => {
        result.current.setUnifiedImportMode('text')
        result.current.setPasteContent('要导入的内容')
        result.current.setImportContentCategory('factual')
      })
      await act(async () => {
        await result.current.submitImportByMode()
      })

      expect(memoryApi.createMemoryPasteImport).toHaveBeenCalledOnce()
      expect(memoryApi.createMemoryUploadImport).not.toHaveBeenCalled()
      expect(onCreated).toHaveBeenCalledWith('task-paste-1')
    })

    it('文本内容为空时拦截，不调用任何 create 接口', async () => {
      const { result, onCreated } = renderForm()

      act(() => result.current.setUnifiedImportMode('text'))
      await act(async () => {
        await result.current.submitImportByMode()
      })

      expect(memoryApi.createMemoryPasteImport).not.toHaveBeenCalled()
      expect(onCreated).not.toHaveBeenCalled()
    })

    it('内容导入未选择资料类别时不创建任务', async () => {
      const { result, onCreated } = renderForm()

      act(() => {
        result.current.setUnifiedImportMode('text')
        result.current.setPasteContent('要导入的内容')
      })
      expect(result.current.importContentCategoryMissing).toBe(true)

      await act(async () => {
        await result.current.submitImportByMode()
      })

      expect(memoryApi.createMemoryPasteImport).not.toHaveBeenCalled()
      expect(onCreated).not.toHaveBeenCalled()
    })
  })

  describe('buildCommonImportPayload', () => {
    beforeEach(() => {
      vi.mocked(memoryApi.getMemoryImportSettings).mockResolvedValue({ success: true, settings: {} } as never)
    })

    it('产出当前公共参数载荷，供队列重试复用', async () => {
      const { result } = renderForm()

      act(() => result.current.setImportCommonLlmEnabled(true))
      const payload = result.current.buildCommonImportPayload()

      expect(payload).toMatchObject({ llm_enabled: true })
    })

    it('把四种页面类别映射为稳定的后端载荷', () => {
      const { result } = renderForm()
      const cases = [
        { category: 'narrative', strategy_override: 'narrative', chat_log: false },
        { category: 'factual', strategy_override: 'factual', chat_log: false },
        { category: 'quote', strategy_override: 'quote', chat_log: false },
        { category: 'chat_log', strategy_override: 'narrative', chat_log: true },
      ] as const

      for (const item of cases) {
        act(() => result.current.setImportContentCategory(item.category))
        expect(result.current.buildCommonImportPayload()).toMatchObject({
          strategy_override: item.strategy_override,
          chat_log: item.chat_log,
        })
      }
    })

    it('未选择类别时保留 auto 载荷供旧任务重试', () => {
      const { result } = renderForm()
      expect(result.current.buildCommonImportPayload()).toMatchObject({
        strategy_override: 'auto',
        chat_log: false,
      })
    })

    it('维护类任务不受资料类别选择阻塞', () => {
      const { result } = renderForm()
      act(() => result.current.setImportCreateMode('lpmm_convert'))
      expect(result.current.importContentCategoryMissing).toBe(false)
    })
  })

  describe('resolveImportPath', () => {
    beforeEach(() => {
      vi.mocked(memoryApi.getMemoryImportSettings).mockResolvedValue({ success: true, settings: {} } as never)
    })

    it('别名为空时不发解析请求', async () => {
      const { result } = renderForm()
      act(() => result.current.setPathResolveAlias('   '))
      await act(async () => {
        await result.current.resolveImportPath()
      })
      expect(memoryApi.resolveMemoryImportPath).not.toHaveBeenCalled()
    })

    it('成功时把解析结果格式化进输出框，空相对路径显示 (空)', async () => {
      vi.mocked(memoryApi.resolveMemoryImportPath).mockResolvedValue({
        alias: 'raw',
        relative_path: '',
        resolved_path: '/data/raw',
        exists: true,
        is_file: false,
        is_dir: true,
      } as never)
      const { result } = renderForm()

      act(() => {
        result.current.setPathResolveAlias('raw')
        result.current.setPathResolveRelativePath('')
        result.current.setPathResolveMustExist(false)
      })
      await act(async () => {
        await result.current.resolveImportPath()
      })

      expect(memoryApi.resolveMemoryImportPath).toHaveBeenCalledWith({
        alias: 'raw',
        relative_path: '',
        must_exist: false,
      })
      expect(result.current.pathResolveOutput).toBe(
        [
          '路径别名: raw',
          '相对路径: (空)',
          '解析结果: /data/raw',
          '是否存在: true',
          '是否文件: false',
          '是否目录: true',
        ].join('\n'),
      )
      expect(result.current.resolvingPath).toBe(false)
    })

    it('解析失败写入输出框；非 Error 使用兜底文案', async () => {
      vi.mocked(memoryApi.resolveMemoryImportPath).mockRejectedValueOnce(new Error('别名不存在'))
      const { result } = renderForm()

      await act(async () => {
        await result.current.resolveImportPath()
      })
      expect(result.current.pathResolveOutput).toBe('解析失败：别名不存在')

      vi.mocked(memoryApi.resolveMemoryImportPath).mockRejectedValueOnce('bad')
      await act(async () => {
        await result.current.resolveImportPath()
      })
      expect(result.current.pathResolveOutput).toBe('解析失败：路径解析失败')
    })
  })
})

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

function makeUploadFile(name = 'demo.txt') {
  return new File(['hello'], name, { type: 'text/plain' })
}

describe('useImportForm 模式切换校验', () => {
  it('切换文本/文件/文件夹/LPMM 时分别拦截或放行提交', async () => {
    vi.mocked(memoryApi.createMemoryRawScanImport).mockResolvedValue({
      success: true,
      task: { task_id: 'raw-1' },
    } as never)
    const { result, onCreated } = renderForm()

    act(() => result.current.setUnifiedImportMode('text'))
    expect(result.current.importContentCategoryMissing).toBe(true)
    await act(async () => {
      await result.current.submitImportByMode()
    })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '请选择资料类别',
        description: '新建内容导入任务前需要明确选择资料类别',
        variant: 'destructive',
      }),
    )
    expect(memoryApi.createMemoryPasteImport).not.toHaveBeenCalled()

    act(() => {
      result.current.setImportContentCategory('factual')
      result.current.setUnifiedImportMode('text')
    })
    expect(result.current.importContentCategoryMissing).toBe(false)
    await act(async () => {
      await result.current.submitImportByMode()
    })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({ title: '粘贴内容不能为空' }),
    )
    expect(memoryApi.createMemoryPasteImport).not.toHaveBeenCalled()

    act(() => result.current.setUnifiedImportMode('file'))
    await act(async () => {
      await result.current.submitImportByMode()
    })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '请选择上传文件',
        description: '至少选择一个 txt/md/json 文件后再提交',
        variant: 'destructive',
      }),
    )
    expect(memoryApi.createMemoryUploadImport).not.toHaveBeenCalled()

    act(() => result.current.setUnifiedImportMode('folder'))
    await act(async () => {
      await result.current.submitImportByMode()
    })
    expect(memoryApi.createMemoryRawScanImport).toHaveBeenCalledOnce()
    expect(onCreated).toHaveBeenCalledWith('raw-1')

    act(() => {
      result.current.setImportContentCategory('')
      result.current.setImportCreateMode('lpmm_openie')
    })
    expect(result.current.importContentCategoryMissing).toBe(true)
    await act(async () => {
      await result.current.submitImportByMode()
    })
    expect(memoryApi.createMemoryLpmmOpenieImport).not.toHaveBeenCalled()

    act(() => result.current.setImportCreateMode('lpmm_convert'))
    expect(result.current.importContentCategoryMissing).toBe(false)

    act(() => result.current.setImportCreateMode('upload'))
    expect(result.current.importContentCategoryMissing).toBe(true)

    act(() => {
      result.current.setImportContentCategory('quote')
      result.current.setImportCreateMode('paste')
    })
    await act(async () => {
      await result.current.submitImportByMode()
    })
    expect(memoryApi.createMemoryPasteImport).not.toHaveBeenCalled()
    expect(memoryApi.createMemoryLpmmConvertImport).not.toHaveBeenCalled()
  })

  it('文件/文件夹/OpenIE/转换成功后按当前模式分派创建接口', async () => {
    vi.mocked(memoryApi.createMemoryUploadImport).mockResolvedValue({
      success: true,
      task: { task_id: 'upload-task-1' },
    } as never)
    vi.mocked(memoryApi.createMemoryRawScanImport).mockResolvedValue({
      success: true,
      task: { task_id: 'raw-task-1' },
    } as never)
    vi.mocked(memoryApi.createMemoryLpmmOpenieImport).mockResolvedValue({
      success: true,
      task: { task_id: 'openie-task-1' },
    } as never)
    vi.mocked(memoryApi.createMemoryLpmmConvertImport).mockResolvedValue({
      success: true,
      task: { task_id: 'convert-task-1' },
    } as never)
    const { result, onCreated } = renderForm()
    const file = makeUploadFile()

    act(() => {
      result.current.setImportContentCategory('narrative')
      result.current.setUnifiedImportMode('file')
      result.current.setUploadFiles([file])
      result.current.setUploadInputMode('json')
    })
    await act(async () => {
      await result.current.submitImportByMode()
    })
    expect(memoryApi.createMemoryUploadImport).toHaveBeenCalledWith(
      [file],
      expect.objectContaining({
        input_mode: 'json',
        strategy_override: 'narrative',
        chat_log: false,
      }),
    )
    expect(result.current.uploadFiles).toEqual([])
    expect(onCreated).toHaveBeenCalledWith('upload-task-1')
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({ title: '上传导入任务已创建' }),
    )

    act(() => {
      result.current.setUnifiedImportMode('folder')
      result.current.setRawRelativePath('notes')
      result.current.setRawGlob('*.md')
      result.current.setRawRecursive(false)
      result.current.setRawInputMode('json')
    })
    await act(async () => {
      await result.current.submitImportByMode()
    })
    expect(memoryApi.createMemoryRawScanImport).toHaveBeenCalledWith(
      expect.objectContaining({
        alias: 'raw',
        relative_path: 'notes',
        glob: '*.md',
        recursive: false,
        input_mode: 'json',
      }),
    )

    act(() => {
      result.current.setImportCreateMode('lpmm_openie')
      result.current.setOpenieRelativePath('lpmm/in')
      result.current.setOpenieIncludeAllJson(true)
    })
    await act(async () => {
      await result.current.submitImportByMode()
    })
    expect(memoryApi.createMemoryLpmmOpenieImport).toHaveBeenCalledWith(
      expect.objectContaining({
        alias: 'lpmm',
        relative_path: 'lpmm/in',
        include_all_json: true,
      }),
    )

    act(() => {
      result.current.setImportCreateMode('lpmm_convert')
      result.current.setConvertRelativePath('src')
      result.current.setConvertTargetRelativePath('dst')
      result.current.setConvertDimension('8')
      result.current.setConvertBatchSize('16')
    })
    await act(async () => {
      await result.current.submitImportByMode()
    })
    expect(memoryApi.createMemoryLpmmConvertImport).toHaveBeenCalledWith({
      alias: 'lpmm',
      relative_path: 'src',
      target_alias: 'converted',
      target_relative_path: 'dst',
      dimension: 8,
      batch_size: 16,
    })
    expect(onCreated).toHaveBeenCalledWith('convert-task-1')
  })

  it('创建中再次提交会被忽略', async () => {
    const pending = deferred<{ success: boolean; task?: { task_id: string } }>()
    vi.mocked(memoryApi.createMemoryUploadImport).mockReturnValue(pending.promise as never)
    const { result } = renderForm()

    act(() => {
      result.current.setImportContentCategory('factual')
      result.current.setUnifiedImportMode('file')
      result.current.setUploadFiles([makeUploadFile()])
    })

    let firstSubmit: Promise<void> | undefined
    act(() => {
      firstSubmit = result.current.submitImportByMode()
    })
    await waitFor(() => expect(result.current.creatingImport).toBe(true))

    await act(async () => {
      await result.current.submitImportByMode()
    })
    expect(memoryApi.createMemoryUploadImport).toHaveBeenCalledOnce()

    await act(async () => {
      pending.resolve({ success: true, task: { task_id: 'up-1' } })
      await firstSubmit
    })
    expect(result.current.creatingImport).toBe(false)
  })
})

describe('useImportForm 确认失败', () => {
  it('文本模式 success=false 使用接口 error，缺省时走兜底文案', async () => {
    vi.mocked(memoryApi.createMemoryPasteImport).mockResolvedValueOnce({
      success: false,
      error: '队列已满',
    } as never)
    const { result, onCreated } = renderForm()

    act(() => {
      result.current.setUnifiedImportMode('text')
      result.current.setPasteContent('要导入的内容')
      result.current.setPasteName('草稿')
      result.current.setImportContentCategory('factual')
    })
    await act(async () => {
      await result.current.submitImportByMode()
    })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '创建粘贴导入任务失败',
        description: '队列已满',
        variant: 'destructive',
      }),
    )
    expect(onCreated).not.toHaveBeenCalled()
    expect(result.current.pasteContent).toBe('要导入的内容')

    toastMock.mockClear()
    vi.mocked(memoryApi.createMemoryPasteImport).mockResolvedValueOnce({ success: false } as never)
    await act(async () => {
      await result.current.submitImportByMode()
    })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '创建粘贴导入任务失败',
        description: '创建粘贴导入任务失败',
      }),
    )
  })

  it('各模式创建抛非 Error 时使用各自兜底文案', async () => {
    vi.mocked(memoryApi.createMemoryPasteImport).mockRejectedValue('bad')
    vi.mocked(memoryApi.createMemoryUploadImport).mockRejectedValue('bad')
    vi.mocked(memoryApi.createMemoryRawScanImport).mockRejectedValue('bad')
    vi.mocked(memoryApi.createMemoryLpmmOpenieImport).mockRejectedValue('bad')
    vi.mocked(memoryApi.createMemoryLpmmConvertImport).mockRejectedValue('bad')
    const { result } = renderForm()

    act(() => {
      result.current.setImportContentCategory('quote')
      result.current.setUnifiedImportMode('text')
      result.current.setPasteContent('内容')
    })
    await act(async () => {
      await result.current.submitImportByMode()
    })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '创建粘贴导入任务失败',
        description: '创建粘贴导入任务失败',
      }),
    )

    act(() => {
      result.current.setUnifiedImportMode('file')
      result.current.setUploadFiles([makeUploadFile()])
    })
    await act(async () => {
      await result.current.submitImportByMode()
    })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({ title: '创建上传导入任务失败' }),
    )

    act(() => result.current.setUnifiedImportMode('folder'))
    await act(async () => {
      await result.current.submitImportByMode()
    })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({ title: '创建本地扫描任务失败' }),
    )

    act(() => result.current.setImportCreateMode('lpmm_openie'))
    await act(async () => {
      await result.current.submitImportByMode()
    })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({ title: '创建 LPMM OpenIE 任务失败' }),
    )

    act(() => result.current.setImportCreateMode('lpmm_convert'))
    await act(async () => {
      await result.current.submitImportByMode()
    })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({ title: '创建 LPMM 转换任务失败' }),
    )
    expect(result.current.creatingImport).toBe(false)
  })

  it('文件/扫描/OpenIE/转换 success=false 使用默认失败文案', async () => {
    vi.mocked(memoryApi.createMemoryUploadImport).mockResolvedValue({ success: false } as never)
    vi.mocked(memoryApi.createMemoryRawScanImport).mockResolvedValue({ success: false } as never)
    vi.mocked(memoryApi.createMemoryLpmmOpenieImport).mockResolvedValue({ success: false } as never)
    vi.mocked(memoryApi.createMemoryLpmmConvertImport).mockResolvedValue({ success: false } as never)
    const { result, onCreated } = renderForm()

    act(() => {
      result.current.setImportContentCategory('chat_log')
      result.current.setUnifiedImportMode('file')
      result.current.setUploadFiles([makeUploadFile()])
    })
    await act(async () => {
      await result.current.submitImportByMode()
    })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '创建上传导入任务失败',
        description: '创建上传导入任务失败',
      }),
    )

    act(() => result.current.setUnifiedImportMode('folder'))
    await act(async () => {
      await result.current.submitImportByMode()
    })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({ title: '创建本地扫描任务失败' }),
    )

    act(() => result.current.setImportCreateMode('lpmm_openie'))
    await act(async () => {
      await result.current.submitImportByMode()
    })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({ title: '创建 LPMM OpenIE 任务失败' }),
    )

    act(() => result.current.setImportCreateMode('lpmm_convert'))
    await act(async () => {
      await result.current.submitImportByMode()
    })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({ title: '创建 LPMM 转换任务失败' }),
    )
    expect(onCreated).not.toHaveBeenCalled()
  })

  it('创建成功但没有 task_id 时仍回调空字符串，并使用队列兜底文案', async () => {
    vi.mocked(memoryApi.createMemoryPasteImport).mockResolvedValue({
      success: true,
      task: {},
    } as never)
    const { result, onCreated } = renderForm()

    act(() => {
      result.current.setUnifiedImportMode('text')
      result.current.setPasteContent('内容')
      result.current.setImportContentCategory('factual')
    })
    await act(async () => {
      await result.current.submitImportByMode()
    })

    expect(onCreated).toHaveBeenCalledWith('')
    expect(result.current.pasteContent).toBe('')
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '粘贴导入任务已创建',
        description: '导入任务已加入队列',
      }),
    )
  })

  it('onCreated 抛错时按创建失败处理', async () => {
    vi.mocked(memoryApi.createMemoryPasteImport).mockResolvedValue({
      success: true,
      task: { task_id: 'task-paste-2' },
    } as never)
    const { result, onCreated } = renderForm(
      vi.fn().mockRejectedValue(new Error('队列刷新失败')),
    )

    act(() => {
      result.current.setUnifiedImportMode('text')
      result.current.setPasteContent('内容')
      result.current.setImportContentCategory('factual')
    })
    await act(async () => {
      await result.current.submitImportByMode()
    })

    expect(onCreated).toHaveBeenCalledWith('task-paste-2')
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '创建粘贴导入任务失败',
        description: '队列刷新失败',
        variant: 'destructive',
      }),
    )
  })
})

describe('useImportForm 空 counts 与默认值', () => {
  it('空 settings / 空别名 / 空聊天流保持表单缺省，无效并发不写入载荷', async () => {
    const { result } = renderForm()

    await waitFor(() => expect(memoryApi.getMemoryImportSettings).toHaveBeenCalled())
    expect(result.current.importSettings).toEqual({})
    expect(result.current.importAliasKeys).toEqual([])
    expect(result.current.importChatTargets).toEqual([])

    act(() => {
      result.current.setImportCommonFileConcurrency('')
      result.current.setImportCommonChunkConcurrency('0')
      result.current.setImportCommonNarrativeWindowSize('abc')
      result.current.setImportCommonNarrativeOverlap('-1')
      result.current.setImportCommonFactualTargetSize('1.5')
      result.current.setImportCommonChatId('   ')
      result.current.setImportCommonChatReferenceTime('   ')
    })
    expect(result.current.buildCommonImportPayload()).toEqual({
      llm_enabled: true,
      strategy_override: 'auto',
      chat_log: false,
      scope_type: 'global',
      dedupe_policy: 'content_hash',
      force: false,
      clear_manifest: false,
    })
  })

  it('聊天范围与合法计数写入载荷；settings 可 seed 全部默认并发', async () => {
    vi.mocked(memoryApi.getMemoryImportSettings).mockResolvedValue({
      success: true,
      settings: {
        default_file_concurrency: 8,
        default_chunk_concurrency: 9,
        default_narrative_window_size: 2000,
        default_narrative_overlap: 100,
        default_factual_target_size: 1500,
      },
    } as never)
    vi.mocked(memoryApi.getMemoryImportChatTargets).mockResolvedValue({
      success: true,
      data: [{ chat_id: 'chat-1', chat_name: '测试群' }],
    } as never)
    const { result } = renderForm()

    await waitFor(() => expect(result.current.importCommonFileConcurrency).toBe('8'))
    expect(result.current.importCommonChunkConcurrency).toBe('9')
    expect(result.current.importCommonNarrativeWindowSize).toBe('2000')
    expect(result.current.importCommonNarrativeOverlap).toBe('100')
    expect(result.current.importCommonFactualTargetSize).toBe('1500')
    expect(result.current.importChatTargets).toEqual([
      { chat_id: 'chat-1', chat_name: '测试群' },
    ])

    act(() => {
      result.current.setImportContentCategory('chat_log')
      result.current.setImportCommonChatId('  chat-1  ')
      result.current.setImportCommonChatReferenceTime('  2024-01-01  ')
    })
    expect(result.current.buildCommonImportPayload()).toMatchObject({
      strategy_override: 'narrative',
      chat_log: true,
      scope_type: 'chat',
      chat_id: 'chat-1',
      chat_reference_time: '2024-01-01',
      file_concurrency: 8,
      chunk_concurrency: 9,
      narrative_window_size: 2000,
      narrative_overlap: 100,
      factual_target_size: 1500,
    })
  })

  it('路径别名到达后：非法当前值回退到排序后的第一项，合法值保持', async () => {
    let resolveAliases: (value: unknown) => void = () => {}
    vi.mocked(memoryApi.getMemoryImportPathAliases).mockReturnValue(
      new Promise((resolve) => {
        resolveAliases = resolve
      }) as never,
    )
    const { result, unmount } = renderForm()

    act(() => result.current.setPathResolveAlias('gone'))
    await act(async () => {
      resolveAliases({
        success: true,
        path_aliases: { zeta: '/z', alpha: '/a', raw: '/r' },
      })
    })
    await waitFor(() => expect(result.current.importAliasKeys).toEqual(['alpha', 'raw', 'zeta']))
    expect(result.current.pathResolveAlias).toBe('alpha')
    unmount()

    vi.mocked(memoryApi.getMemoryImportPathAliases).mockResolvedValue({
      success: true,
      path_aliases: { raw: '/r', lpmm: '/l' },
    } as never)
    const keepRaw = renderForm()
    await waitFor(() => expect(keepRaw.result.current.importAliasKeys).toEqual(['lpmm', 'raw']))
    expect(keepRaw.result.current.pathResolveAlias).toBe('raw')
  })

  it('用户已改过的其余默认字段不被后续 settings 覆盖', async () => {
    let resolveSettings: (value: unknown) => void = () => {}
    vi.mocked(memoryApi.getMemoryImportSettings).mockReturnValue(
      new Promise((resolve) => {
        resolveSettings = resolve
      }) as never,
    )
    const { result } = renderForm()

    act(() => {
      result.current.setImportCommonChunkConcurrency('11')
      result.current.setImportCommonNarrativeWindowSize('11')
      result.current.setImportCommonNarrativeOverlap('11')
      result.current.setImportCommonFactualTargetSize('11')
    })
    await act(async () => {
      resolveSettings({
        success: true,
        settings: {
          default_chunk_concurrency: 9,
          default_narrative_window_size: 2000,
          default_narrative_overlap: 100,
          default_factual_target_size: 1500,
        },
      })
    })

    await waitFor(() => expect(memoryApi.getMemoryImportSettings).toHaveBeenCalled())
    expect(result.current.importCommonChunkConcurrency).toBe('11')
    expect(result.current.importCommonNarrativeWindowSize).toBe('11')
    expect(result.current.importCommonNarrativeOverlap).toBe('11')
    expect(result.current.importCommonFactualTargetSize).toBe('11')
  })
})

