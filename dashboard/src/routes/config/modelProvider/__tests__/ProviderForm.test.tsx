import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import * as configApi from '@/lib/config-api'

import { ProviderForm } from '../ProviderForm'
import type { APIProvider } from '../types'

const toastMock = vi.fn()

vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: toastMock }) }))

// 仅桩掉客户端类型拉取，保留 config-api 其余导出（类型与常量）
vi.mock('@/lib/config-api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/config-api')>()
  return { ...actual, fetchModelClientTypes: vi.fn() }
})

const makeProvider = (overrides: Partial<APIProvider> = {}): APIProvider => ({
  name: 'DeepSeek',
  base_url: 'https://api.deepseek.com',
  api_key: 'sk-test',
  client_type: 'openai',
  max_retry: 2,
  timeout: 30,
  retry_interval: 10,
  ...overrides,
})

/** 空白提供商，对应父组件「添加提供商」时传入的初始对象 */
const emptyProvider = (): APIProvider =>
  makeProvider({ name: '', base_url: '', api_key: '', max_retry: null, timeout: null, retry_interval: null })

interface RenderOptions {
  editingProvider?: APIProvider | null
  editingIndex?: number | null
  providers?: APIProvider[]
  onSave?: (provider: APIProvider, index: number | null) => Promise<void> | void
}

function renderForm(options: RenderOptions = {}) {
  const onSave = vi.fn(options.onSave)
  const onOpenChange = vi.fn()
  render(
    <ProviderForm
      open
      onOpenChange={onOpenChange}
      editingProvider={options.editingProvider === undefined ? emptyProvider() : options.editingProvider}
      editingIndex={options.editingIndex ?? null}
      providers={options.providers ?? []}
      onSave={onSave}
      tourState={{ isRunning: false }}
    />
  )
  return { onSave, onOpenChange }
}

/** 模板下拉触发器：通过 data-tour 容器定位，避免与客户端类型 Select 的 combobox 角色混淆 */
function getTemplateTrigger() {
  const container = document.querySelector('[data-tour="provider-template-select"]')
  if (!container) throw new Error('未找到模板选择容器')
  return within(container as HTMLElement).getByRole('combobox')
}

beforeEach(() => {
  // mockReset 会清空实现，这里每个用例前恢复默认返回
  vi.mocked(configApi.fetchModelClientTypes).mockResolvedValue([])

  // Radix Select / Popover 在 jsdom 下依赖 PointerCapture API，按需补桩
  if (!Element.prototype.hasPointerCapture) {
    Element.prototype.hasPointerCapture = vi.fn()
  }
  if (!Element.prototype.setPointerCapture) {
    Element.prototype.setPointerCapture = vi.fn()
  }
  if (!Element.prototype.releasePointerCapture) {
    Element.prototype.releasePointerCapture = vi.fn()
  }
})

afterEach(() => {
  cleanup()
})

describe('ProviderForm 基础渲染', () => {
  it('新增模式（editingIndex 为 null）标题为「添加提供商」', async () => {
    renderForm()
    expect(await screen.findByText('添加提供商')).toBeInTheDocument()
  })

  it('编辑模式标题为「编辑提供商」，表单回填当前提供商数据', async () => {
    renderForm({ editingProvider: makeProvider(), editingIndex: 0 })
    expect(await screen.findByText('编辑提供商')).toBeInTheDocument()
    expect(screen.getByLabelText('名称 *')).toHaveValue('DeepSeek')
    expect(screen.getByLabelText('基础 URL *')).toHaveValue('https://api.deepseek.com')
    expect(screen.getByLabelText('API Key *')).toHaveValue('sk-test')
  })

  it('点击取消按钮调用 onOpenChange(false)', async () => {
    const user = userEvent.setup()
    const { onOpenChange } = renderForm()
    await user.click(screen.getByRole('button', { name: '取消' }))
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })
})

