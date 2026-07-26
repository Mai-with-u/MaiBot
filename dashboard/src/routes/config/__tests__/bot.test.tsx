import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { BotConfigPage } from '../bot'
import * as configApi from '@/lib/config-api'

import type { ConfigSchema } from '@/types/config-schema'
import type { ReactNode } from 'react'

const toastMock = vi.fn()

// 路由 search 字符串（供 useRouterState mock 读取，可按用例改写）
let routerSearchStr = ''

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: toastMock }) }))
vi.mock('@tanstack/react-router', () => ({
  Link: ({ children }: { children?: ReactNode }) => <span data-testid="router-link">{children}</span>,
  useRouterState: ({
    select,
  }: {
    select: (state: { location: { searchStr: string } }) => string
  }) => select({ location: { searchStr: routerSearchStr } }),
}))
vi.mock('@/lib/restart-context', () => ({
  RestartProvider: ({ children }: { children: ReactNode }) => children,
  useRestart: () => ({ isRestarting: false, triggerRestart: vi.fn() }),
}))
vi.mock('@/components/restart-overlay', () => ({ RestartOverlay: () => null }))

// CodeEditor 桩：用原生 textarea 替代 Monaco，保留 value/onChange 数据链路
vi.mock('@/components/CodeEditor', () => ({
  CodeEditor: ({
    value,
    onChange,
    placeholder,
  }: {
    value: string
    onChange: (value: string) => void
    placeholder?: string
  }) => (
    <textarea
      aria-label="源码编辑器"
      placeholder={placeholder}
      value={value}
      onChange={(event) => onChange(event.target.value)}
    />
  ),
}))

// CoreSettings 桩：展示接收到的分节数据，并暴露 personality 修改回调
vi.mock('../bot/CoreSettings', () => ({
  CoreSettings: ({
    botSection,
    personalitySection,
    onPersonalitySectionChange,
  }: {
    botSection: Record<string, unknown> | null
    personalitySection: Record<string, unknown> | null
    onPersonalitySectionChange: (value: Record<string, unknown>) => void
  }) => (
    <div data-testid="core-settings">
      <span data-testid="core-bot">{JSON.stringify(botSection)}</span>
      <span data-testid="core-personality">{JSON.stringify(personalitySection)}</span>
      <button type="button" onClick={() => onPersonalitySectionChange({ personality: '新人格' })}>
        change-personality
      </button>
    </div>
  ),
}))

// DynamicConfigForm 桩：展示 schema 组织形式与传入的值，并为每个嵌套分节提供修改按钮
vi.mock('@/components/dynamic-form', () => ({
  DynamicConfigForm: ({
    schema,
    values,
    onChange,
    advancedVisible,
  }: {
    schema: { className: string; nested?: Record<string, unknown> }
    values: Record<string, unknown>
    onChange: (fieldPath: string, value: unknown) => void
    advancedVisible?: boolean
  }) => (
    <div data-testid={`form-${schema.className}`} data-advanced={String(Boolean(advancedVisible))}>
      <span data-testid={`form-${schema.className}-sections`}>
        {Object.keys(schema.nested ?? {}).join(',')}
      </span>
      <span data-testid={`form-${schema.className}-values`}>{JSON.stringify(values)}</span>
      {Object.keys(schema.nested ?? {}).map((sectionName) => (
        <button
          key={sectionName}
          type="button"
          onClick={() => onChange(`${sectionName}.stub_field`, `${sectionName}-新值`)}
        >
          {`change-${schema.className}-${sectionName}`}
        </button>
      ))}
    </div>
  ),
}))

vi.mock('@/lib/config-api', () => ({
  getBotConfig: vi.fn(),
  getBotConfigCached: vi.fn(),
  getBotConfigRaw: vi.fn(),
  getBotConfigSchema: vi.fn(),
  updateBotConfig: vi.fn(),
  updateBotConfigRaw: vi.fn(),
  updateBotConfigSection: vi.fn(),
}))

function baseConfig(): Record<string, unknown> {
  return {
    bot: { nickname: '麦麦', qq_account: 12345 },
    personality: { personality: '原始人格' },
    sub_feature: { enabled: true },
    experimental: { debug: false },
    // 旧版遗留 memory 分区：加载时应被剥离，不应进入表单与保存载荷
    memory: { legacy: true },
  }
}

function sectionSchema(
  className: string,
  classDoc: string,
  ui: Partial<ConfigSchema> = {}
): ConfigSchema {
  return { className, classDoc, fields: [], nested: {}, ...ui }
}

