import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { PromptManagementPage } from '../prompts'
import * as promptApi from '@/lib/prompt-api'

import type {
  PromptCatalog,
  PromptFileContent,
  PromptFileInfo,
  PromptVersionInfo,
} from '@/lib/prompt-api'

const toastMock = vi.fn()

vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: toastMock }) }))
vi.mock('@/components/CodeEditor', () => ({
  CodeEditor: ({
    value,
    onChange,
    readOnly,
  }: {
    value: string
    onChange?: (value: string) => void
    readOnly?: boolean
  }) => (
    <textarea
      aria-label={readOnly ? '只读编辑器' : '可编辑编辑器'}
      data-testid={readOnly ? 'readonly-code-editor' : 'editable-code-editor'}
      readOnly={readOnly}
      value={value}
      onChange={(event) => onChange?.(event.target.value)}
    />
  ),
}))
vi.mock('@/lib/prompt-api', () => ({
  activatePromptVersion: vi.fn(),
  getDefaultPromptFile: vi.fn(),
  getPromptCatalog: vi.fn(),
  getPromptFile: vi.fn(),
  getPromptVersionFile: vi.fn(),
  resetPromptFile: vi.fn(),
  updatePromptFile: vi.fn(),
}))

const VERSION_STORAGE_PREFIX = 'maibot.promptManagement.selectedVersion'

function makeFileInfo(overrides: Partial<PromptFileInfo> = {}): PromptFileInfo {
  return {
    name: 'first.prompt',
    size: 13,
    modified_at: 1,
    display_name: '第一 Prompt',
    advanced: false,
    description: '',
    customized: true,
    custom_version_count: 0,
    ...overrides,
  }
}

function makeVersion(overrides: Partial<PromptVersionInfo> = {}): PromptVersionInfo {
  return {
    id: 'v1',
    label: '版本 1',
    created_at: 1,
    modified_at: 1,
    size: 10,
    active: false,
    ...overrides,
  }
}

function makePromptContent(
  filename: string,
  content: string,
  overrides: Partial<PromptFileContent> = {}
): PromptFileContent {
  return {
    success: true,
    language: 'zh-CN',
    filename,
    content,
    customized: true,
    active_version_id: null,
    versions: [],
    validation: {
      valid: true,
      missing_placeholders: [],
      extra_placeholders: [],
      message: '',
    },
    ...overrides,
  }
}

function makeCatalog(
  files: PromptFileInfo[] = [makeFileInfo(), makeFileInfo({
    name: 'second.prompt',
    size: 14,
    modified_at: 2,
    display_name: '第二 Prompt',
  })],
  overrides: Partial<PromptCatalog> = {}
): PromptCatalog {
  return {
    success: true,
    languages: ['zh-CN'],
    files: { 'zh-CN': files },
    ...overrides,
  }
}

const catalog = makeCatalog()

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

async function renderReady(expectedDisplay: string | RegExp = 'first.prompt current') {
  const user = userEvent.setup()
  const view = render(<PromptManagementPage />)
  await screen.findByDisplayValue(expectedDisplay)
  return { user, ...view }
}

function getButtonByText(text: string | RegExp) {
  const match = (value: string) =>
    typeof text === 'string' ? value.includes(text) : text.test(value)
  const found = screen.getAllByRole('button').find((element) => match(element.textContent ?? ''))
  if (!found) {
    throw new Error(`未找到按钮「${String(text)}」`)
  }
  return found
}

async function chooseComboboxOption(
  user: ReturnType<typeof userEvent.setup>,
  index: number,
  next: string | RegExp
) {
  const trigger = screen.getAllByRole('combobox')[index]
  await waitFor(() => expect(trigger).toBeEnabled())
  await user.click(trigger)
  await user.click(await screen.findByRole('option', { name: next }))
}

function setEditorValue(value: string) {
  fireEvent.change(screen.getByTestId('editable-code-editor'), { target: { value } })
}