describe('ProviderForm 表单校验', () => {
  it('必填字段为空时提交展示三条错误且不调用 onSave', async () => {
    const user = userEvent.setup()
    const { onSave } = renderForm()
    await user.click(screen.getByRole('button', { name: '保存' }))

    expect(await screen.findByText('请输入提供商名称')).toBeInTheDocument()
    expect(screen.getByText('请输入基础 URL')).toBeInTheDocument()
    expect(screen.getByText('请输入 API Key')).toBeInTheDocument()
    expect(screen.getAllByRole('alert')).toHaveLength(3)
    expect(onSave).not.toHaveBeenCalled()
  })

  it('在出错字段中输入内容后即时清除该字段错误，其余错误保留', async () => {
    const user = userEvent.setup()
    renderForm()
    await user.click(screen.getByRole('button', { name: '保存' }))
    await screen.findByText('请输入提供商名称')

    await user.type(screen.getByLabelText('名称 *'), 'X')
    expect(screen.queryByText('请输入提供商名称')).not.toBeInTheDocument()
    expect(screen.getByText('请输入基础 URL')).toBeInTheDocument()
    expect(screen.getByText('请输入 API Key')).toBeInTheDocument()
  })

  it('名称与现有提供商重复时报重复错误且不保存', async () => {
    const user = userEvent.setup()
    const { onSave } = renderForm({
      editingProvider: makeProvider({ name: 'deepseek' }),
      editingIndex: null,
      providers: [makeProvider({ name: 'DeepSeek' })],
    })
    await user.click(screen.getByRole('button', { name: '保存' }))
    expect(await screen.findByText('提供商名称已存在，请使用其他名称')).toBeInTheDocument()
    expect(onSave).not.toHaveBeenCalled()
  })

  it('editingProvider 为 null 时提交直接忽略，不报错也不保存', async () => {
    const user = userEvent.setup()
    const { onSave } = renderForm({ editingProvider: null })
    await user.click(screen.getByRole('button', { name: '保存' }))
    expect(onSave).not.toHaveBeenCalled()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})

describe('ProviderForm API Key 交互', () => {
  it('默认以密码框隐藏 API Key，点击眼睛按钮切换显示/隐藏', async () => {
    const user = userEvent.setup()
    renderForm({ editingProvider: makeProvider(), editingIndex: 0 })
    const keyInput = screen.getByLabelText('API Key *')
    expect(keyInput).toHaveAttribute('type', 'password')

    await user.click(screen.getByTitle('显示密钥'))
    expect(keyInput).toHaveAttribute('type', 'text')

    await user.click(screen.getByTitle('隐藏密钥'))
    expect(keyInput).toHaveAttribute('type', 'password')
  })

  it('点击复制按钮写入剪贴板并弹出成功提示', async () => {
    const user = userEvent.setup()
    const writeText = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue(undefined)
    renderForm({ editingProvider: makeProvider({ api_key: 'sk-copy-me' }), editingIndex: 0 })

    await user.click(screen.getByTitle('复制密钥'))
    expect(writeText).toHaveBeenCalledWith('sk-copy-me')
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '复制成功',
        description: 'API Key 已复制到剪贴板',
      })
    )
  })

  it('剪贴板不可用时弹出失败提示', async () => {
    const user = userEvent.setup()
    vi.spyOn(navigator.clipboard, 'writeText').mockRejectedValue(new Error('denied'))
    renderForm({ editingProvider: makeProvider(), editingIndex: 0 })

    await user.click(screen.getByTitle('复制密钥'))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '复制失败',
        description: '无法访问剪贴板',
        variant: 'destructive',
      })
    )
  })

  it('API Key 为空时点击复制不触发剪贴板与提示', async () => {
    const user = userEvent.setup()
    const writeText = vi.spyOn(navigator.clipboard, 'writeText')
    renderForm()
    await user.click(screen.getByTitle('复制密钥'))
    expect(writeText).not.toHaveBeenCalled()
    expect(toastMock).not.toHaveBeenCalled()
  })
})

