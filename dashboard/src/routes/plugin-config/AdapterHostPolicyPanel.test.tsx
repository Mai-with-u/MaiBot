import type { ReactNode } from 'react'

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { AdapterHostPolicyPanel, AUTOSAVE_DELAY_MS } from './AdapterHostPolicyPanel'
import type { AdapterHostPolicy, AdapterPolicyDefaults } from '@/lib/chat-management-api'
import { getAdapterHostPolicy, updateAdapterHostPolicy } from '@/lib/chat-management-api'

const { toastMock } = vi.hoisted(() => ({ toastMock: vi.fn() }))

vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: toastMock }) }))

vi.mock('@/lib/chat-management-api', () => ({
  getAllChatStreams: async () => [],
  getAdapterHostPolicy: vi.fn(),
  updateAdapterHostPolicy: vi.fn(),
}))

vi.mock('@/components/ListFieldEditor', () => ({
  ListFieldEditor: ({
    onChange,
    placeholder,
    value,
  }: {
    onChange?: (next: unknown[]) => void
    placeholder?: string
    value?: unknown[]
  }) => {
    const items = Array.isArray(value) ? value : []
    const label = placeholder ?? ''
    return (
      <div data-placeholder={label} data-testid={`list-field:${label}`}>
        <span data-testid={`list-value:${label}`}>{JSON.stringify(items)}</span>
        <button type="button" onClick={() => onChange?.([...items, 'new-item'])}>
          {`添加:${label}`}
        </button>
        <button type="button" onClick={() => onChange?.([123, true])}>
          {`设为混合类型:${label}`}
        </button>
        <button
          type="button"
          onClick={() => {
            items.push('mutated-in-place')
            onChange?.(items)
          }}
        >
          {`就地修改:${label}`}
        </button>
        <button type="button" onClick={() => onChange?.([])}>
          {`清空:${label}`}
        </button>
      </div>
    )
  },
}))

vi.mock('@/components/ui/select', async () => {
  const React = await import('react')
  const SelectContext = React.createContext<{
    value?: string
    onValueChange?: (value: string) => void
  }>({})

  function Select({
    value,
    onValueChange,
    children,
  }: {
    value?: string
    onValueChange?: (value: string) => void
    children?: ReactNode
  }) {
    return (
      <SelectContext.Provider value={{ value, onValueChange }}>
        <div data-testid="host-policy-select" data-value={value}>
          {children}
        </div>
      </SelectContext.Provider>
    )
  }

  function SelectTrigger({ children }: { children?: ReactNode }) {
    return <div>{children}</div>
  }

  function SelectValue() {
    const { value } = React.useContext(SelectContext)
    const labels: Record<string, string> = {
      inherit: '与全局设置一致',
      block: '默认不接收消息',
      allow: '接收所有消息',
    }
    return <span>{value ? (labels[value] ?? value) : null}</span>
  }

  function SelectContent({ children }: { children?: ReactNode }) {
    return <div>{children}</div>
  }

  function SelectItem({ value, children }: { value: string; children?: ReactNode }) {
    const { onValueChange } = React.useContext(SelectContext)
    return (
      <button type="button" data-value={value} onClick={() => onValueChange?.(value)}>
        {children}
      </button>
    )
  }

  return { Select, SelectContent, SelectItem, SelectTrigger, SelectValue }
})

function createDeferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, reject, resolve }
}

function makePolicy(overrides: Partial<{
  group: Partial<AdapterHostPolicy['group']>
  private: Partial<AdapterHostPolicy['private']>
}> = {}): AdapterHostPolicy {
  return {
    group: {
      default_action: 'inherit',
      allow_ids: [],
      deny_ids: [],
      ...overrides.group,
    },
    private: {
      default_action: 'inherit',
      allow_ids: [],
      deny_ids: [],
      ...overrides.private,
    },
  }
}