function baseSchema(): { schema: ConfigSchema } {
  return {
    schema: {
      className: 'BotConfigRoot',
      classDoc: '',
      fields: [],
      nested: {
        // uiOrder 故意与书写顺序相反，用于验证排序逻辑
        bot: sectionSchema('BotSection', '机器人配置', { uiLabel: '机器人', uiOrder: 2 }),
        personality: sectionSchema('PersonalitySection', '人格配置', {
          uiLabel: '人格',
          uiOrder: 1,
        }),
        // 无 uiLabel、有 uiParent：应归并进「机器人」tab
        sub_feature: sectionSchema('SubFeatureSection', '子功能配置', { uiParent: 'bot' }),
        // advanced tab：默认隐藏，点击「更多」后出现
        experimental: sectionSchema('ExperimentalSection', '实验性配置', {
          uiLabel: '实验性',
          uiOrder: 3,
          uiAdvanced: true,
        }),
      },
    },
  }
}

beforeEach(() => {
  localStorage.clear()
  routerSearchStr = ''
  vi.mocked(configApi.getBotConfigCached).mockResolvedValue(baseConfig())
  vi.mocked(configApi.getBotConfig).mockResolvedValue(baseConfig())
  vi.mocked(configApi.getBotConfigSchema).mockResolvedValue(baseSchema() as never)
  // 实际接口返回 { content } 对象，页面从 .content 取原始 TOML
  vi.mocked(configApi.getBotConfigRaw).mockResolvedValue({
    content: 'title = "hello\\nworld"',
  } as never)
  vi.mocked(configApi.updateBotConfig).mockResolvedValue({})
  vi.mocked(configApi.updateBotConfigRaw).mockResolvedValue({})
  vi.mocked(configApi.updateBotConfigSection).mockResolvedValue({})
})

async function renderBotPage() {
  render(<BotConfigPage />)
  // 等待初始加载完成（模式切换 tab 出现）
  await screen.findByRole('tab', { name: '核心设置' })
}

/** 切换到「详细设置」模式并等待默认 tab 的表单渲染完成 */
async function enterDetailMode(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('tab', { name: '详细设置' }))
  await screen.findByTestId('form-personality')
}

