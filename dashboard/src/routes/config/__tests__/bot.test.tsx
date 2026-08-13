import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { BotConfigPage } from '../bot'
import { scrollToConfigSearchField } from '@/lib/config-search-navigation'
import { fieldHooks } from '@/lib/field-hooks'
import * as configApi from '@/lib/config-api'

import type { ConfigSchema, FieldSchema } from '@/types/config-schema'
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

// CommandPermissions 桩：只验证页面把 plugin 分节传入并回写
vi.mock('../bot/CommandPermissions', () => ({
  CommandPermissions: ({
    pluginSection,
    onChange,
  }: {
    pluginSection: Record<string, unknown> | null
    onChange: (value: Record<string, unknown>) => void
  }) => (
    <div data-testid="command-permissions">
      <span data-testid="command-plugin">{JSON.stringify(pluginSection)}</span>
      <button
        type="button"
        onClick={() =>
          onChange({
            ...(pluginSection ?? {}),
            command_permissions: { demo: { allow_chats: [], allow_users: [] } },
          })
        }
      >
        change-plugin
      </button>
    </div>
  ),
}))

vi.mock('@/lib/config-search-navigation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/config-search-navigation')>()
  return {
    ...actual,
    // mockReset 会清掉 factory 默认实现，beforeEach 会重新挂上
    scrollToConfigSearchField: vi.fn(),
  }
})

// DynamicConfigForm 桩：展示 schema 组织形式与传入的值，并提供浅/深/整节替换回调
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
    <div
      data-testid={`form-${schema.className}`}
      data-advanced={String(Boolean(advancedVisible))}
      data-config-field-path={schema.className}
    >
      <span data-testid={`form-${schema.className}-sections`}>
        {Object.keys(schema.nested ?? {}).join(',')}
      </span>
      <span data-testid={`form-${schema.className}-values`}>{JSON.stringify(values)}</span>
      {Object.keys(schema.nested ?? {}).map((sectionName) => (
        <div key={sectionName}>
          <button
            type="button"
            onClick={() => onChange(`${sectionName}.stub_field`, `${sectionName}-新值`)}
          >
            {`change-${schema.className}-${sectionName}`}
          </button>
          <button
            type="button"
            onClick={() => onChange(`${sectionName}.nested.deep`, 'deep-val')}
          >
            {`change-deep-${schema.className}-${sectionName}`}
          </button>
          <button type="button" onClick={() => onChange(`${sectionName}.nested`, ['arr'])}>
            {`change-array-${schema.className}-${sectionName}`}
          </button>
          <button
            type="button"
            onClick={() => onChange(`${sectionName}.nested.deep`, 'from-array')}
          >
            {`change-through-array-${schema.className}-${sectionName}`}
          </button>
          <button type="button" onClick={() => onChange(sectionName, { replaced: true })}>
            {`change-replace-${schema.className}-${sectionName}`}
          </button>
          <button type="button" onClick={() => onChange(`${sectionName}.`, 'blank-key')}>
            {`change-blank-${schema.className}-${sectionName}`}
          </button>
        </div>
      ))}
      <button type="button" onClick={() => onChange('root_field', 'root-新值')}>
        {`change-root-${schema.className}`}
      </button>
      <button type="button" onClick={() => onChange('nested.deep', 'deep-新值')}>
        {`change-deep-root-${schema.className}`}
      </button>
      <button type="button" onClick={() => onChange('', 'empty-path')}>
        {`change-empty-${schema.className}`}
      </button>
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

function field(name: string, type: FieldSchema['type'], extra: Partial<FieldSchema> = {}): FieldSchema {
  return {
    name,
    type,
    label: name,
    description: '',
    required: false,
    ...extra,
  }
}

function createDeferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

/** 页面挂载时应注册的字段 hook 路径与类型（replace 为缺省） */
const EXPECTED_FIELD_HOOKS: Array<[string, 'replace' | 'wrapper' | 'hidden']> = [
  ['bot.platform', 'replace'],
  ['bot.alias_names', 'replace'],
  ['bot.qq_account', 'hidden'],
  ['bot.platforms', 'hidden'],
  ['personality.multiple_reply_style', 'replace'],
  ['chat.reply_style.chat_prompts', 'replace'],
  ['chat.reply_timing.talk_value_rules', 'replace'],
  ['experimental.focus_chat_whitelist', 'replace'],
  ['experimental.focus_groups', 'replace'],
  ['experimental.behavior_groups', 'replace'],
  ['experimental.behavior_learning_list', 'replace'],
  ['expression.expression_groups', 'replace'],
  ['expression.learning_list', 'replace'],
  ['jargon.jargon_groups', 'replace'],
  ['jargon.learning_list', 'replace'],
  ['a_memorix.global_memory_sharing_enabled', 'hidden'],
  ['a_memorix.shared_memory_groups', 'replace'],
  ['a_memorix.filter.chats', 'replace'],
  ['a_memorix.filter.retrieval', 'wrapper'],
  ['a_memorix.filter.retrieval.chat_stream.chats', 'replace'],
  ['a_memorix.filter.retrieval.chat_summary.chats', 'replace'],
  ['a_memorix.filter.retrieval.episode.chats', 'replace'],
  ['keyword_reaction.keyword_rules', 'replace'],
  ['keyword_reaction.regex_rules', 'replace'],
  ['mcp.client.roots.items', 'replace'],
  ['mcp.servers', 'replace'],
]