function makeResponse(
  pluginId = 'adapter.qq',
  overrides: {
    global_defaults?: AdapterPolicyDefaults
    policy?: AdapterHostPolicy
  } = {}
) {
  return {
    success: true,
    plugin_id: pluginId,
    active_identity: {
      adapter_id: `gateway:${pluginId}:gw`,
      plugin_id: pluginId,
      gateway_name: 'gw',
      platform: 'qq',
      account_id: '123456',
      scope: null,
    },
    has_entry: true,
    account_entries: [],
    global_defaults: overrides.global_defaults ?? { group: 'allow' as const, private: 'block' as const },
    policy: overrides.policy ?? makePolicy(),
  }
}

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
}

function renderPanel(pluginId = 'adapter.qq', queryClient = makeQueryClient()) {
  const view = render(
    <QueryClientProvider client={queryClient}>
      <AdapterHostPolicyPanel pluginId={pluginId} />
    </QueryClientProvider>
  )
  return { ...view, queryClient }
}

async function renderReadyPanel(
  pluginId = 'adapter.qq',
  response = makeResponse(pluginId),
  queryClient = makeQueryClient()
) {
  vi.mocked(getAdapterHostPolicy).mockResolvedValue(response as never)
  const view = renderPanel(pluginId, queryClient)
  expect(await screen.findByTestId('list-field:输入接收消息的群号')).toBeInTheDocument()
  return { ...view, response }
}

beforeEach(() => {
  vi.mocked(getAdapterHostPolicy).mockResolvedValue(makeResponse() as never)
  vi.mocked(updateAdapterHostPolicy).mockResolvedValue(makeResponse() as never)
})

afterEach(() => {
  vi.clearAllMocks()
})

