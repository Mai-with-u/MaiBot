import type { ReactNode } from 'react'
import type { PersonInfo, VirtualIdentityConfig } from '../types'

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { VirtualIdentityDialog } from '../VirtualIdentityDialog'

// t 稳定引用，count 参数拼进返回值便于断言
const { tMock } = vi.hoisted(() => ({
  tMock: (key: string, options?: Record<string, unknown>) => {
    if (options && options.count !== undefined) {
      return `${key}:${String(options.count)}`
    }
    return key
  },
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: tMock }),
}))

// 用受控上下文替代 Radix Select，规避 jsdom 的 pointer-capture 限制
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
        <div>{children}</div>
      </SelectContext.Provider>
    )
  }

  function SelectTrigger({ children, disabled }: { children?: ReactNode; disabled?: boolean }) {
    return (
      <button type="button" data-testid="platform-select-trigger" disabled={disabled}>
        {children}
      </button>
    )
  }

  function SelectValue({ placeholder }: { placeholder?: ReactNode }) {
    const { value } = React.useContext(SelectContext)
    return <span>{value || placeholder}</span>
  }

  function SelectContent({ children }: { children?: ReactNode }) {
    return <div>{children}</div>
  }

  function SelectItem({ value, children }: { value: string; children?: ReactNode }) {
    const { onValueChange } = React.useContext(SelectContext)
    return (
      <button type="button" onClick={() => onValueChange?.(value)}>
        {children}
      </button>
    )
  }

  return { Select, SelectContent, SelectItem, SelectTrigger, SelectValue }
})

const emptyConfig: VirtualIdentityConfig = {
  platform: '',
  personId: '',
  userId: '',
  userName: '',
  groupName: '',
  groupId: '',
}

function makePerson(overrides: Partial<PersonInfo> = {}): PersonInfo {
  return {
    person_id: 'p1',
    user_id: 'u100',
    person_name: 'alice',
    nickname: null,
    platform: 'qq',
    is_known: true,
    ...overrides,
  }
}

function renderDialog(overrides: Partial<Parameters<typeof VirtualIdentityDialog>[0]> = {}) {
  const props: Parameters<typeof VirtualIdentityDialog>[0] = {
    open: true,
    onOpenChange: vi.fn(),
    platforms: [],
    persons: [],
    isLoadingPlatforms: false,
    isLoadingPersons: false,
    personSearchQuery: '',
    setPersonSearchQuery: vi.fn(),
    tempVirtualConfig: emptyConfig,
    setTempVirtualConfig: vi.fn(),
    onSelectPerson: vi.fn(),
    onCreateVirtualTab: vi.fn(),
    ...overrides,
  }
  const view = render(<VirtualIdentityDialog {...props} />)
  return { ...view, props }
}

afterEach(() => cleanup())