function chatSubtabSchema(): { schema: ConfigSchema } {
  return {
    schema: {
      className: 'BotConfigRoot',
      classDoc: '',
      fields: [],
      nested: {
        chat: {
          className: 'ChatSection',
          classDoc: '聊天配置',
          fields: [
            field('enabled', 'boolean'),
            field('reply_timing', 'object'),
            field('reply_style', 'object'),
            field('advanced_box', 'object'),
          ],
          nested: {
            reply_timing: {
              className: 'ReplyTiming',
              classDoc: '回复时机文档',
              fields: [],
              nested: {},
              uiLabel: '回复时机',
              uiSubLabel: '时机子页',
            },
            reply_style: {
              className: 'ReplyStyle',
              classDoc: '',
              fields: [],
              nested: {},
              uiLabel: '回复风格',
            },
            advanced_box: {
              className: 'AdvancedBox',
              classDoc: '高级文档',
              fields: [],
              nested: {},
              uiAdvanced: true,
            },
          },
          uiLabel: '聊天',
          uiOrder: 1,
          uiUseSubTabs: true,
          uiRootSubLabel: '总览',
        },
        chat_inner: sectionSchema('ChatInner', '', {
          uiParent: 'chat',
          uiLabel: '内部组',
          uiAdvanced: true,
        }),
        chat_leaf: sectionSchema('ChatLeaf', '叶子文档', { uiParent: 'chat_inner' }),
        personality: sectionSchema('PersonalitySection', '人格配置', {
          uiLabel: '人格',
          uiOrder: 2,
        }),
        empty_sub: sectionSchema('EmptySub', '空', {
          uiLabel: '空栏目',
          uiOrder: 9,
          uiUseSubTabs: true,
        }),
      },
    },
  }
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
  fieldHooks.clear()
  vi.mocked(scrollToConfigSearchField).mockImplementation(() => document.createElement('div'))
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
  const view = render(<BotConfigPage />)
  // 等待初始加载完成（模式切换 tab 出现）
  await screen.findByRole('tab', { name: '核心设置' })
  return view
}