describe('AdapterHostPolicyPanel', () => {
  it('复制包含未保存编辑，切换保留其他组，删除仅移除非当前组', async () => {
    const user = userEvent.setup()
    vi.mocked(updateAdapterHostPolicy).mockImplementation(async (pluginId, policy) =>
      makeResponse(pluginId, { policy }))
    await renderReadyPanel()

    await user.click(screen.getByRole('button', { name: '添加:输入接收消息的用户 ID' }))
    await user.click(screen.getByRole('button', { name: '复制当前组' }))
    await user.type(screen.getByLabelText('分组名称'), '测试组')
    await user.click(screen.getByRole('button', { name: '创建' }))
    await waitFor(() => expect(updateAdapterHostPolicy).toHaveBeenCalledTimes(1))
    const copied = vi.mocked(updateAdapterHostPolicy).mock.calls[0][1]
    expect(copied.active_group).toBe('default')
    expect(copied.policy_groups).toHaveLength(2)
    expect(copied.policy_groups?.[1].private.allow_ids).toEqual(['new-item'])

    await waitFor(() => expect(screen.getByRole('button', { name: '新建分组' })).toBeEnabled())
    await user.click(screen.getByRole('button', { name: '测试组' }))
    await waitFor(() => expect(updateAdapterHostPolicy).toHaveBeenCalledTimes(2))
    expect(vi.mocked(updateAdapterHostPolicy).mock.calls[1][1].active_group).toBe(copied.policy_groups?.[1].id)
    await waitFor(() => expect(screen.getByRole('button', { name: '新建分组' })).toBeEnabled())
    await user.click(screen.getByRole('button', { name: '管理分组' }))
    expect(screen.queryByRole('button', { name: '删除分组 测试组' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '删除分组 默认分组' }))
    await waitFor(() => expect(updateAdapterHostPolicy).toHaveBeenCalledTimes(3))
    expect(vi.mocked(updateAdapterHostPolicy).mock.calls[2][1].policy_groups).toHaveLength(1)
  })

  it('加载中展示转圈提示', async () => {
    const deferred = createDeferred<ReturnType<typeof makeResponse>>()
    vi.mocked(getAdapterHostPolicy).mockReturnValue(deferred.promise as never)
    renderPanel()

    expect(await screen.findByText('正在加载主程序规则')).toBeInTheDocument()
    deferred.resolve(makeResponse())
    expect(await screen.findByTestId('list-field:输入接收消息的群号')).toBeInTheDocument()
  })

  it('加载失败时展示 Error.message', async () => {
    vi.mocked(getAdapterHostPolicy).mockRejectedValue(new Error('策略服务不可用'))
    renderPanel()
    expect(await screen.findByText('策略服务不可用')).toBeInTheDocument()
  })

  it('加载失败且非 Error 时使用回退文案', async () => {
    vi.mocked(getAdapterHostPolicy).mockRejectedValue('offline')
    renderPanel()
    expect(await screen.findByText('主程序规则加载失败')).toBeInTheDocument()
  })

  it('查询成功但没有 data 时使用回退文案', async () => {
    vi.mocked(getAdapterHostPolicy).mockResolvedValue(null as never)
    renderPanel()
    expect(await screen.findByText('主程序规则加载失败')).toBeInTheDocument()
  })

  it('成功渲染群聊/私聊规则、全局默认和列表占位符', async () => {
    await renderReadyPanel()

    expect(screen.queryByText('这是 MaiBot 主程序侧规则，与适配器自身名单相互独立。')).not.toBeInTheDocument()
    expect(screen.queryByText(/适配器自身的白名单仍在/)).not.toBeInTheDocument()
    expect(screen.getByText('群聊规则')).toBeInTheDocument()
    expect(screen.getByText('私聊规则')).toBeInTheDocument()
    expect(screen.queryByText(/^全局默认：/)).not.toBeInTheDocument()
    expect(screen.queryByText('群聊填写群号，私聊填写用户 ID。')).not.toBeInTheDocument()
    expect(screen.queryByText('同一 ID 不可同时出现在阅读和不阅读列表。')).not.toBeInTheDocument()
    // 群聊继承全局「接收所有消息」→ 黑名单模式；私聊继承全局「默认不接收」→ 白名单模式
    expect(screen.getByTestId('mode-hint:group')).toHaveTextContent('黑名单模式')
    expect(screen.getByTestId('mode-hint:private')).toHaveTextContent('白名单模式')
    expect(screen.getByTestId('allow-section:group')).toHaveAttribute('data-inactive', 'true')
    expect(screen.getByTestId('deny-section:group')).toHaveAttribute('data-inactive', 'false')
    expect(screen.getByTestId('allow-section:private')).toHaveAttribute('data-inactive', 'false')
    expect(screen.getByTestId('deny-section:private')).toHaveAttribute('data-inactive', 'true')
    expect(screen.getByTestId('list-field:输入接收消息的群号')).toBeInTheDocument()
    expect(screen.getByTestId('list-field:输入不接收消息的群号')).toBeInTheDocument()
    expect(screen.getByTestId('list-field:输入接收消息的用户 ID')).toBeInTheDocument()
    expect(screen.getByTestId('list-field:输入不接收消息的用户 ID')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '与全局设置一致（接收所有消息）' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '与全局设置一致（默认不接收消息）' })).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: '默认不接收消息' })).toHaveLength(2)
    expect(screen.getAllByRole('button', { name: '接收所有消息' })).toHaveLength(2)
  })

  it('切换默认规则后置灰不生效的名单', async () => {
    const user = userEvent.setup()
    await renderReadyPanel()

    await user.click(screen.getAllByRole('button', { name: '默认不接收消息' })[0])
    expect(screen.getByTestId('mode-hint:group')).toHaveTextContent('白名单模式')
    expect(screen.getByTestId('allow-section:group')).toHaveAttribute('data-inactive', 'false')
    expect(screen.getByTestId('deny-section:group')).toHaveAttribute('data-inactive', 'true')
  })

  it('接收名单含 * 时不接收名单不置灰', async () => {
    await renderReadyPanel(
      'adapter.qq',
      makeResponse('adapter.qq', {
        policy: makePolicy({ group: { default_action: 'block', allow_ids: ['*'] } }),
      })
    )
    expect(screen.getByTestId('deny-section:group')).toHaveAttribute('data-inactive', 'false')
  })

  it('未做修改时不会触发自动保存，保存按钮禁用', async () => {
    await renderReadyPanel()
    await new Promise((resolve) => setTimeout(resolve, AUTOSAVE_DELAY_MS + 200))
    expect(updateAdapterHostPolicy).not.toHaveBeenCalled()
    expect(screen.queryByTestId('host-policy-save-status')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '保存' })).toBeDisabled()
  })

  it('点击保存按钮绕过防抖立即写回', async () => {
    const user = userEvent.setup()
    await renderReadyPanel()

    await user.click(screen.getAllByRole('button', { name: '接收所有消息' })[0])
    await user.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(
      () =>
        expect(updateAdapterHostPolicy).toHaveBeenCalledWith('adapter.qq', {
          group: { default_action: 'allow', allow_ids: [], deny_ids: [] },
          private: { default_action: 'inherit', allow_ids: [], deny_ids: [] },
        }),
      { timeout: 6000 }
    )
  })

  it('修改群聊默认规则后停止编辑即自动保存并展示保存时间', async () => {
    const user = userEvent.setup()
    vi.mocked(updateAdapterHostPolicy).mockResolvedValue(
      makeResponse('adapter.qq', { policy: makePolicy({ group: { default_action: 'allow' } }) }) as never
    )
    await renderReadyPanel()

    expect(screen.queryByTestId('host-policy-save-status')).not.toBeInTheDocument()
    await user.click(screen.getAllByRole('button', { name: '接收所有消息' })[0])
    expect(await screen.findByTestId('host-policy-save-status')).toHaveTextContent('未保存的更改')

    await waitFor(
      () =>
        expect(updateAdapterHostPolicy).toHaveBeenCalledWith('adapter.qq', {
          group: { default_action: 'allow', allow_ids: [], deny_ids: [] },
          private: { default_action: 'inherit', allow_ids: [], deny_ids: [] },
        }),
      { timeout: 6000 }
    )
    expect(getAdapterHostPolicy).toHaveBeenCalledWith('adapter.qq')
    expect(await screen.findByTestId('host-policy-save-status')).toHaveTextContent('已保存')
  })

  it('修改私聊名单时把非字符串项转成字符串', async () => {
    const user = userEvent.setup()
    await renderReadyPanel()

    await user.click(screen.getByRole('button', { name: '设为混合类型:输入接收消息的用户 ID' }))
    await user.click(screen.getByRole('button', { name: '添加:输入不接收消息的用户 ID' }))

    await waitFor(
      () =>
        expect(updateAdapterHostPolicy).toHaveBeenCalledWith('adapter.qq', {
          group: { default_action: 'inherit', allow_ids: [], deny_ids: [] },
          private: {
            default_action: 'inherit',
            allow_ids: ['123', 'true'],
            deny_ids: ['new-item'],
          },
        }),
      { timeout: 6000 }
    )
  })

  it('就地修改列表时 clonePolicy 会隔离初始数据', async () => {
    const user = userEvent.setup()
    const allowIds = ['g1']
    const policy = makePolicy({ group: { allow_ids: allowIds } })
    await renderReadyPanel('adapter.qq', makeResponse('adapter.qq', { policy }))

    await user.click(screen.getByRole('button', { name: '就地修改:输入接收消息的群号' }))

    expect(allowIds).toEqual(['g1'])
    expect(screen.getByTestId('list-value:输入接收消息的群号')).toHaveTextContent(
      JSON.stringify(['g1', 'mutated-in-place'])
    )
    await waitFor(
      () => expect(updateAdapterHostPolicy).toHaveBeenCalled(),
      { timeout: 6000 }
    )
  })

  it('自动保存成功后写入 query cache、失效聊天流详情，并保留编辑草稿', async () => {
    const user = userEvent.setup()
    const queryClient = makeQueryClient()
    const setQueryData = vi.spyOn(queryClient, 'setQueryData')
    const invalidateQueries = vi.spyOn(queryClient, 'invalidateQueries')
    const savedPolicy = makePolicy({
      group: { default_action: 'block', deny_ids: ['new-item'] },
    })
    const saved = makeResponse('adapter.qq', { policy: savedPolicy })
    vi.mocked(updateAdapterHostPolicy).mockResolvedValue(saved as never)
    await renderReadyPanel('adapter.qq', makeResponse('adapter.qq'), queryClient)

    await user.click(screen.getAllByRole('button', { name: '默认不接收消息' })[0])
    await user.click(screen.getByRole('button', { name: '添加:输入不接收消息的群号' }))

    await waitFor(
      () => expect(setQueryData).toHaveBeenCalledWith(['adapter-host-policy', 'adapter.qq'], saved),
      { timeout: 6000 }
    )
    expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ['chat-stream-detail'] })
    await waitFor(() =>
      expect(screen.getByTestId('host-policy-save-status')).toHaveTextContent('已保存')
    )
    // 回包落缓存后保留编辑草稿，内容与保存结果一致时显示已保存。
    expect(screen.getByTestId('list-value:输入不接收消息的群号')).toHaveTextContent(
      JSON.stringify(['new-item'])
    )
  })

  it('自动保存失败时按 Error / 非 Error 弹出 toast 并展示失败状态', async () => {
    const user = userEvent.setup()
    await renderReadyPanel()

    vi.mocked(updateAdapterHostPolicy).mockRejectedValueOnce(new Error('写入失败'))
    await user.click(screen.getAllByRole('button', { name: '接收所有消息' })[0])
    await waitFor(
      () =>
        expect(toastMock).toHaveBeenCalledWith({
          title: '主程序放行规则保存失败',
          description: '写入失败',
          variant: 'destructive',
        }),
      { timeout: 6000 }
    )
    expect(screen.getByTestId('host-policy-save-status')).toHaveTextContent('保存失败')

    vi.mocked(updateAdapterHostPolicy).mockRejectedValueOnce('offline')
    await user.click(screen.getByRole('button', { name: '添加:输入不接收消息的群号' }))
    await waitFor(
      () =>
        expect(toastMock).toHaveBeenCalledWith({
          title: '主程序放行规则保存失败',
          description: '请稍后重试',
          variant: 'destructive',
        }),
      { timeout: 6000 }
    )
  })

  it('自动保存进行中展示保存状态', async () => {
    const user = userEvent.setup()
    const deferred = createDeferred<ReturnType<typeof makeResponse>>()
    vi.mocked(updateAdapterHostPolicy).mockReturnValue(deferred.promise as never)
    await renderReadyPanel()

    await user.click(screen.getAllByRole('button', { name: '接收所有消息' })[1])

    await waitFor(
      () => expect(screen.getByTestId('host-policy-save-status')).toHaveTextContent('自动保存中'),
      { timeout: 6000 }
    )

    deferred.resolve(makeResponse('adapter.qq', {
      policy: makePolicy({ private: { default_action: 'allow' } }),
    }))
    await waitFor(
      () => expect(screen.getByTestId('host-policy-save-status')).toHaveTextContent('已保存'),
      { timeout: 6000 }
    )
  })

  it('pluginId 变化会按新 id 重新拉取规则', async () => {
    const queryClient = makeQueryClient()
    vi.mocked(getAdapterHostPolicy).mockImplementation(async (pluginId: string) => {
      return makeResponse(pluginId, {
        policy: makePolicy({
          group: { allow_ids: pluginId === 'adapter.telegram' ? ['tg'] : ['qq'] },
        }),
      }) as never
    })
    const { rerender } = renderPanel('adapter.qq', queryClient)
    expect(await screen.findByTestId('list-value:输入接收消息的群号')).toHaveTextContent(
      JSON.stringify(['qq'])
    )

    rerender(
      <QueryClientProvider client={queryClient}>
        <AdapterHostPolicyPanel pluginId="adapter.telegram" />
      </QueryClientProvider>
    )

    await waitFor(() =>
      expect(getAdapterHostPolicy).toHaveBeenCalledWith('adapter.telegram')
    )
    expect(await screen.findByTestId('list-value:输入接收消息的群号')).toHaveTextContent(
      JSON.stringify(['tg'])
    )
  })
})