describe('BotConfigPage 特征化', () => {
  it('初始加载调用 getBotConfigCached + getBotConfigSchema，核心设置展示分节数据', async () => {
    await renderBotPage()
    expect(configApi.getBotConfigCached).toHaveBeenCalledTimes(1)
    expect(configApi.getBotConfigSchema).toHaveBeenCalledTimes(1)
    // 核心设置模式默认渲染，接收 bot / personality 分节
    expect(screen.getByTestId('core-bot')).toHaveTextContent('麦麦')
    expect(screen.getByTestId('core-personality')).toHaveTextContent('原始人格')
  })

  it('初始加载失败时弹出加载失败 toast', async () => {
    vi.mocked(configApi.getBotConfigCached).mockRejectedValue(new Error('网络错误'))
    render(<BotConfigPage />)
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '加载失败',
        description: '网络错误',
        variant: 'destructive',
      })
    )
  })

  it('手动保存：personality 变更后保存整份配置，载荷剥离 legacy memory 分区', async () => {
    const user = userEvent.setup()
    await renderBotPage()

    // 初始无未保存更改：保存按钮为「已保存」且禁用
    expect(screen.getByRole('button', { name: '已保存' })).toBeDisabled()

    await user.click(screen.getByText('change-personality'))
    const saveButton = await screen.findByRole('button', { name: '保存' })
    await user.click(saveButton)

    await waitFor(() => expect(configApi.updateBotConfig).toHaveBeenCalledTimes(1))
    const savedConfig = vi.mocked(configApi.updateBotConfig).mock.calls[0][0]
    expect(savedConfig.personality).toEqual({ personality: '新人格' })
    expect(savedConfig.bot).toEqual({ nickname: '麦麦', qq_account: 12345 })
    // 加载时剥离的 legacy memory 不应回写
    expect('memory' in savedConfig).toBe(false)

    expect(toastMock).toHaveBeenCalledWith({ title: '保存成功', description: '麦麦设置已保存' })
    // 手动保存经过 autosave barrier：待执行的分区防抖保存被取消
    expect(configApi.updateBotConfigSection).not.toHaveBeenCalled()
    // 保存完成后回到「已保存」状态
    await screen.findByRole('button', { name: '已保存' })
  })

  it('手动保存失败时弹出保存失败 toast', async () => {
    vi.mocked(configApi.updateBotConfig).mockRejectedValue(new Error('后端拒绝'))
    const user = userEvent.setup()
    await renderBotPage()

    await user.click(screen.getByText('change-personality'))
    await user.click(await screen.findByRole('button', { name: '保存' }))

    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '保存失败',
        description: '后端拒绝',
        variant: 'destructive',
      })
    )
  })

  it('存在未保存更改时切换模式被阻止并提示', async () => {
    const user = userEvent.setup()
    await renderBotPage()

    await user.click(screen.getByText('change-personality'))
    await user.click(screen.getByRole('tab', { name: '源文件' }))

    expect(toastMock).toHaveBeenCalledWith({
      variant: 'destructive',
      title: '切换失败',
      description: '请先保存当前更改',
    })
    // 仍停留在核心设置模式
    expect(screen.getByTestId('core-settings')).toBeInTheDocument()
    expect(screen.queryByPlaceholderText('TOML 配置内容')).not.toBeInTheDocument()
  })

  it('刷新按钮重新读取配置并提示已刷新', async () => {
    const user = userEvent.setup()
    await renderBotPage()

    await user.click(screen.getByRole('button', { name: '刷新' }))
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith({
        title: '已刷新',
        description: '已从 bot_config.toml 重新读取配置',
      })
    )
    expect(configApi.getBotConfigCached).toHaveBeenCalledTimes(2)
  })

  describe('源文件模式', () => {
    it('切换后加载原始 TOML，并把双引号字符串内的转义换行展开为真实换行', async () => {
      const user = userEvent.setup()
      await renderBotPage()

      await user.click(screen.getByRole('tab', { name: '源文件' }))
      const editor = await screen.findByPlaceholderText('TOML 配置内容')
      expect(configApi.getBotConfigRaw).toHaveBeenCalledTimes(1)
      // \n 转义序列被展开成真实换行
      expect(editor).toHaveValue('title = "hello\nworld"')
      // 文件模式提示默认可见
      expect(screen.getByText('文件模式：')).toBeInTheDocument()
    })

    it('编辑后保存：换行被转义回 \\n，保存成功后回读配置', async () => {
      const user = userEvent.setup()
      await renderBotPage()

      await user.click(screen.getByRole('tab', { name: '源文件' }))
      const editor = await screen.findByPlaceholderText('TOML 配置内容')
      // 双引号字符串内含真实换行，保存前应转义回 \n
      fireEvent.change(editor, { target: { value: 'title = "a\nb"' } })

      await user.click(await screen.findByRole('button', { name: '保存' }))
      await waitFor(() =>
        expect(configApi.updateBotConfigRaw).toHaveBeenCalledWith('title = "a\\nb"')
      )
      expect(toastMock).toHaveBeenCalledWith({ title: '保存成功', description: '配置已保存' })
      // 保存成功后重新加载可视化配置
      await waitFor(() => expect(configApi.getBotConfigCached).toHaveBeenCalledTimes(2))
    })

    it('TOML 语法错误被前端拦截：不发请求并展示错误信息', async () => {
      const user = userEvent.setup()
      await renderBotPage()

      await user.click(screen.getByRole('tab', { name: '源文件' }))
      const editor = await screen.findByPlaceholderText('TOML 配置内容')
      fireEvent.change(editor, { target: { value: 'a =' } })

      await user.click(await screen.findByRole('button', { name: '保存' }))
      await waitFor(() =>
        expect(toastMock).toHaveBeenCalledWith({
          variant: 'destructive',
          title: 'TOML 格式错误',
          description: expect.any(String),
        })
      )
      expect(configApi.updateBotConfigRaw).not.toHaveBeenCalled()
      // 错误面板展示翻译后的错误信息
      expect(await screen.findByText('⚠️ TOML 格式错误：')).toBeInTheDocument()
    })

    it('关闭文件模式提示后写入 localStorage 不再展示', async () => {
      const user = userEvent.setup()
      await renderBotPage()

      await user.click(screen.getByRole('tab', { name: '源文件' }))
      await screen.findByText('文件模式：')
      await user.click(screen.getByRole('button', { name: '关闭文件模式提示' }))

      expect(screen.queryByText('文件模式：')).not.toBeInTheDocument()
      expect(localStorage.getItem('bot-config-file-mode-notice-dismissed')).toBe('true')
    })
  })

  describe('详细设置模式', () => {
    it('按 schema 构建 tab 分组：uiOrder 排序、uiParent 归并、advanced 默认隐藏', async () => {
      localStorage.setItem('bot-config-tabs-guide-dismissed', 'true')
      const user = userEvent.setup()
      await renderBotPage()
      await enterDetailMode(user)

      // 模式切换时重新读取一次完整配置
      expect(configApi.getBotConfig).toHaveBeenCalledTimes(1)

      // tab 按 uiOrder 排序，advanced 的「实验性」默认隐藏
      const tabList = document.querySelector('[data-config-bot-tab-list="true"]') as HTMLElement
      const tabNames = within(tabList)
        .getAllByRole('tab')
        .map((tab) => tab.textContent)
      expect(tabNames).toEqual(['人格', '机器人'])

      // 「人格」tab 默认激活，表单仅包含自身分节
      expect(screen.getByTestId('form-personality-sections')).toHaveTextContent('personality')
      expect(screen.getByTestId('form-personality-values')).toHaveTextContent('原始人格')

      // uiParent 指向 bot 的 sub_feature 归并进「机器人」tab
      await user.click(within(tabList).getByRole('tab', { name: '机器人' }))
      expect(await screen.findByTestId('form-bot-sections')).toHaveTextContent('bot,sub_feature')

      // 点击「更多」后 advanced tab 出现
      await user.click(screen.getByRole('button', { name: '更多' }))
      expect(within(tabList).getByRole('tab', { name: '实验性' })).toBeInTheDocument()
    })

    it('表单修改更新分节值，防抖后按分节自动保存', async () => {
      localStorage.setItem('bot-config-tabs-guide-dismissed', 'true')
      const user = userEvent.setup()
      await renderBotPage()
      await enterDetailMode(user)

      await user.click(screen.getByText('change-personality-personality'))
      // 分节值立即更新并回传表单
      expect(screen.getByTestId('form-personality-values')).toHaveTextContent('personality-新值')
      // 出现未保存标记
      expect(screen.getByRole('button', { name: '保存' })).toBeInTheDocument()

      // 2 秒防抖后触发分区自动保存
      await waitFor(
        () =>
          expect(configApi.updateBotConfigSection).toHaveBeenCalledWith('personality', {
            personality: '原始人格',
            stub_field: 'personality-新值',
          }),
        { timeout: 4000 }
      )
      // 自动保存完成后回到「已保存」状态
      await screen.findByRole('button', { name: '已保存' })
    })

    it('「高级设置」按钮切换表单的 advancedVisible', async () => {
      localStorage.setItem('bot-config-tabs-guide-dismissed', 'true')
      const user = userEvent.setup()
      await renderBotPage()
      await enterDetailMode(user)

      expect(screen.getByTestId('form-personality')).toHaveAttribute('data-advanced', 'false')
      await user.click(screen.getByRole('button', { name: '高级设置' }))
      expect(screen.getByTestId('form-personality')).toHaveAttribute('data-advanced', 'true')
    })

    it('首次进入实验性 tab 弹出提示，确认后写入 localStorage', async () => {
      localStorage.setItem('bot-config-tabs-guide-dismissed', 'true')
      const user = userEvent.setup()
      await renderBotPage()
      await enterDetailMode(user)

      await user.click(screen.getByRole('button', { name: '更多' }))
      await user.click(screen.getByRole('tab', { name: '实验性' }))

      // 实验性功能提示对话框
      expect(await screen.findByText('实验性功能')).toBeInTheDocument()
      await user.click(screen.getByRole('button', { name: '我知道了' }))

      await waitFor(() => expect(screen.queryByText('实验性功能')).not.toBeInTheDocument())
      expect(localStorage.getItem('bot-config-experimental-features-notice-dismissed')).toBe('true')
      // tab 内容为实验性分节表单
      expect(screen.getByTestId('form-experimental')).toBeInTheDocument()
    })

    it('tab 使用引导可通过「我知道了」关闭并记忆', async () => {
      const user = userEvent.setup()
      await renderBotPage()
      await enterDetailMode(user)

      expect(screen.getByText(/展开隐藏配置栏目/)).toBeInTheDocument()
      await user.click(screen.getByRole('button', { name: '我知道了' }))

      expect(screen.queryByText(/展开隐藏配置栏目/)).not.toBeInTheDocument()
      expect(localStorage.getItem('bot-config-tabs-guide-dismissed')).toBe('true')
    })
  })

  it('URL 携带 field 搜索参数时自动切到详细设置并激活目标 tab（含高级展开）', async () => {
    routerSearchStr = '?field=experimental.debug'
    localStorage.setItem('bot-config-tabs-guide-dismissed', 'true')
    await renderBotPage()

    // 经 requestAnimationFrame 链自动切换：detail 模式 + experimental tab + 高级可见
    const experimentalForm = await screen.findByTestId('form-experimental')
    expect(experimentalForm).toHaveAttribute('data-advanced', 'true')
    expect(screen.getByTestId('form-experimental-values')).toHaveTextContent('debug')
  })
})