/** 切换到「详细设置」模式并等待指定分节表单渲染完成 */
async function enterDetailMode(
  user: ReturnType<typeof userEvent.setup>,
  formTestId = 'form-personality'
) {
  await user.click(screen.getByRole('tab', { name: '详细设置' }))
  await screen.findByTestId(formTestId)
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
    await waitFor(() =>
      expect(scrollToConfigSearchField).toHaveBeenCalledWith('experimental.debug')
    )
  })

  describe('字段 hook 挂载', () => {
    it('挂载时按路径注册 hook，并带上 replace/hidden/wrapper 类型', async () => {
      await renderBotPage()

      expect(fieldHooks.getAllPaths().sort()).toEqual(
        EXPECTED_FIELD_HOOKS.map(([path]) => path).sort()
      )
      for (const [fieldPath, hookType] of EXPECTED_FIELD_HOOKS) {
        expect(fieldHooks.get(fieldPath)?.type).toBe(hookType)
      }
    })

    it('卸载时注销全部字段 hook', async () => {
      const { unmount } = await renderBotPage()
      expect(fieldHooks.has('bot.platform')).toBe(true)
      expect(fieldHooks.has('a_memorix.filter.retrieval')).toBe(true)

      unmount()

      expect(fieldHooks.getAllPaths()).toEqual([])
    })
  })

  describe('加载与保存错误', () => {
    it('配置未返回前展示加载中状态', async () => {
      const deferred = createDeferred<Record<string, unknown>>()
      vi.mocked(configApi.getBotConfigCached).mockReturnValue(deferred.promise)

      render(<BotConfigPage />)
      expect(screen.getByRole('status', { name: '加载中' })).toBeInTheDocument()

      deferred.resolve(baseConfig())
      await screen.findByRole('tab', { name: '核心设置' })
      expect(screen.queryByRole('status', { name: '加载中' })).not.toBeInTheDocument()
    })

    it('初始加载失败且原因不是 Error 时使用兜底文案', async () => {
      vi.mocked(configApi.getBotConfigCached).mockRejectedValue('offline')
      render(<BotConfigPage />)
      await waitFor(() =>
        expect(toastMock).toHaveBeenCalledWith({
          title: '加载失败',
          description: '加载配置失败',
          variant: 'destructive',
        })
      )
    })

    it('schema 加载失败时仍可进入详细设置，但不渲染分节 tab', async () => {
      vi.mocked(configApi.getBotConfigSchema).mockRejectedValue(new Error('schema 不可用'))
      const user = userEvent.setup()
      await renderBotPage()

      await user.click(screen.getByRole('tab', { name: '详细设置' }))
      await waitFor(() => expect(configApi.getBotConfig).toHaveBeenCalledTimes(1))

      expect(document.querySelector('[data-config-bot-tab-list="true"]')).toBeNull()
      expect(screen.queryByTestId(/form-/)).not.toBeInTheDocument()
    })

    it('schema 返回空值时详细设置不渲染分组', async () => {
      vi.mocked(configApi.getBotConfigSchema).mockResolvedValue(null as never)
      const user = userEvent.setup()
      await renderBotPage()

      await user.click(screen.getByRole('tab', { name: '详细设置' }))
      await waitFor(() => expect(configApi.getBotConfig).toHaveBeenCalledTimes(1))
      expect(document.querySelector('[data-config-bot-tab-list="true"]')).toBeNull()
    })

    it('schema 缺少 nested 时详细设置不渲染分组', async () => {
      vi.mocked(configApi.getBotConfigSchema).mockResolvedValue({
        schema: { className: 'EmptyRoot', classDoc: '', fields: [] },
      } as never)
      const user = userEvent.setup()
      await renderBotPage()

      await user.click(screen.getByRole('tab', { name: '详细设置' }))
      await waitFor(() => expect(configApi.getBotConfig).toHaveBeenCalledTimes(1))
      expect(document.querySelector('[data-config-bot-tab-list="true"]')).toBeNull()
    })

    it('命令管理模式挂载命令权限；plugin 缺失时传入 null，回写后可保存', async () => {
      const user = userEvent.setup()
      await renderBotPage()

      await user.click(screen.getByRole('tab', { name: '命令管理' }))
      expect(await screen.findByTestId('command-plugin')).toHaveTextContent('null')

      await user.click(screen.getByText('change-plugin'))
      await user.click(await screen.findByRole('button', { name: '保存' }))

      await waitFor(() => expect(configApi.updateBotConfig).toHaveBeenCalledTimes(1))
      const savedConfig = vi.mocked(configApi.updateBotConfig).mock.calls[0][0] as Record<
        string,
        unknown
      >
      expect(savedConfig.plugin).toEqual({
        command_permissions: { demo: { allow_chats: [], allow_users: [] } },
      })
    })

    it('命令管理模式把已有 plugin 分节传给命令权限面板', async () => {
      const configWithPlugin = {
        ...baseConfig(),
        plugin: { permission: ['qq:1'], command_permissions: {} },
      }
      vi.mocked(configApi.getBotConfigCached).mockResolvedValue(configWithPlugin)
      vi.mocked(configApi.getBotConfig).mockResolvedValue(configWithPlugin)
      const user = userEvent.setup()
      await renderBotPage()

      await user.click(screen.getByRole('tab', { name: '命令管理' }))
      expect(await screen.findByTestId('command-plugin')).toHaveTextContent('qq:1')
    })

    it('切到可视化模式时 getBotConfig 失败会提示无法加载配置文件', async () => {
      vi.mocked(configApi.getBotConfig).mockRejectedValue(new Error('可视化失败'))
      const user = userEvent.setup()
      await renderBotPage()

      await user.click(screen.getByRole('tab', { name: '详细设置' }))
      await waitFor(() =>
        expect(toastMock).toHaveBeenCalledWith({
          title: '加载失败',
          description: '无法加载配置文件',
          variant: 'destructive',
        })
      )
    })

    it('切到源文件模式时加载原始配置失败（Error）', async () => {
      vi.mocked(configApi.getBotConfigRaw).mockRejectedValue(new Error('源码失败'))
      const user = userEvent.setup()
      await renderBotPage()

      await user.click(screen.getByRole('tab', { name: '源文件' }))
      await waitFor(() =>
        expect(toastMock).toHaveBeenCalledWith({
          variant: 'destructive',
          title: '加载失败',
          description: '源码失败',
        })
      )
    })

    it('切到源文件模式时加载失败且原因不是 Error 时使用兜底文案', async () => {
      vi.mocked(configApi.getBotConfigRaw).mockRejectedValue('raw-down')
      const user = userEvent.setup()
      await renderBotPage()

      await user.click(screen.getByRole('tab', { name: '源文件' }))
      await waitFor(() =>
        expect(toastMock).toHaveBeenCalledWith({
          variant: 'destructive',
          title: '加载失败',
          description: '加载源代码失败',
        })
      )
    })

    it('刷新失败时只提示加载失败，不提示已刷新', async () => {
      vi.mocked(configApi.getBotConfigCached)
        .mockResolvedValueOnce(baseConfig())
        .mockRejectedValueOnce(new Error('刷新失败'))
      const user = userEvent.setup()
      await renderBotPage()

      await user.click(screen.getByRole('button', { name: '刷新' }))
      await waitFor(() =>
        expect(toastMock).toHaveBeenCalledWith({
          title: '加载失败',
          description: '刷新失败',
          variant: 'destructive',
        })
      )
      expect(toastMock).not.toHaveBeenCalledWith({
        title: '已刷新',
        description: '已从 bot_config.toml 重新读取配置',
      })
    })

    it('源文件模式下刷新会同时回读可视化配置和原始 TOML', async () => {
      const user = userEvent.setup()
      await renderBotPage()

      await user.click(screen.getByRole('tab', { name: '源文件' }))
      await screen.findByPlaceholderText('TOML 配置内容')
      await user.click(screen.getByRole('button', { name: '刷新' }))

      await waitFor(() =>
        expect(toastMock).toHaveBeenCalledWith({
          title: '已刷新',
          description: '已从 bot_config.toml 重新读取配置',
        })
      )
      expect(configApi.getBotConfigCached).toHaveBeenCalledTimes(2)
      expect(configApi.getBotConfigRaw).toHaveBeenCalledTimes(2)
    })

    it('源文件模式刷新时原始 TOML 再读失败仍提示已刷新', async () => {
      vi.mocked(configApi.getBotConfigRaw)
        .mockResolvedValueOnce({ content: 'title = "ok"' } as never)
        .mockRejectedValueOnce(new Error('二次失败'))
      const user = userEvent.setup()
      await renderBotPage()

      await user.click(screen.getByRole('tab', { name: '源文件' }))
      await screen.findByPlaceholderText('TOML 配置内容')
      toastMock.mockClear()
      await user.click(screen.getByRole('button', { name: '刷新' }))

      await waitFor(() =>
        expect(toastMock).toHaveBeenCalledWith({
          variant: 'destructive',
          title: '加载失败',
          description: '二次失败',
        })
      )
      expect(toastMock).toHaveBeenCalledWith({
        title: '已刷新',
        description: '已从 bot_config.toml 重新读取配置',
      })
    })

    it('源文件保存接口失败时展示保存失败并写入错误面板', async () => {
      vi.mocked(configApi.updateBotConfigRaw).mockRejectedValue(new Error('磁盘满'))
      const user = userEvent.setup()
      await renderBotPage()

      await user.click(screen.getByRole('tab', { name: '源文件' }))
      const editor = await screen.findByPlaceholderText('TOML 配置内容')
      fireEvent.change(editor, { target: { value: 'title = "ok"' } })
      await user.click(await screen.findByRole('button', { name: '保存' }))

      await waitFor(() =>
        expect(toastMock).toHaveBeenCalledWith({
          variant: 'destructive',
          title: '保存失败',
          description: '磁盘满',
        })
      )
      expect(await screen.findByText('磁盘满')).toBeInTheDocument()
    })

    it('源文件保存接口抛出非 Error 时使用兜底文案', async () => {
      vi.mocked(configApi.updateBotConfigRaw).mockRejectedValue('write-fail')
      const user = userEvent.setup()
      await renderBotPage()

      await user.click(screen.getByRole('tab', { name: '源文件' }))
      const editor = await screen.findByPlaceholderText('TOML 配置内容')
      fireEvent.change(editor, { target: { value: 'title = "ok"' } })
      await user.click(await screen.findByRole('button', { name: '保存' }))

      await waitFor(() =>
        expect(toastMock).toHaveBeenCalledWith({
          variant: 'destructive',
          title: '保存失败',
          description: '保存配置失败',
        })
      )
    })

    it('编辑源码会清掉先前的 TOML 错误面板', async () => {
      const user = userEvent.setup()
      await renderBotPage()

      await user.click(screen.getByRole('tab', { name: '源文件' }))
      const editor = await screen.findByPlaceholderText('TOML 配置内容')
      fireEvent.change(editor, { target: { value: 'a =' } })
      await user.click(await screen.findByRole('button', { name: '保存' }))
      expect(await screen.findByText('⚠️ TOML 格式错误：')).toBeInTheDocument()

      fireEvent.change(editor, { target: { value: 'title = "fixed"' } })
      expect(screen.queryByText('⚠️ TOML 格式错误：')).not.toBeInTheDocument()
    })

    it('加载时展开双引号内的 \\t/\\\\，保存时再转回转义序列', async () => {
      // jsdom textarea 会把 \\r 归一成换行，这里只锁定 tab 与反斜杠
      vi.mocked(configApi.getBotConfigRaw).mockResolvedValue({
        content: 'msg = "a\\tb\\\\e"',
      } as never)
      const user = userEvent.setup()
      await renderBotPage()

      await user.click(screen.getByRole('tab', { name: '源文件' }))
      const editor = await screen.findByPlaceholderText('TOML 配置内容')
      expect(editor).toHaveValue('msg = "a\tb\\e"')

      // 必须改成不同内容，相同值不会把保存按钮切到「保存」
      fireEvent.change(editor, { target: { value: 'msg = "z\tb\\e"' } })
      await user.click(await screen.findByRole('button', { name: '保存' }))
      await waitFor(() =>
        expect(configApi.updateBotConfigRaw).toHaveBeenCalledWith('msg = "z\\tb\\\\e"')
      )
    })

    it('手动保存进行中时按钮显示保存中', async () => {
      const deferred = createDeferred<Record<string, unknown>>()
      vi.mocked(configApi.updateBotConfig).mockReturnValue(deferred.promise)
      const user = userEvent.setup()
      await renderBotPage()

      await user.click(screen.getByText('change-personality'))
      await user.click(await screen.findByRole('button', { name: '保存' }))

      expect(await screen.findByRole('button', { name: '保存中' })).toBeDisabled()
      deferred.resolve({})
      await waitFor(() =>
        expect(toastMock).toHaveBeenCalledWith({
          title: '保存成功',
          description: '麦麦设置已保存',
        })
      )
    })

    it('从源文件切回核心设置会重新拉取可视化配置', async () => {
      const user = userEvent.setup()
      await renderBotPage()

      await user.click(screen.getByRole('tab', { name: '源文件' }))
      await screen.findByPlaceholderText('TOML 配置内容')
      await user.click(screen.getByRole('tab', { name: '核心设置' }))

      await waitFor(() => expect(configApi.getBotConfig).toHaveBeenCalledTimes(1))
      expect(await screen.findByTestId('core-settings')).toBeInTheDocument()
      expect(screen.queryByPlaceholderText('TOML 配置内容')).not.toBeInTheDocument()
    })

    it('源文件保存成功但回读配置失败时仍提示保存成功并再提示加载失败', async () => {
      vi.mocked(configApi.getBotConfigCached)
        .mockResolvedValueOnce(baseConfig())
        .mockRejectedValueOnce(new Error('回读失败'))
      const user = userEvent.setup()
      await renderBotPage()

      await user.click(screen.getByRole('tab', { name: '源文件' }))
      const editor = await screen.findByPlaceholderText('TOML 配置内容')
      fireEvent.change(editor, { target: { value: 'title = "ok"' } })
      await user.click(await screen.findByRole('button', { name: '保存' }))

      await waitFor(() =>
        expect(toastMock).toHaveBeenCalledWith({ title: '保存成功', description: '配置已保存' })
      )
      await waitFor(() =>
        expect(toastMock).toHaveBeenCalledWith({
          title: '加载失败',
          description: '回读失败',
          variant: 'destructive',
        })
      )
    })

    it('非法键名会被翻译成中文 TOML 错误', async () => {
      const user = userEvent.setup()
      await renderBotPage()

      await user.click(screen.getByRole('tab', { name: '源文件' }))
      const editor = await screen.findByPlaceholderText('TOML 配置内容')
      fireEvent.change(editor, { target: { value: 'foo bar = 1' } })
      await user.click(await screen.findByRole('button', { name: '保存' }))

      // 翻译后仍保留 smol-toml 的多行指针，所以只匹配中文主句
      expect(
        await screen.findByText(/键名只能包含字母、数字、短横线和下划线/)
      ).toBeInTheDocument()
      expect(configApi.updateBotConfigRaw).not.toHaveBeenCalled()
    })
  })

  describe('详细设置分节导航', () => {
    it('收起更多时若当前是高级 tab 则回到第一个普通 tab', async () => {
      localStorage.setItem('bot-config-tabs-guide-dismissed', 'true')
      localStorage.setItem('bot-config-experimental-features-notice-dismissed', 'true')
      const user = userEvent.setup()
      await renderBotPage()
      await enterDetailMode(user)

      const tabList = document.querySelector('[data-config-bot-tab-list="true"]') as HTMLElement
      await user.click(within(tabList).getByRole('button', { name: '更多' }))
      await user.click(within(tabList).getByRole('tab', { name: '实验性' }))
      expect(await screen.findByTestId('form-experimental')).toBeInTheDocument()

      await user.click(within(tabList).getByRole('button', { name: '收起' }))
      expect(within(tabList).queryByRole('tab', { name: '实验性' })).not.toBeInTheDocument()
      expect(await screen.findByTestId('form-personality')).toBeInTheDocument()
    })

    it('收起更多时若当前是普通 tab 则保持该 tab', async () => {
      localStorage.setItem('bot-config-tabs-guide-dismissed', 'true')
      const user = userEvent.setup()
      await renderBotPage()
      await enterDetailMode(user)

      const tabList = document.querySelector('[data-config-bot-tab-list="true"]') as HTMLElement
      await user.click(within(tabList).getByRole('tab', { name: '机器人' }))
      expect(await screen.findByTestId('form-bot')).toBeInTheDocument()

      await user.click(within(tabList).getByRole('button', { name: '更多' }))
      expect(within(tabList).getByRole('tab', { name: '实验性' })).toBeInTheDocument()
      await user.click(within(tabList).getByRole('button', { name: '收起' }))

      expect(within(tabList).queryByRole('tab', { name: '实验性' })).not.toBeInTheDocument()
      expect(screen.getByTestId('form-bot')).toBeInTheDocument()
    })

    it('没有高级 tab 时不渲染更多按钮', async () => {
      vi.mocked(configApi.getBotConfigSchema).mockResolvedValue({
        schema: {
          className: 'BotConfigRoot',
          classDoc: '',
          fields: [],
          nested: {
            personality: sectionSchema('PersonalitySection', '人格配置', {
              uiLabel: '人格',
              uiOrder: 1,
            }),
          },
        },
      } as never)
      localStorage.setItem('bot-config-tabs-guide-dismissed', 'true')
      const user = userEvent.setup()
      await renderBotPage()
      await enterDetailMode(user)

      expect(screen.queryByRole('button', { name: '更多' })).not.toBeInTheDocument()
    })

    it('第一个 tab 就是实验性时进入详细设置即弹出提示', async () => {
      vi.mocked(configApi.getBotConfigSchema).mockResolvedValue({
        schema: {
          className: 'BotConfigRoot',
          classDoc: '',
          fields: [],
          nested: {
            experimental: sectionSchema('ExperimentalSection', '实验性配置', {
              uiLabel: '实验性',
              uiOrder: 0,
              uiAdvanced: true,
            }),
            personality: sectionSchema('PersonalitySection', '人格配置', {
              uiLabel: '人格',
              uiOrder: 1,
            }),
          },
        },
      } as never)
      localStorage.setItem('bot-config-tabs-guide-dismissed', 'true')
      const user = userEvent.setup()
      await renderBotPage()

      await user.click(screen.getByRole('tab', { name: '详细设置' }))
      expect(await screen.findByText('实验性功能')).toBeInTheDocument()
    })

    it('已关闭实验性提示后再进入该 tab 不再弹出', async () => {
      localStorage.setItem('bot-config-tabs-guide-dismissed', 'true')
      localStorage.setItem('bot-config-experimental-features-notice-dismissed', 'true')
      const user = userEvent.setup()
      await renderBotPage()
      await enterDetailMode(user)

      await user.click(screen.getByRole('button', { name: '更多' }))
      await user.click(screen.getByRole('tab', { name: '实验性' }))

      expect(screen.queryByText('实验性功能')).not.toBeInTheDocument()
      expect(await screen.findByTestId('form-experimental')).toBeInTheDocument()
    })

    it('Escape 关闭实验性提示并写入 localStorage', async () => {
      localStorage.setItem('bot-config-tabs-guide-dismissed', 'true')
      const user = userEvent.setup()
      await renderBotPage()
      await enterDetailMode(user)

      await user.click(screen.getByRole('button', { name: '更多' }))
      await user.click(screen.getByRole('tab', { name: '实验性' }))
      expect(await screen.findByText('实验性功能')).toBeInTheDocument()

      await user.keyboard('{Escape}')
      await waitFor(() => expect(screen.queryByText('实验性功能')).not.toBeInTheDocument())
      expect(localStorage.getItem('bot-config-experimental-features-notice-dismissed')).toBe('true')
    })

    it('全部为高级 tab 时默认只显示更多，展开后才能切换', async () => {
      vi.mocked(configApi.getBotConfigSchema).mockResolvedValue({
        schema: {
          className: 'BotConfigRoot',
          classDoc: '',
          fields: [],
          nested: {
            experimental: sectionSchema('ExperimentalSection', '实验性配置', {
              uiLabel: '实验性',
              uiOrder: 1,
              uiAdvanced: true,
            }),
          },
        },
      } as never)
      localStorage.setItem('bot-config-tabs-guide-dismissed', 'true')
      localStorage.setItem('bot-config-experimental-features-notice-dismissed', 'true')
      const user = userEvent.setup()
      await renderBotPage()

      await user.click(screen.getByRole('tab', { name: '详细设置' }))
      const tabList = await waitFor(() => {
        const node = document.querySelector('[data-config-bot-tab-list="true"]') as HTMLElement | null
        if (!node) throw new Error('missing tab list')
        return node
      })
      expect(within(tabList).queryByRole('tab', { name: '实验性' })).not.toBeInTheDocument()
      await user.click(within(tabList).getByRole('button', { name: '更多' }))
      expect(within(tabList).getByRole('tab', { name: '实验性' })).toBeInTheDocument()
    })

    it('uiUseSubTabs 按根字段/子类/高级子页拆分，并展示聊天管理提示', async () => {
      const config = {
        ...baseConfig(),
        chat: { enabled: true, reply_timing: { talk_value: 1 }, reply_style: { style: 'a' } },
      }
      vi.mocked(configApi.getBotConfigCached).mockResolvedValue(config)
      vi.mocked(configApi.getBotConfig).mockResolvedValue(config)
      vi.mocked(configApi.getBotConfigSchema).mockResolvedValue(chatSubtabSchema() as never)
      localStorage.setItem('bot-config-tabs-guide-dismissed', 'true')
      const user = userEvent.setup()
      await renderBotPage()
      await enterDetailMode(user, 'form-ChatSectionRoot')

      const tabList = document.querySelector('[data-config-bot-tab-list="true"]') as HTMLElement
      expect(within(tabList).getByRole('tab', { name: '聊天' })).toBeInTheDocument()
      await user.click(within(tabList).getByRole('tab', { name: '空栏目' }))
      expect(screen.queryByTestId('form-EmptySub')).not.toBeInTheDocument()

      await user.click(within(tabList).getByRole('tab', { name: '聊天' }))
      const subtabList = document.querySelector('[data-config-bot-subtab-list="true"]') as HTMLElement
      const defaultSubtabNames = within(subtabList)
        .getAllByRole('tab')
        .map((tab) => tab.textContent)
      expect(defaultSubtabNames).toEqual(['总览', '时机子页', '回复风格'])

      await user.click(within(subtabList).getByRole('tab', { name: '时机子页' }))
      expect(
        await screen.findByText(/需要按具体聊天流调整发言频率或查看聊天 Prompt/)
      ).toBeInTheDocument()
      expect(screen.getByText('聊天管理')).toBeInTheDocument()

      await user.click(within(subtabList).getByRole('button', { name: '更多' }))
      expect(within(subtabList).getByRole('tab', { name: '高级文档' })).toBeInTheDocument()
      expect(within(subtabList).getByRole('tab', { name: '内部组' })).toBeInTheDocument()

      await user.click(within(subtabList).getByRole('tab', { name: '内部组' }))
      expect(await screen.findByTestId('form-chat_inner.chat_leaf-sections')).toHaveTextContent(
        'chat_inner,chat_leaf'
      )

      await user.click(within(subtabList).getByRole('button', { name: '收起' }))
      expect(within(subtabList).queryByRole('tab', { name: '内部组' })).not.toBeInTheDocument()
      expect(await screen.findByTestId('form-ChatSectionRoot')).toBeInTheDocument()
    })

    it('子页表单修改会写入嵌套路径，空 fieldPath 被忽略', async () => {
      const config = {
        ...baseConfig(),
        chat: { enabled: true, reply_timing: {}, reply_style: {} },
      }
      vi.mocked(configApi.getBotConfigCached).mockResolvedValue(config)
      vi.mocked(configApi.getBotConfig).mockResolvedValue(config)
      vi.mocked(configApi.getBotConfigSchema).mockResolvedValue(chatSubtabSchema() as never)
      localStorage.setItem('bot-config-tabs-guide-dismissed', 'true')
      const user = userEvent.setup()
      await renderBotPage()
      await enterDetailMode(user, 'form-ChatSectionRoot')

      await user.click(screen.getByText('change-root-ChatSectionRoot'))
      expect(screen.getByTestId('form-ChatSectionRoot-values')).toHaveTextContent('root-新值')

      await user.click(screen.getByText('change-deep-root-ChatSectionRoot'))
      expect(screen.getByTestId('form-ChatSectionRoot-values')).toHaveTextContent('deep-新值')

      const beforeEmpty = screen.getByTestId('form-ChatSectionRoot-values').textContent
      await user.click(screen.getByText('change-empty-ChatSectionRoot'))
      expect(screen.getByTestId('form-ChatSectionRoot-values').textContent).toBe(beforeEmpty)
    })

    it('普通 tab 的深层/整节/穿越数组更新会写回分节值', async () => {
      localStorage.setItem('bot-config-tabs-guide-dismissed', 'true')
      const user = userEvent.setup()
      await renderBotPage()
      await enterDetailMode(user)

      await user.click(screen.getByText('change-deep-personality-personality'))
      expect(screen.getByTestId('form-personality-values')).toHaveTextContent('deep-val')

      await user.click(screen.getByText('change-array-personality-personality'))
      expect(screen.getByTestId('form-personality-values')).toHaveTextContent('["arr"]')
      await user.click(screen.getByText('change-through-array-personality-personality'))
      expect(screen.getByTestId('form-personality-values')).toHaveTextContent('from-array')

      const beforeBlank = screen.getByTestId('form-personality-values').textContent
      await user.click(screen.getByText('change-blank-personality-personality'))
      expect(screen.getByTestId('form-personality-values').textContent).toBe(beforeBlank)

      await user.click(screen.getByText('change-replace-personality-personality'))
      expect(screen.getByTestId('form-personality-values')).toHaveTextContent('{"replaced":true}')

      await user.click(screen.getByText('change-empty-personality'))
      expect(screen.getByTestId('form-personality-values')).toHaveTextContent('{"replaced":true}')
    })

    it('URL field 指向子类时激活对应子页并滚动定位', async () => {
      const config = {
        ...baseConfig(),
        chat: { enabled: true, reply_timing: { talk_value: 1 }, reply_style: {} },
      }
      vi.mocked(configApi.getBotConfigCached).mockResolvedValue(config)
      vi.mocked(configApi.getBotConfig).mockResolvedValue(config)
      vi.mocked(configApi.getBotConfigSchema).mockResolvedValue(chatSubtabSchema() as never)
      routerSearchStr = '?field=chat.reply_timing'
      localStorage.setItem('bot-config-tabs-guide-dismissed', 'true')
      await renderBotPage()

      expect(await screen.findByTestId('form-ReplyTiming')).toHaveAttribute('data-advanced', 'true')
      await waitFor(() =>
        expect(scrollToConfigSearchField).toHaveBeenCalledWith('chat.reply_timing')
      )
    })

    it('URL field 指向未知分节时仍切到详细设置并停留在默认 tab', async () => {
      routerSearchStr = '?field=ghost.missing'
      localStorage.setItem('bot-config-tabs-guide-dismissed', 'true')
      await renderBotPage()

      expect(await screen.findByTestId('form-personality')).toBeInTheDocument()
      await waitFor(() =>
        expect(scrollToConfigSearchField).toHaveBeenCalledWith('ghost.missing')
      )
    })

    it('uiParent 成环的字段不会生成 tab，只保留可解析的 host', async () => {
      vi.mocked(configApi.getBotConfigSchema).mockResolvedValue({
        schema: {
          className: 'BotConfigRoot',
          classDoc: '',
          fields: [],
          nested: {
            solo: sectionSchema('SoloSection', '单独', { uiLabel: '单独', uiOrder: 0 }),
            loop_a: sectionSchema('LoopA', '环A', { uiLabel: '环A', uiParent: 'loop_b', uiOrder: 1 }),
            loop_b: sectionSchema('LoopB', '环B', { uiLabel: '环B', uiParent: 'loop_a', uiOrder: 2 }),
          },
        },
      } as never)
      localStorage.setItem('bot-config-tabs-guide-dismissed', 'true')
      const user = userEvent.setup()
      await renderBotPage()
      await enterDetailMode(user, 'form-solo')

      const tabList = document.querySelector('[data-config-bot-tab-list="true"]') as HTMLElement
      const tabNames = within(tabList)
        .getAllByRole('tab')
        .map((tab) => tab.textContent)
      expect(tabNames).toEqual(['单独'])
    })

    it('相同 uiOrder 的 tab 按中文 locale 标签排序', async () => {
      vi.mocked(configApi.getBotConfigSchema).mockResolvedValue({
        schema: {
          className: 'BotConfigRoot',
          classDoc: '',
          fields: [],
          nested: {
            zeta: sectionSchema('ZetaSection', 'Z', { uiLabel: 'zeta配置', uiOrder: 5 }),
            alpha: sectionSchema('AlphaSection', 'A', { uiLabel: 'alpha配置', uiOrder: 5 }),
          },
        },
      } as never)
      localStorage.setItem('bot-config-tabs-guide-dismissed', 'true')
      const user = userEvent.setup()
      await renderBotPage()
      await enterDetailMode(user, 'form-alpha')

      const tabList = document.querySelector('[data-config-bot-tab-list="true"]') as HTMLElement
      const tabNames = within(tabList)
        .getAllByRole('tab')
        .map((tab) => tab.textContent)
      expect(tabNames).toEqual(['alpha配置', 'zeta配置'])
    })
  })
})