describe('ProviderForm 模板切换', () => {
  it('选择预设模板后自动填充名称/URL 并锁定 URL 与客户端类型', async () => {
    const user = userEvent.setup()
    renderForm()
    // 初始为自定义模板
    expect(getTemplateTrigger()).toHaveTextContent('自定义')

    await user.click(getTemplateTrigger())
    await user.click(await screen.findByRole('option', { name: 'DeepSeek' }))

    expect(screen.getByLabelText('名称 *')).toHaveValue('DeepSeek')
    const urlInput = screen.getByLabelText('基础 URL *')
    expect(urlInput).toHaveValue('https://api.deepseek.com')
    expect(urlInput).toBeDisabled()
    expect(screen.getByText('使用模板时 URL 不可编辑,切换到"自定义"以手动配置')).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: '客户端类型' })).toBeDisabled()
    expect(
      screen.getByText('使用模板时客户端类型不可编辑,切换到"自定义"以手动配置')
    ).toBeInTheDocument()
  })

  it('从模板切回「自定义」时清空名称/URL 并解锁编辑', async () => {
    const user = userEvent.setup()
    renderForm()
    await user.click(getTemplateTrigger())
    await user.click(await screen.findByRole('option', { name: 'DeepSeek' }))

    await user.click(getTemplateTrigger())
    await user.click(await screen.findByRole('option', { name: '自定义' }))

    expect(screen.getByLabelText('名称 *')).toHaveValue('')
    const urlInput = screen.getByLabelText('基础 URL *')
    expect(urlInput).toHaveValue('')
    expect(urlInput).toBeEnabled()
    expect(screen.getByRole('combobox', { name: '客户端类型' })).toBeEnabled()
  })

  it('编辑已有提供商时按 base_url 与 client_type 自动匹配模板', async () => {
    renderForm({ editingProvider: makeProvider(), editingIndex: 0 })
    await waitFor(() => expect(getTemplateTrigger()).toHaveTextContent('DeepSeek'))
    expect(screen.getByLabelText('基础 URL *')).toBeDisabled()
  })

  it('URL 不匹配任何模板时保持「自定义」且 URL 可编辑', async () => {
    renderForm({
      editingProvider: makeProvider({ base_url: 'https://my.private.host/v1' }),
      editingIndex: 0,
    })
    expect(getTemplateTrigger()).toHaveTextContent('自定义')
    expect(screen.getByLabelText('基础 URL *')).toBeEnabled()
  })
})

describe('ProviderForm 客户端类型选项', () => {
  it('后端返回的插件类型与内置 openai/gemini 合并展示', async () => {
    const user = userEvent.setup()
    vi.mocked(configApi.fetchModelClientTypes).mockResolvedValue([
      {
        client_type: 'plugin-llm',
        owner_plugin_id: 'pluginA',
        version: '1.0.0',
        description: '插件客户端',
        builtin: false,
      },
    ])
    renderForm()
    await waitFor(() => expect(configApi.fetchModelClientTypes).toHaveBeenCalled())

    await user.click(screen.getByRole('combobox', { name: '客户端类型' }))
    expect(await screen.findByRole('option', { name: 'plugin-llm (pluginA)' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'openai' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'openai_responses' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'gemini' })).toBeInTheDocument()

    // 选择插件类型后触发器展示新值
    await user.click(screen.getByRole('option', { name: 'plugin-llm (pluginA)' }))
    expect(screen.getByRole('combobox', { name: '客户端类型' })).toHaveTextContent('plugin-llm')
  })

  it('客户端类型拉取失败时降级为全部内置客户端选项', async () => {
    const user = userEvent.setup()
    vi.mocked(configApi.fetchModelClientTypes).mockRejectedValue(new Error('network'))
    renderForm()
    await waitFor(() => expect(configApi.fetchModelClientTypes).toHaveBeenCalled())

    await user.click(screen.getByRole('combobox', { name: '客户端类型' }))
    expect(await screen.findByRole('option', { name: 'openai' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'openai_responses' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'gemini' })).toBeInTheDocument()
  })
})

describe('ProviderForm 保存流程', () => {
  it('校验通过后以本地编辑值与索引调用 onSave，等待期间按钮显示「保存中...」', async () => {
    const user = userEvent.setup()
    let resolveSave: (() => void) | undefined
    const { onSave } = renderForm({
      editingProvider: makeProvider(),
      editingIndex: 2,
      onSave: () =>
        new Promise<void>((resolve) => {
          resolveSave = resolve
        }),
    })

    await user.click(screen.getByRole('button', { name: '保存' }))
    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({ name: 'DeepSeek', base_url: 'https://api.deepseek.com' }),
      2
    )
    const savingButton = screen.getByRole('button', { name: '保存中...' })
    expect(savingButton).toBeDisabled()

    resolveSave?.()
    expect(await screen.findByRole('button', { name: '保存' })).toBeEnabled()
  })

  it('修改数值字段后保存，onSave 收到解析后的整数与清空的 null', async () => {
    const user = userEvent.setup()
    const { onSave } = renderForm({ editingProvider: makeProvider(), editingIndex: 0 })

    const retryInput = screen.getByLabelText('最大重试')
    await user.clear(retryInput)
    await user.type(retryInput, '5')
    // 清空超时输入框，应回传 null（由上层 cleanProviderData 填默认值）
    await user.clear(screen.getByLabelText('超时(秒)'))

    await user.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() =>
      expect(onSave).toHaveBeenCalledWith(
        expect.objectContaining({ max_retry: 5, timeout: null, retry_interval: 10 }),
        0
      )
    )
  })
})