describe('适配器账号身份展示', () => {
  it('显示当前激活账号 ID，并在换号后提示历史账号条目不再生效', async () => {
    vi.mocked(getAdapterHostPolicy).mockResolvedValue(
      {
        ...makeResponse('adapter.qq'),
        active_identity: {
          adapter_id: 'gateway:adapter.qq:gw',
          plugin_id: 'adapter.qq',
          gateway_name: 'gw',
          platform: 'qq',
          account_id: '9999999999',
          scope: null,
        },
        has_entry: false,
        account_entries: [
          {
            adapter_id: 'gateway:adapter.qq:gw',
            account_id: '2814567326',
            platform: 'qq',
            gateway_name: 'gw',
            scope: '',
            rules: { group: { default_action: 'block', allow_ids: ['10001'], deny_ids: [] } },
          },
        ],
      } as never
    )

    renderPanel('adapter.qq')

    expect(await screen.findByTestId('active-account-badge')).toHaveTextContent('当前账号 ID：9999999999')
    expect(await screen.findByText(/2814567326 的规则保留在配置中/)).toBeInTheDocument()
    expect(screen.getByText('无专属规则，按全局默认生效')).toBeInTheDocument()
  })

  it('已有专属规则的账号不显示换号提示', async () => {
    vi.mocked(getAdapterHostPolicy).mockResolvedValue(
      {
        ...makeResponse('adapter.qq'),
        active_identity: {
          adapter_id: 'gateway:adapter.qq:gw',
          plugin_id: 'adapter.qq',
          gateway_name: 'gw',
          platform: 'qq',
          account_id: '2814567326',
          scope: null,
        },
        has_entry: true,
        account_entries: [
          {
            adapter_id: 'gateway:adapter.qq:gw',
            account_id: '2814567326',
            platform: 'qq',
            gateway_name: 'gw',
            scope: '',
            rules: {},
          },
        ],
      } as never
    )

    renderPanel('adapter.qq')

    await screen.findByTestId('active-account-badge')
    expect(screen.getByTestId('active-account-badge')).toHaveTextContent('当前账号 ID：2814567326')
    expect(screen.queryByText(/不再生效/)).not.toBeInTheDocument()
  })
})

describe('历史账号条目去重', () => {
  it('同一历史账号在多个网关下有条目时只提示一次', async () => {
    const staleEntry = {
      adapter_id: 'gateway:adapter.qq:gw',
      account_id: '2814567326',
      platform: 'qq',
      gateway_name: 'gw',
      scope: '',
      rules: {},
    }
    vi.mocked(getAdapterHostPolicy).mockResolvedValue(
      {
        ...makeResponse('adapter.qq'),
        active_identity: {
          adapter_id: 'gateway:adapter.qq:gw',
          plugin_id: 'adapter.qq',
          gateway_name: 'gw',
          platform: 'qq',
          account_id: '9999999999',
          scope: null,
        },
        has_entry: false,
        account_entries: [
          staleEntry,
          { ...staleEntry, adapter_id: 'gateway:adapter.qq:gw2', gateway_name: 'gw2' },
        ],
      } as never
    )

    renderPanel('adapter.qq')

    const alert = await screen.findByText(/的规则保留在配置中/)
    expect(alert.textContent?.match(/2814567326/g)).toHaveLength(1)
  })
})