beforeEach(() => {
  // Radix Select 在 jsdom 里会读 pointer capture，未实现时 pointerdown 直接抛错
  if (!Element.prototype.hasPointerCapture) {
    Element.prototype.hasPointerCapture = () => false
  }
  if (!Element.prototype.setPointerCapture) {
    Element.prototype.setPointerCapture = () => {}
  }
  if (!Element.prototype.releasePointerCapture) {
    Element.prototype.releasePointerCapture = () => {}
  }

  vi.mocked(promptApi.getPromptCatalog).mockResolvedValue(catalog)
  vi.mocked(promptApi.getPromptFile).mockImplementation((_, filename) =>
    Promise.resolve(makePromptContent(filename, `${filename} current`))
  )
  vi.mocked(promptApi.getDefaultPromptFile).mockImplementation((_, filename) =>
    Promise.resolve(makePromptContent(filename, `${filename} default`))
  )
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  window.localStorage.clear()
})

describe('PromptManagementPage', () => {
  it('对比默认模式下切换 Prompt 文件会刷新默认版本面板', async () => {
    const user = userEvent.setup()

    render(<PromptManagementPage />)

    await screen.findByDisplayValue('first.prompt current')
    await user.click(screen.getByRole('button', { name: /对比默认/ }))

    await screen.findByDisplayValue('first.prompt default')
    await user.click(screen.getByRole('button', { name: /第二 Prompt/ }))

    await screen.findByDisplayValue('second.prompt current')
    await waitFor(() =>
      expect(screen.getByTestId('readonly-code-editor')).toHaveValue('second.prompt default')
    )
  })

  it('可按文件名、展示名和描述搜索，空白查询会还原列表', async () => {
    vi.mocked(promptApi.getPromptCatalog).mockResolvedValue(
      makeCatalog([
        makeFileInfo({ description: '规划器开场白' }),
        makeFileInfo({
          name: 'second.prompt',
          size: 14,
          modified_at: 2,
          display_name: '第二 Prompt',
          description: '回复风格模板',
        }),
        makeFileInfo({
          name: 'hidden.prompt',
          display_name: '隐藏高级',
          advanced: true,
          description: '只有高级可见',
        }),
        makeFileInfo({
          name: 'plain.prompt',
          display_name: '',
          description: '无展示名',
        }),
      ])
    )
    const { user } = await renderReady()

    expect(screen.getByRole('button', { name: /第一 Prompt/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /第二 Prompt/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /plain.prompt/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /隐藏高级/ })).not.toBeInTheDocument()

    const search = screen.getByPlaceholderText('搜索')
    await user.type(search, '  SECOND  ')
    expect(screen.queryByRole('button', { name: /第一 Prompt/ })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /第二 Prompt/ })).toBeInTheDocument()

    await user.clear(search)
    await user.type(search, '规划器')
    expect(screen.getByRole('button', { name: /第一 Prompt/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /第二 Prompt/ })).not.toBeInTheDocument()

    await user.clear(search)
    await user.type(search, '只有高级可见')
    expect(screen.getByText('没有可编辑的 Prompt 文件')).toBeInTheDocument()

    await user.clear(search)
    await user.type(search, '   ')
    expect(screen.getByRole('button', { name: /第一 Prompt/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /第二 Prompt/ })).toBeInTheDocument()
  })

  it('默认版本上保存会创建新版本并写入本地选中记录', async () => {
    const saved = makePromptContent('first.prompt', '新版本内容', {
      active_version_id: 'v-new',
      versions: [makeVersion({ id: 'v-new', label: '版本新', active: true })],
    })
    vi.mocked(promptApi.updatePromptFile).mockResolvedValue(saved)
    const { user } = await renderReady('first.prompt current')

    setEditorValue('新版本内容')
    expect(screen.getByText(/有未保存修改/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /保存为新版本/ }))

    await waitFor(() => {
      expect(promptApi.updatePromptFile).toHaveBeenCalledWith('zh-CN', 'first.prompt', '新版本内容', {
        versionId: null,
        createVersion: true,
      })
    })
    expect(toastMock).toHaveBeenCalledWith({
      title: 'Prompt 已保存',
      description: 'zh-CN/first.prompt',
    })
    expect(window.localStorage.getItem(`${VERSION_STORAGE_PREFIX}.zh-CN/first.prompt`)).toBe('v-new')
    expect(promptApi.getPromptCatalog).toHaveBeenCalledTimes(2)
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /^已保存$/ })).toBeDisabled()
    })
  })

  it('选中自定义版本后保存会覆盖该版本而不是新建', async () => {
    vi.mocked(promptApi.getPromptFile).mockResolvedValue(
      makePromptContent('first.prompt', 'v1 内容', {
        active_version_id: 'v1',
        versions: [
          makeVersion({ id: 'v1', label: '版本 1', active: true }),
          makeVersion({ id: 'v2', label: '版本 2' }),
        ],
      })
    )
    vi.mocked(promptApi.getPromptVersionFile).mockResolvedValue(
      makePromptContent('first.prompt', 'v2 内容', {
        active_version_id: 'v1',
        versions: [
          makeVersion({ id: 'v1', label: '版本 1', active: true }),
          makeVersion({ id: 'v2', label: '版本 2' }),
        ],
      })
    )
    vi.mocked(promptApi.updatePromptFile).mockResolvedValue(
      makePromptContent('first.prompt', '改过的 v2', {
        active_version_id: 'v2',
        versions: [
          makeVersion({ id: 'v1', label: '版本 1' }),
          makeVersion({ id: 'v2', label: '版本 2', active: true }),
        ],
      })
    )
    const { user } = await renderReady('v1 内容')

    await chooseComboboxOption(user, 1, /版本 2/)
    await waitFor(() => expect(promptApi.getPromptVersionFile).toHaveBeenCalledWith('zh-CN', 'first.prompt', 'v2'))
    await screen.findByDisplayValue('v2 内容')

    setEditorValue('改过的 v2')
    await user.click(screen.getByRole('button', { name: /保存修改/ }))

    await waitFor(() => {
      expect(promptApi.updatePromptFile).toHaveBeenCalledWith('zh-CN', 'first.prompt', '改过的 v2', {
        versionId: 'v2',
        createVersion: false,
      })
    })
    expect(toastMock).toHaveBeenCalledWith({
      title: 'Prompt 已保存',
      description: 'zh-CN/first.prompt',
    })
  })

  it('应用默认版本会重置自定义内容（删除覆盖）', async () => {
    vi.mocked(promptApi.resetPromptFile).mockResolvedValue(
      makePromptContent('first.prompt', '恢复后的默认', {
        customized: false,
        active_version_id: null,
      })
    )
    const { user } = await renderReady()

    await user.click(screen.getByRole('button', { name: /应用此版本/ }))

    await waitFor(() => {
      expect(promptApi.resetPromptFile).toHaveBeenCalledWith('zh-CN', 'first.prompt')
    })
    expect(toastMock).toHaveBeenCalledWith({
      title: '已应用 Prompt 版本',
      description: 'zh-CN/first.prompt',
    })
    expect(window.localStorage.getItem(`${VERSION_STORAGE_PREFIX}.zh-CN/first.prompt`)).toBe(
      '__default__'
    )
    await screen.findByDisplayValue('恢复后的默认')
  })

  it('应用未启用的自定义版本会调用启用接口', async () => {
    vi.mocked(promptApi.getPromptFile).mockResolvedValue(
      makePromptContent('first.prompt', 'v1 内容', {
        active_version_id: 'v1',
        versions: [
          makeVersion({ id: 'v1', label: '版本 1', active: true }),
          makeVersion({ id: 'v2', label: '版本 2' }),
        ],
      })
    )
    vi.mocked(promptApi.getPromptVersionFile).mockResolvedValue(
      makePromptContent('first.prompt', 'v2 内容', {
        active_version_id: 'v1',
        versions: [
          makeVersion({ id: 'v1', label: '版本 1', active: true }),
          makeVersion({ id: 'v2', label: '版本 2' }),
        ],
      })
    )
    vi.mocked(promptApi.activatePromptVersion).mockResolvedValue(
      makePromptContent('first.prompt', 'v2 内容', {
        active_version_id: 'v2',
        versions: [
          makeVersion({ id: 'v1', label: '版本 1' }),
          makeVersion({ id: 'v2', label: '版本 2', active: true }),
        ],
      })
    )
    const { user } = await renderReady('v1 内容')

    await chooseComboboxOption(user, 1, /版本 2/)
    await screen.findByDisplayValue('v2 内容')
    await user.click(screen.getByRole('button', { name: /应用此版本/ }))

    await waitFor(() => {
      expect(promptApi.activatePromptVersion).toHaveBeenCalledWith('zh-CN', 'first.prompt', 'v2')
    })
    expect(toastMock).toHaveBeenCalledWith({
      title: '已应用 Prompt 版本',
      description: 'zh-CN/first.prompt',
    })
    expect(window.localStorage.getItem(`${VERSION_STORAGE_PREFIX}.zh-CN/first.prompt`)).toBe('v2')
  })

  it('目录、读取、保存、切版本、应用、查看默认和对比失败都会弹出错误提示', async () => {
    vi.mocked(promptApi.getPromptCatalog).mockRejectedValueOnce(new Error('目录挂了'))
    render(<PromptManagementPage />)
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '加载 Prompt 目录失败',
        description: '目录挂了',
        variant: 'destructive',
      })
    })
    expect(screen.getByText('没有可编辑的 Prompt 文件')).toBeInTheDocument()
    cleanup()
    toastMock.mockClear()

    vi.mocked(promptApi.getPromptCatalog).mockResolvedValue(catalog)
    vi.mocked(promptApi.getPromptFile).mockRejectedValueOnce(new Error('读文件失败'))
    render(<PromptManagementPage />)
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '读取 Prompt 失败',
        description: '读文件失败',
        variant: 'destructive',
      })
    })
    cleanup()
    toastMock.mockClear()

    vi.mocked(promptApi.getPromptFile).mockImplementation((_, filename) =>
      Promise.resolve(makePromptContent(filename, `${filename} current`))
    )
    const { user } = await renderReady('first.prompt current')

    setEditorValue('改一下')
    vi.mocked(promptApi.updatePromptFile).mockRejectedValue(new Error('磁盘满了'))
    await user.click(screen.getByRole('button', { name: /保存为新版本/ }))
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '保存 Prompt 失败',
        description: '磁盘满了',
        variant: 'destructive',
      })
    })

    vi.mocked(promptApi.getDefaultPromptFile).mockRejectedValueOnce(new Error('默认读不出来'))
    await user.click(screen.getByRole('button', { name: /查看默认/ }))
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '读取默认 Prompt 失败',
        description: '默认读不出来',
        variant: 'destructive',
      })
    })
    expect(screen.queryByRole('heading', { name: '默认 Prompt' })).not.toBeInTheDocument()

    vi.mocked(promptApi.getDefaultPromptFile).mockRejectedValueOnce(new Error('对比失败'))
    await user.click(screen.getByRole('button', { name: /对比默认/ }))
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '读取默认 Prompt 失败',
        description: '对比失败',
        variant: 'destructive',
      })
    })

    // 有未保存修改时「应用此版本」是禁用的，先回到已保存内容
    setEditorValue('first.prompt current')
    toastMock.mockClear()
    vi.mocked(promptApi.resetPromptFile).mockRejectedValue(new Error('重置失败'))
    await user.click(screen.getByRole('button', { name: /应用此版本/ }))
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '应用 Prompt 版本失败',
        description: '重置失败',
        variant: 'destructive',
      })
    })
  })

  it('有未保存修改时切换版本会提示并保持当前内容', async () => {
    vi.mocked(promptApi.getPromptFile).mockResolvedValue(
      makePromptContent('first.prompt', '原稿', {
        active_version_id: 'v1',
        versions: [
          makeVersion({ id: 'v1', label: '版本 1', active: true }),
          makeVersion({ id: 'v2', label: '版本 2' }),
        ],
      })
    )
    const { user } = await renderReady('原稿')

    setEditorValue('还没保存')
    await chooseComboboxOption(user, 1, /版本 2/)

    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '当前 Prompt 有未保存修改',
        description: '请先保存或放弃当前修改后再切换版本。',
        variant: 'destructive',
      })
    })
    expect(promptApi.getPromptVersionFile).not.toHaveBeenCalled()
    expect(screen.getByTestId('editable-code-editor')).toHaveValue('还没保存')
  })

  it('切换版本失败时弹出错误提示', async () => {
    vi.mocked(promptApi.getPromptFile).mockResolvedValue(
      makePromptContent('first.prompt', '原稿', {
        active_version_id: 'v1',
        versions: [
          makeVersion({ id: 'v1', label: '版本 1', active: true }),
          makeVersion({ id: 'v2', label: '版本 2' }),
        ],
      })
    )
    vi.mocked(promptApi.getPromptVersionFile).mockRejectedValue(new Error('版本丢了'))
    const { user } = await renderReady('原稿')

    await chooseComboboxOption(user, 1, /版本 2/)
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '切换 Prompt 版本失败',
        description: '版本丢了',
        variant: 'destructive',
      })
    })
  })

  it('查看默认会打开只读对话框，关闭后不影响当前编辑', async () => {
    const { user } = await renderReady()
    await user.click(screen.getByRole('button', { name: /查看默认/ }))

    expect(await screen.findByRole('heading', { name: '默认 Prompt' })).toBeInTheDocument()
    expect(screen.getByText(/zh-CN\/first.prompt 的内置模板/)).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.getByTestId('readonly-code-editor')).toHaveValue('first.prompt default')
    })

    await user.click(screen.getByRole('button', { name: '关闭' }))
    await waitFor(() => {
      expect(screen.queryByRole('heading', { name: '默认 Prompt' })).not.toBeInTheDocument()
    })
    expect(screen.getByTestId('editable-code-editor')).toHaveValue('first.prompt current')
  })

  it('对比默认会统计新增、删除和修改，空行删除也能计入', async () => {
    vi.mocked(promptApi.getDefaultPromptFile).mockResolvedValue(
      makePromptContent('first.prompt', 'keep\nhello\n\ngone\ntail')
    )
    const { user } = await renderReady('first.prompt current')

    setEditorValue('keep\nhello\n\ngone\ntail')
    await user.click(screen.getByRole('button', { name: /对比默认/ }))
    await screen.findByText('退出对比')
    expect(screen.getByText(/新增 0/)).toBeInTheDocument()
    expect(screen.getByText(/删除 0/)).toBeInTheDocument()
    expect(screen.getByText(/修改 0/)).toBeInTheDocument()

    // 同行替换：hello → hallo，走修改 + 行内相邻区间合并
    setEditorValue('keep\nhallo\n\ngone\ntail')
    expect(screen.getByText(/修改 1/)).toBeInTheDocument()

    // 删掉空行
    setEditorValue('keep\nhallo\ngone\ntail')
    expect(screen.getByText(/删除 1/)).toBeInTheDocument()

    // 在末尾追加一行，LCS 会把它记成新增而不是改写空行
    setEditorValue('keep\nhello\n\ngone\ntail\nnew')
    expect(screen.getByText(/新增 1/)).toBeInTheDocument()

    // 整段清空对照默认，覆盖只剩删除的 LCS 路径
    setEditorValue('')
    expect(screen.getByText(/删除/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /退出对比/ }))
    expect(screen.queryByText(/新增/)).not.toBeInTheDocument()
    expect(screen.queryByTestId('readonly-code-editor')).not.toBeInTheDocument()
  })

  it('显示/隐藏高级会过滤列表，隐藏时会离开当前高级文件', async () => {
    vi.mocked(promptApi.getPromptCatalog).mockResolvedValue(
      makeCatalog([
        makeFileInfo(),
        makeFileInfo({
          name: 'adv.prompt',
          display_name: '高级模板',
          advanced: true,
          customized: false,
          custom_version_count: 2,
          description: '进阶说明',
        }),
      ])
    )
    vi.mocked(promptApi.getPromptFile).mockImplementation((_, filename) =>
      Promise.resolve(
        makePromptContent(filename, `${filename} current`, {
          versions:
            filename === 'adv.prompt'
              ? [makeVersion({ id: 'v1', label: '旧版' }), makeVersion({ id: 'v2', label: '新版' })]
              : [],
        })
      )
    )
    const { user } = await renderReady()

    expect(screen.queryByRole('button', { name: /高级模板/ })).not.toBeInTheDocument()
    await user.click(getButtonByText(/显示高级/))
    expect(screen.getByRole('button', { name: /高级模板/ })).toBeInTheDocument()
    expect(screen.getByText('高级')).toBeInTheDocument()
    expect(screen.getByText('2 版')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /高级模板/ }))
    await screen.findByDisplayValue('adv.prompt current')
    expect(screen.getByText(/2 个自定义版本/)).toBeInTheDocument()
    expect(screen.getAllByText('进阶说明').length).toBeGreaterThan(0)

    await user.click(getButtonByText(/隐藏高级/))
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: /高级模板/ })).not.toBeInTheDocument()
    })
    await screen.findByDisplayValue('first.prompt current')
  })

  it('切换语言会清空搜索并选中目标语言的第一个可见文件', async () => {
    vi.mocked(promptApi.getPromptCatalog).mockResolvedValue(
      makeCatalog(
        [
          makeFileInfo(),
          makeFileInfo({ name: 'second.prompt', display_name: '第二 Prompt', size: 14 }),
        ],
        {
          languages: ['zh-CN', 'en'],
          files: {
            'zh-CN': [
              makeFileInfo(),
              makeFileInfo({ name: 'second.prompt', display_name: '第二 Prompt', size: 14 }),
            ],
            en: [
              makeFileInfo({
                name: 'english.prompt',
                display_name: 'English Prompt',
                size: 2048,
                description: 'English only',
              }),
            ],
          },
        }
      )
    )
    const { user } = await renderReady()

    await user.type(screen.getByPlaceholderText('搜索'), '第一')
    expect(screen.queryByRole('button', { name: /第二 Prompt/ })).not.toBeInTheDocument()

    await chooseComboboxOption(user, 0, 'en')
    await waitFor(() => {
      expect(screen.getByPlaceholderText('搜索')).toHaveValue('')
    })
    await screen.findByRole('button', { name: /English Prompt/ })
    await screen.findByDisplayValue('english.prompt current')
    expect(screen.getByText(/en · 2\.0 KB/)).toBeInTheDocument()
  })

  it('刷新目录时会按当前语言回退，并在当前文件消失后改选第一个基础文件', async () => {
    const enOnly = makeCatalog(
      [makeFileInfo({ name: 'english.prompt', display_name: 'English Prompt', size: 2097152 })],
      {
        languages: ['en'],
        files: {
          en: [makeFileInfo({ name: 'english.prompt', display_name: 'English Prompt', size: 2097152 })],
        },
      }
    )
    vi.mocked(promptApi.getPromptCatalog)
      .mockResolvedValueOnce(makeCatalog(undefined, { languages: ['zh-CN', 'en'], files: {
        'zh-CN': catalog.files['zh-CN'],
        en: [makeFileInfo({ name: 'english.prompt', display_name: 'English Prompt' })],
      } }))
      .mockResolvedValue(enOnly)

    const { user } = await renderReady()
    await user.click(screen.getByRole('button', { name: '刷新' }))

    await screen.findByRole('button', { name: /English Prompt/ })
    await screen.findByDisplayValue('english.prompt current')
    expect(screen.getByText(/en · 2\.0 MB/)).toBeInTheDocument()
  })

  it('会读取本地保存的版本：默认模板、指定版本、无效记录分别走不同加载路径', async () => {
    window.localStorage.setItem(`${VERSION_STORAGE_PREFIX}.zh-CN/first.prompt`, '__default__')
    window.localStorage.setItem(`${VERSION_STORAGE_PREFIX}.zh-CN/second.prompt`, 'v2')
    vi.mocked(promptApi.getPromptFile).mockImplementation((_, filename) =>
      Promise.resolve(
        makePromptContent(filename, `${filename} active`, {
          active_version_id: 'v1',
          versions: [
            makeVersion({ id: 'v1', label: '版本 1', active: true }),
            makeVersion({ id: 'v2', label: '版本 2' }),
          ],
        })
      )
    )
    vi.mocked(promptApi.getDefaultPromptFile).mockResolvedValue(
      makePromptContent('first.prompt', '持久化默认内容')
    )
    vi.mocked(promptApi.getPromptVersionFile).mockResolvedValue(
      makePromptContent('second.prompt', '持久化 v2 内容', {
        active_version_id: 'v1',
        versions: [
          makeVersion({ id: 'v1', label: '版本 1', active: true }),
          makeVersion({ id: 'v2', label: '版本 2' }),
        ],
      })
    )

    const { user } = await renderReady('持久化默认内容')
    expect(promptApi.getDefaultPromptFile).toHaveBeenCalledWith('zh-CN', 'first.prompt')

    await user.click(screen.getByRole('button', { name: /第二 Prompt/ }))
    await screen.findByDisplayValue('持久化 v2 内容')
    expect(promptApi.getPromptVersionFile).toHaveBeenCalledWith('zh-CN', 'second.prompt', 'v2')

    window.localStorage.setItem(`${VERSION_STORAGE_PREFIX}.zh-CN/first.prompt`, 'already-gone')
    await user.click(screen.getByRole('button', { name: /第一 Prompt/ }))
    await screen.findByDisplayValue('first.prompt active')
  })

  it('卸载或快速切换文件时，过期的读取失败不会再弹 toast', async () => {
    const firstLoad = deferred<PromptFileContent>()
    vi.mocked(promptApi.getPromptFile).mockImplementation((_, filename) => {
      if (filename === 'first.prompt') return firstLoad.promise
      return Promise.resolve(makePromptContent(filename, `${filename} current`))
    })

    const user = userEvent.setup()
    render(<PromptManagementPage />)
    await screen.findByRole('button', { name: /第二 Prompt/ })
    await user.click(screen.getByRole('button', { name: /第二 Prompt/ }))
    await screen.findByDisplayValue('second.prompt current')

    firstLoad.reject(new Error('已过期'))
    await waitFor(() => expect(promptApi.getPromptFile).toHaveBeenCalledTimes(2))
    expect(toastMock).not.toHaveBeenCalled()
  })

  it('参数校验失败时在编辑区上方展示告警', async () => {
    vi.mocked(promptApi.getPromptFile).mockResolvedValue(
      makePromptContent('first.prompt', '缺参数', {
        validation: {
          valid: false,
          missing_placeholders: ['name'],
          extra_placeholders: [],
          message: '缺少 {name}',
        },
      })
    )
    await renderReady('缺参数')
    expect(screen.getByRole('alert')).toHaveTextContent('Prompt 参数不匹配')
    expect(screen.getByRole('alert')).toHaveTextContent('缺少 {name}')
  })

  it('没有语言时清空编辑器，文件大小按 B/KB 展示', async () => {
    vi.mocked(promptApi.getPromptCatalog).mockResolvedValue(
      makeCatalog(
        [
          makeFileInfo({ size: 13 }),
          makeFileInfo({
            name: 'second.prompt',
            display_name: '',
            size: 1536,
            customized: false,
          }),
        ],
        { languages: [], files: {} }
      )
    )
    render(<PromptManagementPage />)
    await waitFor(() => {
      expect(screen.getByText('没有可编辑的 Prompt 文件')).toBeInTheDocument()
    })
    expect(screen.getByText('未选择文件')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^已保存$/ })).toBeDisabled()
  })

  it('保存过程中按钮显示保存中，接口返回空版本信息时回退到默认版本', async () => {
    const save = deferred<PromptFileContent>()
    vi.mocked(promptApi.updatePromptFile).mockReturnValue(save.promise)
    const { user } = await renderReady('first.prompt current')

    setEditorValue('写入中')
    await user.click(screen.getByRole('button', { name: /保存为新版本/ }))
    expect(screen.getByRole('button', { name: /保存中/ })).toBeDisabled()

    save.resolve({
      ...makePromptContent('first.prompt', '写入中'),
      versions: undefined as unknown as PromptVersionInfo[],
      validation: undefined as unknown as PromptFileContent['validation'],
      active_version_id: null,
    })

    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: 'Prompt 已保存',
        description: 'zh-CN/first.prompt',
      })
    })
    expect(window.localStorage.getItem(`${VERSION_STORAGE_PREFIX}.zh-CN/first.prompt`)).toBe(
      '__default__'
    )
    expect(screen.getByRole('button', { name: /^已保存$/ })).toBeDisabled()
  })

  it('应用版本过程中显示应用中，成功后回退到接口给出的版本', async () => {
    const apply = deferred<PromptFileContent>()
    vi.mocked(promptApi.resetPromptFile).mockReturnValue(apply.promise)
    const { user } = await renderReady()

    await user.click(screen.getByRole('button', { name: /应用此版本/ }))
    expect(screen.getByRole('button', { name: /应用中/ })).toBeDisabled()

    apply.resolve(
      makePromptContent('first.prompt', '已重置', {
        active_version_id: null,
      })
    )
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '已应用 Prompt 版本',
        description: 'zh-CN/first.prompt',
      })
    })
    await screen.findByDisplayValue('已重置')
  })

  it('未定制文件的默认版本显示已应用，切换到默认版本会拉取默认内容', async () => {
    vi.mocked(promptApi.getPromptCatalog).mockResolvedValue(
      makeCatalog([
        makeFileInfo({ customized: false, custom_version_count: 1 }),
      ])
    )
    vi.mocked(promptApi.getPromptFile).mockResolvedValue(
      makePromptContent('first.prompt', '启用中的版本', {
        customized: false,
        active_version_id: 'v1',
        versions: [makeVersion({ id: 'v1', label: '版本 1', active: true })],
      })
    )
    vi.mocked(promptApi.getDefaultPromptFile).mockResolvedValue(
      makePromptContent('first.prompt', '出厂默认', { customized: false, active_version_id: null })
    )
    const { user } = await renderReady('启用中的版本')

    expect(screen.getByRole('button', { name: /已应用/ })).toBeDisabled()
    await chooseComboboxOption(user, 1, /默认版本/)
    await screen.findByDisplayValue('出厂默认')
    expect(promptApi.getDefaultPromptFile).toHaveBeenCalledWith('zh-CN', 'first.prompt')
    expect(window.localStorage.getItem(`${VERSION_STORAGE_PREFIX}.zh-CN/first.prompt`)).toBe(
      '__default__'
    )
    // 目录里 customized=false 时，选中默认版本仍视为已应用
    expect(screen.getByRole('button', { name: /已应用/ })).toBeDisabled()
  })

  it('对比模式下若本地记的是默认版本，切换文件会复用默认内容而不再请求一次', async () => {
    window.localStorage.setItem(`${VERSION_STORAGE_PREFIX}.zh-CN/second.prompt`, '__default__')
    vi.mocked(promptApi.getPromptFile).mockImplementation((_, filename) =>
      Promise.resolve(
        makePromptContent(filename, `${filename} active`, {
          active_version_id: 'v1',
          versions: [makeVersion({ id: 'v1', label: '版本 1', active: true })],
        })
      )
    )
    vi.mocked(promptApi.getDefaultPromptFile).mockImplementation((_, filename) =>
      Promise.resolve(makePromptContent(filename, `${filename} default`))
    )
    const { user } = await renderReady('first.prompt active')

    await user.click(screen.getByRole('button', { name: /对比默认/ }))
    await screen.findByDisplayValue('first.prompt default')
    const callsAfterEnterDiff = vi.mocked(promptApi.getDefaultPromptFile).mock.calls.length

    await user.click(screen.getByRole('button', { name: /第二 Prompt/ }))
    await waitFor(() => {
      expect(screen.getByTestId('editable-code-editor')).toHaveValue('second.prompt default')
      expect(screen.getByTestId('readonly-code-editor')).toHaveValue('second.prompt default')
    })
    // 持久化默认版本时，文件加载本身就会取 default，对比面板直接复用，不再多打一次
    expect(vi.mocked(promptApi.getDefaultPromptFile).mock.calls.length).toBe(callsAfterEnterDiff + 1)
    expect(promptApi.getDefaultPromptFile).toHaveBeenLastCalledWith('zh-CN', 'second.prompt')
  })

  it('目录回退到既不含当前语言也不含中文时，使用返回的第一种语言', async () => {
    vi.mocked(promptApi.getPromptCatalog)
      .mockResolvedValueOnce(
        makeCatalog(undefined, {
          languages: ['zh-CN', 'ja'],
          files: {
            'zh-CN': catalog.files['zh-CN'],
            ja: [makeFileInfo({ name: 'jp.prompt', display_name: '日本語', size: 20 })],
          },
        })
      )
      .mockResolvedValue(
        makeCatalog(undefined, {
          languages: ['ja'],
          files: {
            ja: [makeFileInfo({ name: 'jp.prompt', display_name: '日本語', size: 20 })],
          },
        })
      )
    const { user } = await renderReady()
    await user.click(screen.getByRole('button', { name: '刷新' }))
    await screen.findByRole('button', { name: /日本語/ })
    await screen.findByDisplayValue('jp.prompt current')
  })
})