describe('VirtualIdentityDialog', () => {
  it('关闭状态不渲染任何对话框内容', () => {
    renderDialog({ open: false })
    expect(screen.queryByText('chat.dialog.title')).not.toBeInTheDocument()
  })

  it('平台加载中禁用选择器并显示加载占位', () => {
    renderDialog({ isLoadingPlatforms: true })
    expect(screen.getByText('chat.dialog.title')).toBeInTheDocument()
    expect(screen.getByTestId('platform-select-trigger')).toBeDisabled()
    expect(screen.getByText('chat.dialog.loading')).toBeInTheDocument()
    // 未选择平台时不显示用户搜索区域
    expect(screen.queryByPlaceholderText('chat.dialog.searchUser')).not.toBeInTheDocument()
  })

  it('选择平台时重置已选用户并保留其他字段', async () => {
    const user = userEvent.setup()
    const { props } = renderDialog({
      platforms: [
        { platform: 'qq', count: 3 },
        { platform: 'telegram', count: 1 },
      ],
    })

    await user.click(screen.getByRole('button', { name: 'qq chat.dialog.personCount:3' }))
    expect(props.setTempVirtualConfig).toHaveBeenCalledTimes(1)

    // 组件传入的是函数式更新器，手动执行验证重置逻辑
    const updater = (props.setTempVirtualConfig as ReturnType<typeof vi.fn>).mock
      .calls[0][0] as (prev: VirtualIdentityConfig) => VirtualIdentityConfig
    const next = updater({
      platform: 'telegram',
      personId: 'p-old',
      userId: 'u-old',
      userName: '旧用户',
      groupName: '旧群名',
      groupId: 'g-old',
    })
    expect(next).toEqual({
      platform: 'qq',
      personId: '',
      userId: '',
      userName: '',
      groupName: '旧群名',
      groupId: 'g-old',
    })
  })

  it('已选平台后展示搜索框，输入内容触发搜索回调', () => {
    const { props } = renderDialog({
      tempVirtualConfig: { ...emptyConfig, platform: 'qq' },
    })
    const input = screen.getByPlaceholderText('chat.dialog.searchUser')
    fireEvent.change(input, { target: { value: '张' } })
    expect(props.setPersonSearchQuery).toHaveBeenCalledWith('张')
  })

  it('用户加载中显示转圈，无用户时显示空态', () => {
    const { container, rerender, props } = renderDialog({
      tempVirtualConfig: { ...emptyConfig, platform: 'qq' },
      isLoadingPersons: true,
    })
    expect(container.ownerDocument.querySelector('.animate-spin')).not.toBeNull()
    expect(screen.queryByText('chat.dialog.noUsers')).not.toBeInTheDocument()

    rerender(<VirtualIdentityDialog {...props} isLoadingPersons={false} />)
    expect(screen.getByText('chat.dialog.noUsers')).toBeInTheDocument()
  })

  it('用户列表展示昵称优先的显示名、已认识徽章与 ID，点击行触发选择', async () => {
    const user = userEvent.setup()
    const alice = makePerson()
    const bob = makePerson({
      person_id: 'p2',
      user_id: 'u200',
      person_name: 'bob',
      nickname: '小北',
      is_known: false,
    })
    const { props } = renderDialog({
      tempVirtualConfig: { ...emptyConfig, platform: 'qq' },
      persons: [alice, bob],
    })

    // 无昵称时显示 person_name，头像取首字母大写
    expect(screen.getByText('alice')).toBeInTheDocument()
    expect(screen.getByText('A')).toBeInTheDocument()
    // 有昵称时优先昵称
    expect(screen.getByText('小北')).toBeInTheDocument()
    expect(screen.getByText('u100')).toBeInTheDocument()
    // 只有 is_known 的用户显示已认识徽章
    expect(screen.getAllByText('chat.dialog.knownUserSuffix')).toHaveLength(1)

    await user.click(screen.getByRole('button', { name: /alice/ }))
    expect(props.onSelectPerson).toHaveBeenCalledWith(alice)
  })

  it('选中用户后可配置虚拟群名，创建与取消按钮各自回调', async () => {
    const user = userEvent.setup()
    const selectedConfig: VirtualIdentityConfig = {
      platform: 'qq',
      personId: 'p1',
      userId: 'u100',
      userName: 'alice',
      groupName: '',
      groupId: 'g1',
    }
    // 受控输入的值会在事件处理结束后被 React 还原，
    // 因此必须在 setter 被调用的当下同步执行更新器来捕获结果
    let captured: VirtualIdentityConfig | undefined
    const setTempVirtualConfig = vi.fn(
      (action: React.SetStateAction<VirtualIdentityConfig>) => {
        captured = typeof action === 'function' ? action(selectedConfig) : action
      }
    )
    const { props } = renderDialog({
      tempVirtualConfig: selectedConfig,
      setTempVirtualConfig,
    })

    const groupInput = screen.getByPlaceholderText('chat.virtualGroupFallback')
    fireEvent.change(groupInput, { target: { value: '测试群' } })
    expect(captured).toEqual({ ...selectedConfig, groupName: '测试群' })

    const createButton = screen.getByRole('button', { name: 'chat.dialog.create' })
    expect(createButton).toBeEnabled()
    await user.click(createButton)
    expect(props.onCreateVirtualTab).toHaveBeenCalledTimes(1)

    await user.click(screen.getByRole('button', { name: 'chat.actions.cancel' }))
    expect(props.onOpenChange).toHaveBeenCalledWith(false)
  })

  it('未选中用户时创建按钮禁用且不显示群名配置', () => {
    renderDialog({ tempVirtualConfig: { ...emptyConfig, platform: 'qq' } })
    expect(screen.getByRole('button', { name: 'chat.dialog.create' })).toBeDisabled()
    expect(screen.queryByPlaceholderText('chat.virtualGroupFallback')).not.toBeInTheDocument()
  })
})
