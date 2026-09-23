import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  AliasNamesHook,
  AMemorixSharedMemoryGroupsHook,
  BehaviorFocusGroupsHook,
  BehaviorGroupsHook,
  BehaviorLearningListHook,
  BotPlatformAccountsHook,
  ChatPromptsHook,
  ChatTalkValueRulesHook,
  ExpressionGroupsHook,
  ExpressionLearningListHook,
  FocusWhitelistHook,
  HiddenFieldHook,
  JargonGroupsHook,
  JargonLearningListHook,
  KeywordRulesHook,
  MCPRootItemsHook,
  MCPServersHook,
  MultipleReplyStyleHook,
  RegexRulesHook,
  TalkValueDisabledByJevHook,
} from '../complexFieldHooks'
import * as botAccountsApi from '@/lib/bot-accounts-api'
import { getChatStreams, resolveChatTargets, type ChatStream } from '@/lib/chat-management-api'
import { getBotConfigCached } from '@/lib/config-api'
import type { ConfigSchema, FieldSchema } from '@/types/config-schema'

vi.mock('react-i18next', () => {
  const t = (key: string) => key
  return { useTranslation: () => ({ t, i18n: { language: 'zh-CN' } }) }
})

vi.mock('@/lib/chat-management-api', () => ({
  getChatStreams: vi.fn(async () => []),
  resolveChatTargets: vi.fn(async () => []),
}))

vi.mock('@/lib/config-api', () => ({
  getBotConfigCached: vi.fn(async () => ({
    bot: { platform: 'qq', platforms: ['wx:10001', 'telegram'] },
  })),
}))

vi.mock('@/lib/bot-accounts-api', () => ({
  getDiscoveredBotAccounts: vi.fn(),
  setDiscoveredBotAccountDisabled: vi.fn(),
}))

const resolveChatTargetsMock = vi.mocked(resolveChatTargets)
const getChatStreamsMock = vi.mocked(getChatStreams)
const getBotConfigCachedMock = vi.mocked(getBotConfigCached)

const fieldSchema: FieldSchema = {
  name: 'rules',
  type: 'array',
  label: '规则列表',
  description: '规则说明',
  required: false,
}

const talkRuleSchema: ConfigSchema = {
  className: 'TalkValueRule',
  classDoc: '发言频率规则',
  fields: [
    { name: 'platform', type: 'string', label: '平台', description: '', required: false, default: '' },
    { name: 'item_id', type: 'string', label: '聊天流 ID', description: '', required: false, default: '' },
    {
      name: 'rule_type',
      type: 'select',
      label: '聊天类型',
      description: '',
      required: false,
      default: 'group',
      options: ['group', 'private'],
    },
    { name: 'time', type: 'string', label: '时间', description: '', required: false, default: '' },
    { name: 'value', type: 'number', label: '频率', description: '', required: false, default: 0.5 },
  ],
}

const promptSchema: ConfigSchema = {
  className: 'ChatPrompt',
  classDoc: '聊天 Prompt',
  fields: [
    { name: 'platform', type: 'string', label: '平台', description: '', required: false, default: 'qq' },
    { name: 'item_id', type: 'string', label: '聊天流 ID', description: '', required: false, default: '' },
    {
      name: 'rule_type',
      type: 'select',
      label: '聊天类型',
      description: '',
      required: false,
      default: 'group',
      options: ['group', 'private'],
    },
    { name: 'prompt', type: 'textarea', label: '提示', description: '', required: false, default: '' },
  ],
}

const keywordSchema: ConfigSchema = {
  className: 'KeywordRule',
  classDoc: '关键词规则',
  fields: [
    { name: 'keywords', type: 'array', label: '关键词', description: '', required: false, default: [] },
    { name: 'reaction', type: 'string', label: '反应', description: '', required: false, default: '' },
    { name: 'regex', type: 'array', label: '正则', description: '', required: false, default: [] },
  ],
}

function createChatStream(overrides: Partial<ChatStream> = {}): ChatStream {
  return {
    id: 1,
    session_id: 'session-group',
    display_name: '测试群',
    chat_type: 'group',
    target_id: '10001',
    platform: 'qq',
    account_id: null,
    scope: null,
    user_id: '',
    user_nickname: null,
    user_cardname: null,
    group_id: '10001',
    group_name: '测试群',
    message_count: 10,
    expression_count: 0,
    jargon_count: 0,
    created_at: null,
    last_active_at: null,
    latest_message: '',
    latest_message_at: null,
    ...overrides,
  }
}

function createDeferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, reject, resolve }
}

let itemSeq = 0
function nextItemId(prefix = 'item') {
  itemSeq += 1
  return `${prefix}-${itemSeq}`
}

function learningItem(overrides: Record<string, unknown> = {}) {
  return {
    platform: 'qq',
    item_id: nextItemId(),
    type: 'group',
    use: true,
    learn: true,
    ...overrides,
  }
}

function talkItem(overrides: Record<string, unknown> = {}) {
  return {
    platform: 'qq',
    item_id: nextItemId('talk'),
    rule_type: 'group',
    time: '08:00-12:00',
    value: 0.4,
    ...overrides,
  }
}

async function addLearningRule(scopeTitle: string, chatType?: '群聊' | '私聊') {
  const user = userEvent.setup()
  await user.click(screen.getByRole('button', { name: '添加学习规则' }))
  const dialog = await screen.findByRole('dialog')
  await user.click(within(dialog).getByRole('button', { name: new RegExp(scopeTitle) }))
  if (chatType) {
    await user.click(within(dialog).getByRole('combobox'))
    await user.click(await screen.findByRole('option', { name: chatType }))
  }
  await user.click(within(dialog).getByRole('button', { name: '添加' }))
  return user
}

const sharedMemorySchema: FieldSchema = {
  name: 'shared_memory_groups',
  type: 'array',
  label: '共享记忆组',
  description: '共享记忆组',
  required: false,
  'x-display-as-section': true,
}

function talkRulesProps(overrides: Record<string, unknown> = {}) {
  return {
    fieldPath: 'chat.reply_timing.talk_value_rules',
    parentValues: { enable_talk_value_rules: true },
    schema: fieldSchema,
    nestedSchema: talkRuleSchema,
    ...overrides,
  }
}

describe('complexFieldHooks', () => {
  beforeAll(() => {
    const captured = new Set<number>()
    Object.defineProperty(HTMLElement.prototype, 'hasPointerCapture', {
      configurable: true,
      value(this: HTMLElement, pointerId: number) {
        return captured.has(pointerId)
      },
    })
    Object.defineProperty(HTMLElement.prototype, 'setPointerCapture', {
      configurable: true,
      value(this: HTMLElement, pointerId: number) {
        captured.add(pointerId)
      },
    })
    Object.defineProperty(HTMLElement.prototype, 'releasePointerCapture', {
      configurable: true,
      value(this: HTMLElement, pointerId: number) {
        captured.delete(pointerId)
      },
    })
  })

  beforeEach(() => {
    getChatStreamsMock.mockResolvedValue([])
    resolveChatTargetsMock.mockResolvedValue([])
    getBotConfigCachedMock.mockResolvedValue({
      bot: { platform: 'qq', platforms: ['wx:10001', 'telegram'] },
    })
    vi.mocked(botAccountsApi.getDiscoveredBotAccounts).mockResolvedValue([])
  })

  afterEach(() => {
    cleanup()
    vi.useRealTimers()
    vi.clearAllMocks()
  })

  describe('createStringListHook', () => {
    it('添加、编辑并删除别名列表项', async () => {
      const user = userEvent.setup()
      const onChange = vi.fn()

      const { rerender } = render(
        <AliasNamesHook fieldPath="bot.alias_names" onChange={onChange} schema={fieldSchema} value={[]} />,
      )

      expect(screen.getByText('暂无别名。')).toBeInTheDocument()

      await user.click(screen.getByRole('button', { name: '添加别名' }))
      expect(onChange).toHaveBeenLastCalledWith([''])

      rerender(
        <AliasNamesHook fieldPath="bot.alias_names" onChange={onChange} schema={fieldSchema} value={['']} />,
      )

      fireEvent.change(screen.getByPlaceholderText('小麦'), { target: { value: '麦麦' } })
      expect(onChange).toHaveBeenLastCalledWith(['麦麦'])

      rerender(
        <AliasNamesHook fieldPath="bot.alias_names" onChange={onChange} schema={fieldSchema} value={['麦麦']} />,
      )

      await user.click(screen.getByRole('button', { name: '删除别名 1' }))
      expect(onChange).toHaveBeenLastCalledWith([])
    })

    it('备用表达风格走多行 Textarea 编辑路径', async () => {
      const user = userEvent.setup()
      const onChange = vi.fn()

      render(
        <MultipleReplyStyleHook
          fieldPath="personality.multiple_reply_style"
          onChange={onChange}
          schema={fieldSchema}
          value={['温和一点']}
        />,
      )

      const textarea = screen.getByPlaceholderText('输入一种备用表达风格')
      expect(textarea.tagName).toBe('TEXTAREA')
      fireEvent.change(textarea, { target: { value: '更活泼' } })
      expect(onChange).toHaveBeenLastCalledWith(['更活泼'])

      await user.click(screen.getByRole('button', { name: '删除备用表达风格 1' }))
      expect(onChange).toHaveBeenLastCalledWith([])
    })
  })

  describe('LearningRuleEditor', () => {
    it('按范围添加学习规则并切换使用/学习开关', async () => {
      const onChange = vi.fn()
      const { rerender } = render(
        <ExpressionLearningListHook
          fieldPath="expression.learning_list"
          onChange={onChange}
          schema={fieldSchema}
          value={[]}
        />,
      )

      expect(screen.getByText('尚未配置任何学习规则。')).toBeInTheDocument()

      await addLearningRule('默认兜底')
      expect(onChange).toHaveBeenLastCalledWith([
        { type: 'group', use: true, learn: true, platform: '', item_id: '' },
      ])

      rerender(
        <ExpressionLearningListHook
          fieldPath="expression.learning_list"
          onChange={onChange}
          schema={fieldSchema}
          value={[{ type: 'group', use: true, learn: true, platform: '', item_id: '' }]}
        />,
      )

      expect(screen.getByText('全局默认')).toBeInTheDocument()
      expect(screen.getByText('当前范围不需要填写平台或聊天流 ID。')).toBeInTheDocument()

      await addLearningRule('全局通配', '私聊')
      expect(onChange).toHaveBeenLastCalledWith([
        { type: 'group', use: true, learn: true, platform: '', item_id: '' },
        { type: 'private', use: true, learn: true, platform: '*', item_id: '*' },
      ])
    })

    it('平台通配、平台兜底和指定聊天流分别写入不同字段', async () => {
      const onChange = vi.fn()
      render(
        <ExpressionLearningListHook
          fieldPath="expression.learning_list"
          onChange={onChange}
          schema={fieldSchema}
          value={[]}
        />,
      )

      await addLearningRule('平台通配')
      expect(onChange).toHaveBeenLastCalledWith([
        { type: 'group', use: true, learn: true, platform: 'qq', item_id: '*' },
      ])

      await addLearningRule('平台兜底')
      expect(onChange).toHaveBeenLastCalledWith([
        { type: 'group', use: true, learn: true, platform: 'qq', item_id: '' },
      ])

      await addLearningRule('指定聊天流')
      expect(onChange).toHaveBeenLastCalledWith([
        { type: 'group', use: true, learn: true, platform: 'qq', item_id: '' },
      ])
    })

    it('渲染已有范围并支持改平台、聊天流 ID 和删除', async () => {
      const user = userEvent.setup()
      const onChange = vi.fn()
      const chatId = nextItemId('learn')

      render(
        <ExpressionLearningListHook
          fieldPath="expression.learning_list"
          onChange={onChange}
          schema={fieldSchema}
          value={[
            { platform: '', item_id: '', type: 'group', use: true, learn: false },
            { platform: '*', item_id: '*', type: 'private', use: false, learn: true },
            { platform: 'qq', item_id: '*', type: 'group', use: true, learn: true },
            { platform: 'wx', item_id: '', type: 'group', use: true, learn: true },
            { platform: '*', item_id: '777', type: 'group', use: true, learn: true },
            { platform: 'qq', item_id: chatId, type: 'group', use: true, learn: true },
          ]}
        />,
      )

      expect(screen.getByText('全局默认')).toBeInTheDocument()
      expect(screen.getByText('全部聊天')).toBeInTheDocument()
      expect(screen.getByText('qq:全部目标')).toBeInTheDocument()
      expect(screen.getByText('wx:平台兜底')).toBeInTheDocument()
      expect(screen.getByText('任意平台:777')).toBeInTheDocument()
      expect(screen.getByText(`qq:${chatId}`)).toBeInTheDocument()
      expect(screen.queryByText('使用和学习均关闭')).not.toBeInTheDocument()

      const itemIdInputs = screen.getAllByPlaceholderText('群号或用户 ID')
      fireEvent.change(itemIdInputs[0], { target: { value: '888' } })
      expect(onChange).toHaveBeenCalled()

      const platformInputs = screen.getAllByPlaceholderText('qq')
      fireEvent.change(platformInputs[0], { target: { value: 'telegram' } })
      // 平台通配改平台时会同步把 item_id 写回 *
      expect(onChange).toHaveBeenCalled()

      const useSwitches = screen.getAllByRole('switch')
      await user.click(useSwitches[0])
      expect(onChange).toHaveBeenCalledWith(expect.any(Array))

      await user.click(screen.getByRole('button', { name: '删除学习规则 1' }))
      expect(onChange.mock.calls.at(-1)?.[0]).toHaveLength(5)
    })

    it('指定聊天流解析成功、缺失和失败分别展示预览', async () => {
      const foundId = nextItemId('found')
      const missingId = nextItemId('missing')
      const errorId = nextItemId('error')
      const session = createChatStream({
        display_name: '解析成功群',
        target_id: foundId,
        group_id: foundId,
      })

      resolveChatTargetsMock.mockImplementation(async (targets) =>
        targets.map((target) => {
          if (target.item_id === foundId) {
            return { found: true, session }
          }
          if (target.item_id === errorId) {
            throw new Error('解析失败')
          }
          return { found: false, session: null }
        }),
      )

      const { rerender } = render(
        <ExpressionLearningListHook
          fieldPath="expression.learning_list"
          onChange={vi.fn()}
          schema={fieldSchema}
          value={[learningItem({ item_id: foundId })]}
        />,
      )

      await waitFor(() => {
        expect(screen.getByText('解析成功群')).toBeInTheDocument()
      })
      expect(screen.getAllByText(`qq:${foundId}`).length).toBeGreaterThan(0)

      rerender(
        <ExpressionLearningListHook
          fieldPath="expression.learning_list"
          onChange={vi.fn()}
          schema={fieldSchema}
          value={[learningItem({ item_id: missingId })]}
        />,
      )

      await waitFor(() => {
        expect(screen.getByText('无效的聊天流')).toBeInTheDocument()
      })

      rerender(
        <ExpressionLearningListHook
          fieldPath="expression.learning_list"
          onChange={vi.fn()}
          schema={fieldSchema}
          value={[learningItem({ item_id: errorId })]}
        />,
      )

      await waitFor(() => {
        expect(screen.getByText('聊天流验证失败')).toBeInTheDocument()
      })
    })

    it('同一目标命中缓存且批量请求会去重', async () => {
      const cachedId = nextItemId('cache')
      const otherId = nextItemId('other')
      const session = createChatStream({
        display_name: '缓存群',
        target_id: cachedId,
        group_id: cachedId,
      })
      resolveChatTargetsMock.mockResolvedValue([
        { found: true, session },
        { found: false, session: null },
      ])

      const { rerender } = render(
        <ExpressionLearningListHook
          fieldPath="expression.learning_list"
          onChange={vi.fn()}
          schema={fieldSchema}
          value={[
            learningItem({ item_id: cachedId }),
            learningItem({ item_id: cachedId }),
            learningItem({ item_id: otherId }),
          ]}
        />,
      )

      await waitFor(() => {
        expect(resolveChatTargetsMock).toHaveBeenCalled()
      })
      const firstCallCount = resolveChatTargetsMock.mock.calls.length
      expect(resolveChatTargetsMock.mock.calls[0][0]).toHaveLength(2)

      rerender(
        <ExpressionLearningListHook
          fieldPath="expression.learning_list"
          onChange={vi.fn()}
          schema={fieldSchema}
          value={[learningItem({ item_id: cachedId })]}
        />,
      )

      await waitFor(() => {
        expect(screen.getAllByText('缓存群').length).toBeGreaterThan(0)
      })
      expect(resolveChatTargetsMock.mock.calls.length).toBe(firstCallCount)
    })

    it('解析进行中展示加载文案', async () => {
      const pendingId = nextItemId('pending')
      const deferred = createDeferred<Array<{ found: boolean; session?: ChatStream | null }>>()
      resolveChatTargetsMock.mockReturnValue(deferred.promise)

      render(
        <ExpressionLearningListHook
          fieldPath="expression.learning_list"
          onChange={vi.fn()}
          schema={fieldSchema}
          value={[learningItem({ item_id: pendingId })]}
        />,
      )

      expect(await screen.findByText('正在验证聊天流...')).toBeInTheDocument()
      deferred.resolve([{ found: false, session: null }])
      expect(await screen.findByText('无效的聊天流')).toBeInTheDocument()
    })

    it('黑话学习规则使用平台下拉，加载失败时仍可手动输入', async () => {
      const user = userEvent.setup()
      getBotConfigCachedMock.mockRejectedValueOnce(new Error('平台列表失败'))
      const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})
      const onChange = vi.fn()

      const { unmount } = render(
        <JargonLearningListHook
          fieldPath="jargon.learning_list"
          onChange={onChange}
          schema={fieldSchema}
          value={[]}
        />,
      )

      await waitFor(() => {
        expect(consoleError).toHaveBeenCalled()
      })
      unmount()

      getBotConfigCachedMock.mockResolvedValue({
        bot: { platform: 'qq', platforms: ['wx:10001'] },
      })
      render(
        <JargonLearningListHook
          fieldPath="jargon.learning_list"
          onChange={onChange}
          schema={fieldSchema}
          value={[{ platform: 'telegram', item_id: '*', type: 'group', use: true, learn: true }]}
        />,
      )

      expect(await screen.findByText('telegram:全部目标')).toBeInTheDocument()
      await waitFor(() => {
        expect(getBotConfigCachedMock).toHaveBeenCalled()
      })
      await act(async () => {
        await Promise.resolve()
      })
      await user.click(screen.getByRole('combobox'))
      expect(await screen.findByRole('option', { name: 'telegram（当前值）' })).toBeInTheDocument()
      expect(await screen.findByRole('option', { name: 'wx' })).toBeInTheDocument()
      await user.click(screen.getByRole('option', { name: 'wx' }))
      expect(onChange).toHaveBeenCalled()
      consoleError.mockRestore()
    })

    it('删除中间规则后仍保留后续聊天流范围覆盖', async () => {
      const user = userEvent.setup()
      const onChange = vi.fn()

      render(
        <BehaviorLearningListHook
          fieldPath="behavior.learning_list"
          onChange={onChange}
          schema={fieldSchema}
          value={[
            { type: 'group', use: true, learn: true, platform: 'qq', item_id: '' },
            { type: 'group', use: true, learn: true, platform: '', item_id: '' },
          ]}
        />,
      )

      await user.click(screen.getByRole('button', { name: '删除学习规则 1' }))
      expect(onChange).toHaveBeenLastCalledWith([
        { type: 'group', use: true, learn: true, platform: '', item_id: '' },
      ])
    })

    it('取消添加对话框，并展示未指定类型、双关关闭和待填写聊天流', async () => {
      const user = userEvent.setup()
      const onChange = vi.fn()
      const { rerender } = render(
        <ExpressionLearningListHook
          fieldPath="expression.learning_list"
          onChange={onChange}
          schema={fieldSchema}
          value={[]}
        />,
      )

      await user.click(screen.getByRole('button', { name: '添加学习规则' }))
      const dialog = await screen.findByRole('dialog')
      await user.click(within(dialog).getByRole('button', { name: '取消' }))
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
      expect(onChange).not.toHaveBeenCalled()

      await addLearningRule('指定聊天流')
      expect(onChange).toHaveBeenLastCalledWith([
        { type: 'group', use: true, learn: true, platform: 'qq', item_id: '' },
      ])

      rerender(
        <ExpressionLearningListHook
          fieldPath="expression.learning_list"
          onChange={onChange}
          schema={fieldSchema}
          value={[{ type: 'group', use: true, learn: true, platform: 'qq', item_id: '' }]}
        />,
      )
      expect(screen.getByText('qq:待填写聊天流')).toBeInTheDocument()
      expect(screen.queryByText('正在验证聊天流...')).not.toBeInTheDocument()

      await addLearningRule('指定聊天流')
      rerender(
        <ExpressionLearningListHook
          fieldPath="expression.learning_list"
          onChange={onChange}
          schema={fieldSchema}
          value={[
            { type: 'group', use: true, learn: true, platform: 'qq', item_id: '' },
            { type: 'group', use: true, learn: true, platform: 'qq', item_id: '' },
          ]}
        />,
      )
      expect(screen.getAllByText('qq:待填写聊天流')).toHaveLength(2)

      await user.click(screen.getByRole('button', { name: '删除学习规则 1' }))
      rerender(
        <ExpressionLearningListHook
          fieldPath="expression.learning_list"
          onChange={onChange}
          schema={fieldSchema}
          value={[{ type: 'group', use: true, learn: true, platform: 'qq', item_id: '' }]}
        />,
      )
      expect(screen.getByText('qq:待填写聊天流')).toBeInTheDocument()

      rerender(
        <ExpressionLearningListHook
          fieldPath="expression.learning_list"
          onChange={onChange}
          schema={fieldSchema}
          value={[
            { platform: '', item_id: '123', type: 'channel', use: false, learn: false },
            { platform: '*', item_id: '', type: 'group', use: false, learn: false },
            { platform: '', item_id: '*', type: 'private', use: false, learn: false },
            { platform: '  ', item_id: '  ', type: 'group', use: false, learn: false },
          ]}
        />,
      )
      expect(screen.getByText('留空:123')).toBeInTheDocument()
      expect(screen.getByText('任意平台:留空')).toBeInTheDocument()
      expect(screen.getByText('留空:全部目标')).toBeInTheDocument()
      expect(screen.getByText('全局默认')).toBeInTheDocument()

      const learnSwitches = screen.getAllByRole('switch')
      await user.click(learnSwitches[1])
      expect(onChange).toHaveBeenCalled()
    })

    it('解析结果缺少 session 视为缺失，卸载后忽略进行中的请求', async () => {
      const missingSessionId = nextItemId('nosession')
      const pendingId = nextItemId('unmount')
      resolveChatTargetsMock.mockResolvedValueOnce([{ found: true, session: null }])

      render(
        <ExpressionLearningListHook
          fieldPath="expression.learning_list"
          onChange={vi.fn()}
          schema={fieldSchema}
          value={[learningItem({ item_id: missingSessionId })]}
        />,
      )
      expect(await screen.findByText('无效的聊天流')).toBeInTheDocument()

      cleanup()
      const deferred = createDeferred<Array<{ found: boolean; session?: ChatStream | null }>>()
      resolveChatTargetsMock.mockReturnValue(deferred.promise)
      const { unmount } = render(
        <ExpressionLearningListHook
          fieldPath="expression.learning_list"
          onChange={vi.fn()}
          schema={fieldSchema}
          value={[learningItem({ item_id: pendingId, type: 'private' })]}
        />,
      )
      expect(await screen.findByText('正在验证聊天流...')).toBeInTheDocument()
      unmount()
      deferred.resolve([
        {
          found: true,
          session: createChatStream({
            chat_type: 'private',
            display_name: '卸载后不应出现',
            user_id: pendingId,
            target_id: pendingId,
            group_id: null,
          }),
        },
      ])
      await act(async () => {
        await Promise.resolve()
      })
      expect(screen.queryByText('卸载后不应出现')).not.toBeInTheDocument()
    })

    it('命中私聊聊天流时展示用户 ID 预览', async () => {
      const privateId = nextItemId('private')
      resolveChatTargetsMock.mockResolvedValue([
        {
          found: true,
          session: createChatStream({
            chat_type: 'private',
            display_name: '小明的私聊',
            user_id: privateId,
            target_id: privateId,
            group_id: null,
            account_id: 'acc-9',
          }),
        },
      ])

      render(
        <ExpressionLearningListHook
          fieldPath="expression.learning_list"
          onChange={vi.fn()}
          schema={fieldSchema}
          value={[learningItem({ item_id: privateId, type: 'private' })]}
        />,
      )

      expect(await screen.findByText('小明的私聊 · 账号 acc-9')).toBeInTheDocument()
      expect(screen.getAllByText(`qq:${privateId}`).length).toBeGreaterThan(0)
    })

    it('黑话平台下拉在无已定义平台时禁用，卸载后不再写回配置', async () => {
      const deferred = createDeferred<Record<string, unknown>>()
      getBotConfigCachedMock.mockReturnValue(deferred.promise)
      const { unmount } = render(
        <JargonLearningListHook
          fieldPath="jargon.learning_list"
          onChange={vi.fn()}
          schema={fieldSchema}
          value={[]}
        />,
      )
      unmount()
      deferred.resolve({ bot: { platform: 'qq', platforms: ['wx'] } })
      await act(async () => {
        await Promise.resolve()
      })

      getBotConfigCachedMock.mockResolvedValue({
        bot: { platform: null, platforms: 'not-array' },
      })
      render(
        <JargonLearningListHook
          fieldPath="jargon.learning_list"
          onChange={vi.fn()}
          schema={fieldSchema}
          value={[{ platform: '', item_id: '123', type: 'group', use: true, learn: true }]}
        />,
      )
      expect(await screen.findByText('留空:123')).toBeInTheDocument()
      expect(screen.getByText('未定义平台')).toBeInTheDocument()
    })
  })

  describe('ChatTalkValueRulesHook', () => {
    it('折叠未启用的规则，展开后可通过对话框添加轨道', async () => {
      const user = userEvent.setup()
      const onChange = vi.fn()

      const { rerender } = render(
        <ChatTalkValueRulesHook
          fieldPath="chat.reply_timing.talk_value_rules"
          onChange={onChange}
          parentValues={{ enable_talk_value_rules: false }}
          schema={fieldSchema}
          nestedSchema={talkRuleSchema}
          value={[]}
        />,
      )

      expect(screen.getByText('动态发言频率规则未启用，规则列表已折叠。展开后仍可查看或编辑已有规则。')).toBeInTheDocument()
      await user.click(screen.getByRole('button', { name: '展开规则' }))
      expect(screen.getByRole('button', { name: '添加发言频率规则' })).toBeInTheDocument()

      rerender(
        <ChatTalkValueRulesHook
          fieldPath="chat.reply_timing.talk_value_rules"
          onChange={onChange}
          parentValues={{ enable_talk_value_rules: true }}
          schema={fieldSchema}
          nestedSchema={talkRuleSchema}
          value={[]}
        />,
      )

      await user.click(screen.getByRole('button', { name: '添加发言频率规则' }))
      const dialog = await screen.findByRole('dialog')
      await user.click(within(dialog).getByRole('button', { name: /全局通配/ }))
      await user.click(within(dialog).getByRole('combobox'))
      await user.click(await screen.findByRole('option', { name: '私聊' }))
      await user.click(within(dialog).getByRole('button', { name: '添加' }))

      expect(onChange).toHaveBeenLastCalledWith([
        {
          platform: '*',
          item_id: '*',
          rule_type: 'private',
          time: '00:00-23:59',
          value: 0.5,
        },
      ])
    })

    it('新增发言频率规则支持留空的默认兜底范围', async () => {
      const user = userEvent.setup()
      const onChange = vi.fn()

      render(
        <ChatTalkValueRulesHook
          fieldPath="chat.reply_timing.talk_value_rules"
          onChange={onChange}
          parentValues={{ enable_talk_value_rules: true }}
          schema={fieldSchema}
          nestedSchema={talkRuleSchema}
          value={[]}
        />,
      )

      await user.click(screen.getByRole('button', { name: '添加发言频率规则' }))
      const dialog = await screen.findByRole('dialog')
      await user.click(within(dialog).getByRole('button', { name: /默认兜底/ }))
      await user.click(within(dialog).getByRole('button', { name: '添加' }))

      expect(onChange).toHaveBeenLastCalledWith([
        {
          platform: '',
          item_id: '',
          rule_type: 'group',
          time: '00:00-23:59',
          value: 0.5,
        },
      ])
    })

    it('时间轴按时间段/兜底/* 添加轨道，并支持删除和频率调整', async () => {
      const user = userEvent.setup()
      const onChange = vi.fn()
      const itemId = nextItemId('tl')

      render(
        <ChatTalkValueRulesHook
          fieldPath="chat.reply_timing.talk_value_rules"
          onChange={onChange}
          parentValues={{ enable_talk_value_rules: true }}
          schema={fieldSchema}
          nestedSchema={talkRuleSchema}
          value={[
            talkItem({ item_id: itemId, time: '08:00-12:00', value: 0.2 }),
            talkItem({ item_id: itemId, time: '22:00-02:00', value: 0.6 }),
            talkItem({ item_id: itemId, time: 'bad-time', value: '0.9' }),
            talkItem({ item_id: itemId, time: '*', value: 0.8 }),
          ]}
        />,
      )

      expect(screen.getByText('时间轴视图')).toBeInTheDocument()
      expect(screen.getByText('08:00-12:00')).toBeInTheDocument()
      expect(screen.getByText('22:00-02:00 跨夜')).toBeInTheDocument()
      expect(screen.getByText('时间格式错误')).toBeInTheDocument()
      expect(screen.getByText('强制全天')).toBeInTheDocument()

      await user.click(screen.getByRole('button', { name: '时间段' }))
      expect(onChange.mock.calls.at(-1)?.[0].at(-1)).toMatchObject({
        platform: 'qq',
        item_id: itemId,
        time: '00:00-23:59',
        value: 0.5,
      })

      await user.click(screen.getByRole('button', { name: '兜底' }))
      expect(onChange.mock.calls.at(-1)?.[0].at(-1)).toMatchObject({ time: '', value: 0.5 })

      const wildcardButton = screen.getByRole('button', { name: '*' })
      expect(wildcardButton).toBeDisabled()

      await user.click(screen.getByRole('button', { name: `删除qq:${itemId} · 群聊 轨道 1` }))
      expect(onChange.mock.calls.at(-1)?.[0]).toHaveLength(3)
    })

    it('拖动轨道手柄调整时间，并在同组内重排顺序', async () => {
      const onChange = vi.fn()
      const itemId = nextItemId('drag')
      const first = talkItem({ item_id: itemId, time: '08:00-10:00', value: 0.3 })
      const second = talkItem({ item_id: itemId, time: '14:00-16:00', value: 0.7 })

      render(
        <ChatTalkValueRulesHook
          fieldPath="chat.reply_timing.talk_value_rules"
          onChange={onChange}
          parentValues={{ enable_talk_value_rules: true }}
          schema={fieldSchema}
          nestedSchema={talkRuleSchema}
          value={[first, second]}
        />,
      )

      const startHandle = screen.getByLabelText(`调整qq:${itemId} · 群聊轨道 1 开始时间`)
      const track = startHandle.closest('[data-talk-timeline-track]') as HTMLElement
      vi.spyOn(track, 'getBoundingClientRect').mockReturnValue({
        x: 0,
        y: 0,
        top: 0,
        left: 0,
        right: 240,
        bottom: 28,
        width: 240,
        height: 28,
        toJSON: () => ({}),
      })

      fireEvent.pointerDown(startHandle, { pointerId: 1, clientX: 120, clientY: 10 })
      await act(async () => {
        await new Promise((resolve) => requestAnimationFrame(resolve))
      })
      expect(onChange).toHaveBeenCalled()
      expect(String(onChange.mock.calls.at(-1)?.[0][0].time)).toMatch(/^\d{2}:\d{2}-10:00$/)

      const handle1 = screen.getByLabelText(`拖动qq:${itemId} · 群聊轨道 1 调整顺序`)
      const handle2 = screen.getByLabelText(`拖动qq:${itemId} · 群聊轨道 2 调整顺序`)
      const dataTransfer = { effectAllowed: 'none', setData: vi.fn(), getData: vi.fn() }
      fireEvent.dragStart(handle1, { dataTransfer })
      fireEvent.drop(handle2.closest('.min-h-12') as HTMLElement, { dataTransfer })
      expect(onChange.mock.calls.at(-1)?.[0][0].time).toBe('14:00-16:00')
    })

    it('合并编辑可改组字段、时间类型和发言频率', async () => {
      const user = userEvent.setup()
      const onChange = vi.fn()
      const itemId = nextItemId('group')

      render(
        <ChatTalkValueRulesHook
          fieldPath="chat.reply_timing.talk_value_rules"
          onChange={onChange}
          parentValues={{ enable_talk_value_rules: true }}
          schema={fieldSchema}
          nestedSchema={talkRuleSchema}
          value={[
            talkItem({ platform: '', item_id: '', time: '', value: 0.1 }),
            talkItem({ item_id: itemId, time: '09:00-11:00', value: 0.45 }),
            talkItem({ item_id: itemId, time: '', value: 0.2 }),
            talkItem({ item_id: itemId, time: '*', value: 1.5 }),
          ]}
        />,
      )

      await user.click(screen.getByRole('button', { name: '合并编辑' }))
      expect(screen.getByText('全局 · 群聊')).toBeInTheDocument()
      expect(screen.getByText('精确')).toBeInTheDocument()

      const platformInputs = screen.getAllByPlaceholderText('留空表示全局，* 表示通配')
      fireEvent.change(platformInputs[0], { target: { value: 'wx' } })
      expect(onChange).toHaveBeenCalled()

      const timeButtons = screen.getAllByRole('button', { name: '时间段' })
      await user.click(timeButtons[0])
      expect(onChange.mock.calls.at(-1)?.[0][0]).toMatchObject({ time: '00:00-23:59' })

      fireEvent.change(screen.getAllByPlaceholderText('HH:MM-HH:MM')[1], {
        target: { value: '10:00-12:00' },
      })
      expect(onChange).toHaveBeenCalled()

      fireEvent.change(screen.getByDisplayValue('0.45'), { target: { value: '0.66' } })
      expect(onChange.mock.calls.at(-1)?.[0][1].value).toBe(0.66)

      await user.click(screen.getByRole('button', { name: `删除qq:${itemId} · 群聊轨道 1` }))
      expect(onChange).toHaveBeenCalled()
    })

    it('同一聊天区域重复兜底会被规范化成时间段', async () => {
      const onChange = vi.fn()
      const itemId = nextItemId('dup')

      render(
        <ChatTalkValueRulesHook
          fieldPath="chat.reply_timing.talk_value_rules"
          onChange={onChange}
          parentValues={{ enable_talk_value_rules: true }}
          schema={fieldSchema}
          nestedSchema={talkRuleSchema}
          value={[
            talkItem({ item_id: itemId, time: '', value: 0.2 }),
            talkItem({ item_id: itemId, time: '', value: 0.3 }),
          ]}
        />,
      )

      await userEvent.setup().click(screen.getByRole('button', { name: '合并编辑' }))
      fireEvent.change(screen.getAllByPlaceholderText('留空表示全局，* 表示通配')[0], {
        target: { value: 'kook' },
      })

      const normalized = onChange.mock.calls.at(-1)?.[0] as Array<Record<string, unknown>>
      expect(normalized).toEqual(
        expect.arrayContaining([
          expect.objectContaining({ time: '00:00-23:59', platform: 'kook' }),
          expect.objectContaining({ time: '', platform: 'kook' }),
        ]),
      )
    })

    it('折叠后可再次展开，空列表合并编辑展示占位，并支持取消添加', async () => {
      const user = userEvent.setup()
      const onChange = vi.fn()
      const { rerender } = render(
        <ChatTalkValueRulesHook
          {...talkRulesProps({ parentValues: { enable_talk_value_rules: false }, onChange })}
          value={[]}
        />,
      )

      await user.click(screen.getByRole('button', { name: '展开规则' }))
      expect(screen.getByRole('button', { name: '添加发言频率规则' })).toBeInTheDocument()
      await user.click(screen.getByRole('button', { name: '折叠规则' }))
      expect(screen.getByText('动态发言频率规则未启用，规则列表已折叠。展开后仍可查看或编辑已有规则。')).toBeInTheDocument()

      rerender(
        <ChatTalkValueRulesHook {...talkRulesProps({ onChange })} value={[]} />,
      )
      await user.click(screen.getByRole('button', { name: '合并编辑' }))
      expect(screen.getByText('尚未配置任何规则，将使用全局默认频率。')).toBeInTheDocument()

      await user.click(screen.getByRole('button', { name: '可视化轨道' }))
      await user.click(screen.getByRole('button', { name: '添加发言频率规则' }))
      const dialog = await screen.findByRole('dialog')
      await user.click(within(dialog).getByRole('button', { name: '取消' }))
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()

      await user.click(screen.getByRole('button', { name: '添加发言频率规则' }))
      const addDialog = await screen.findByRole('dialog')
      await user.click(within(addDialog).getByRole('button', { name: /平台通配/ }))
      await user.click(within(addDialog).getByRole('button', { name: '添加' }))
      expect(onChange.mock.calls.at(-1)?.[0][0]).toMatchObject({
        platform: 'qq',
        item_id: '*',
        time: '00:00-23:59',
      })
    })

    it('时间轴可添加强制全天、拖动结束手柄、滑动频率并忽略跨组拖放', async () => {
      const user = userEvent.setup()
      const onChange = vi.fn()
      const firstId = nextItemId('end')
      const secondId = nextItemId('other-group')
      const first = talkItem({ item_id: firstId, time: '08:00-10:00', value: 0.3, rule_type: '' })
      const second = talkItem({
        item_id: firstId,
        time: '14:00-16:00',
        value: 0.7,
        rule_type: '',
      })
      const other = talkItem({ item_id: secondId, time: '09:00-11:00', value: 0.1 })

      const { rerender } = render(
        <ChatTalkValueRulesHook
          {...talkRulesProps({ onChange })}
          value={[first, second, other]}
        />,
      )

      expect(screen.getByText(/未指定/)).toBeInTheDocument()
      await user.click(screen.getAllByRole('button', { name: '*' })[0])
      expect(onChange.mock.calls.at(-1)?.[0].at(-1)).toMatchObject({
        item_id: firstId,
        time: '*',
        value: 0.5,
      })

      const endHandle = screen.getByLabelText(`调整qq:${firstId} · 未指定轨道 1 结束时间`)
      const track = endHandle.closest('[data-talk-timeline-track]') as HTMLElement
      vi.spyOn(track, 'getBoundingClientRect').mockReturnValue({
        x: 0,
        y: 0,
        top: 0,
        left: 0,
        right: 240,
        bottom: 28,
        width: 240,
        height: 28,
        toJSON: () => ({}),
      })

      fireEvent.pointerDown(endHandle, { pointerId: 7, clientX: 200, clientY: 10 })
      fireEvent.pointerMove(endHandle, { pointerId: 7, clientX: 160, clientY: 10 })
      fireEvent.pointerMove(endHandle, { pointerId: 7, clientX: 160, clientY: 10 })
      await act(async () => {
        await new Promise((resolve) => requestAnimationFrame(resolve))
      })
      fireEvent.pointerUp(endHandle, { pointerId: 7, clientX: 160, clientY: 10 })
      expect(onChange).toHaveBeenCalled()

      const sliders = screen.getAllByRole('slider')
      fireEvent.keyDown(sliders[0], { key: 'ArrowRight' })
      expect(onChange).toHaveBeenCalled()

      const handleA = screen.getByLabelText(`拖动qq:${firstId} · 未指定轨道 1 调整顺序`)
      const handleB = screen.getByLabelText(`拖动qq:${secondId} · 群聊轨道 1 调整顺序`)
      const dataTransfer = { effectAllowed: 'none', setData: vi.fn(), getData: vi.fn() }
      fireEvent.dragStart(handleA, { dataTransfer })
      fireEvent.dragOver(handleB.closest('.min-h-12') as HTMLElement, { dataTransfer })
      fireEvent.drop(handleB.closest('.min-h-12') as HTMLElement, { dataTransfer })
      fireEvent.dragEnd(handleA)
      expect(onChange.mock.calls.at(-1)?.[0][0].item_id).toBe(firstId)

      const fallbackItem = talkItem({ item_id: firstId, time: '', value: 0.2, rule_type: '' })
      rerender(
        <ChatTalkValueRulesHook
          {...talkRulesProps({ onChange })}
          value={[first, fallbackItem]}
        />,
      )
      const fallbackHandle = screen.getByLabelText(`拖动qq:${firstId} · 未指定轨道 2 调整顺序`)
      fireEvent.dragStart(fallbackHandle, { dataTransfer })
      fireEvent.drop(
        screen.getByLabelText(`拖动qq:${firstId} · 未指定轨道 1 调整顺序`).closest('.min-h-12') as HTMLElement,
        { dataTransfer },
      )

      rerender(
        <ChatTalkValueRulesHook
          {...talkRulesProps({ onChange })}
          value={[
            talkItem({ item_id: firstId, time: '24:00-10:00', value: 'nope' }),
            talkItem({ item_id: firstId, time: '12:60-13:00', value: null }),
            talkItem({ item_id: firstId, time: '08:00-12:00-99', value: { x: 1 } }),
            talkItem({ item_id: firstId, time: '00:00-00:00', value: 0 }),
          ]}
        />,
      )
      expect(screen.getAllByText('时间格式错误').length).toBeGreaterThan(1)
      expect(screen.getByText('00:00-00:00')).toBeInTheDocument()
    })

    it('合并编辑可切换兜底/强制全天、改聊天类型，非法频率输入被忽略', async () => {
      const user = userEvent.setup()
      const onChange = vi.fn()
      const itemId = nextItemId('merge2')

      render(
        <ChatTalkValueRulesHook
          {...talkRulesProps({ onChange })}
          value={[
            talkItem({ item_id: itemId, time: '09:00-11:00', value: 0.45 }),
            talkItem({ item_id: itemId, time: '', value: 0.2 }),
          ]}
        />,
      )

      await user.click(screen.getByRole('button', { name: '合并编辑' }))
      fireEvent.change(screen.getAllByPlaceholderText('留空表示全局，* 表示通配')[1], {
        target: { value: '888' },
      })
      expect(onChange).toHaveBeenCalled()

      const typeTrigger = screen.getByRole('combobox')
      await user.click(typeTrigger)
      await user.click(await screen.findByRole('option', { name: '私聊' }))
      expect(onChange.mock.calls.at(-1)?.[0][0].rule_type).toBe('private')

      const wildcardButtons = screen.getAllByRole('button', { name: '*' })
      await user.click(wildcardButtons[0])
      expect(onChange.mock.calls.at(-1)?.[0][0].time).toBe('*')

      const fallbackButtons = screen.getAllByRole('button', { name: '兜底' })
      expect(fallbackButtons[0]).toBeDisabled()

      const timeRangeButtons = screen.getAllByRole('button', { name: '时间段' })
      await user.click(timeRangeButtons[0])
      expect(onChange).toHaveBeenCalled()

      fireEvent.change(screen.getByDisplayValue('0.45'), { target: { value: 'abc' } })
      expect(onChange.mock.calls.at(-1)?.[0][0].value).not.toBeNaN()

      const groupedSliders = screen.getAllByRole('slider')
      fireEvent.keyDown(groupedSliders[0], { key: 'ArrowLeft' })
      expect(onChange).toHaveBeenCalled()

      await user.click(wildcardButtons[1])
      const lastItems = onChange.mock.calls.at(-1)?.[0] as Array<Record<string, unknown>>
      expect(lastItems.some((item) => item.time === '*')).toBe(true)
    })

    it('把已有时间段改成重复兜底时会保留当前项并规范化旧兜底', async () => {
      const user = userEvent.setup()
      const onChange = vi.fn()
      const itemId = nextItemId('pref')

      render(
        <ChatTalkValueRulesHook
          {...talkRulesProps({ onChange })}
          value={[
            talkItem({ item_id: itemId, time: '', value: 0.2 }),
            talkItem({ item_id: itemId, time: '08:00-12:00', value: 0.4 }),
          ]}
        />,
      )

      await user.click(screen.getByRole('button', { name: '合并编辑' }))
      fireEvent.change(screen.getByDisplayValue('08:00-12:00'), {
        target: { value: '' },
      })
      const normalized = onChange.mock.calls.at(-1)?.[0] as Array<Record<string, unknown>>
      expect(normalized).toEqual(
        expect.arrayContaining([
          expect.objectContaining({ time: '00:00-23:59' }),
          expect.objectContaining({ time: '' }),
        ]),
      )
    })
  })

  describe('ExpressionGroupsHook 非共享记忆范围', () => {
    it('表达共享组可按范围添加、修改并删除成员', async () => {
      const user = userEvent.setup()
      const onChange = vi.fn()

      const { rerender } = render(
        <ExpressionGroupsHook
          fieldPath="expression.expression_groups"
          onChange={onChange}
          schema={fieldSchema}
          value={[]}
        />,
      )

      expect(screen.getByText('表达共享组')).toBeInTheDocument()
      await user.click(screen.getByLabelText('添加表达共享组'))
      const dialog = await screen.findByRole('dialog')
      await user.click(within(dialog).getByRole('button', { name: /全局通配/ }))
      await user.click(within(dialog).getByRole('button', { name: '添加' }))
      expect(onChange).toHaveBeenLastCalledWith([
        { targets: [{ platform: '*', item_id: '*', rule_type: 'group' }] },
      ])

      rerender(
        <ExpressionGroupsHook
          fieldPath="expression.expression_groups"
          onChange={onChange}
          schema={fieldSchema}
          value={[{ targets: [{ platform: '*', item_id: '*', rule_type: 'group' }] }]}
        />,
      )
      expect(screen.getByText('全部聊天流')).toBeInTheDocument()
      expect(screen.getByText('当前范围不需要填写平台或聊天流 ID。')).toBeInTheDocument()

      await user.click(screen.getByLabelText('添加表达共享组 1 的成员'))
      const memberDialog = await screen.findByRole('dialog')
      await user.click(within(memberDialog).getByRole('button', { name: /^指定平台/ }))
      await user.click(within(memberDialog).getByRole('button', { name: '添加' }))
      expect(onChange.mock.calls.at(-1)?.[0][0].targets).toHaveLength(2)
    })

    it('平台、任意平台目标和指定聊天流展示不同字段', async () => {
      const onChange = vi.fn()
      const chatId = nextItemId('group')
      resolveChatTargetsMock.mockResolvedValue([{ found: false, session: null }])

      render(
        <ExpressionGroupsHook
          fieldPath="expression.expression_groups"
          onChange={onChange}
          schema={fieldSchema}
          value={[
            {
              targets: [
                { platform: 'qq', item_id: '*', rule_type: 'group' },
                { platform: '*', item_id: '555', rule_type: 'private' },
                { platform: 'wx', item_id: chatId, rule_type: 'group' },
              ],
            },
          ]}
        />,
      )

      expect(screen.getByText('qq:全部目标')).toBeInTheDocument()
      expect(screen.getByText('任意平台:555')).toBeInTheDocument()
      expect(screen.getByText(`wx:${chatId}`)).toBeInTheDocument()

      fireEvent.change(screen.getAllByPlaceholderText('群号或用户 ID')[0], {
        target: { value: '666' },
      })
      expect(onChange).toHaveBeenCalled()

      await waitFor(() => {
        expect(screen.getByText('无效的聊天流')).toBeInTheDocument()
      })
    })

    it('兼容旧版 expression_groups / jargon_groups 字段并展示对应标题', async () => {
      render(
        <JargonGroupsHook
          fieldPath="jargon.jargon_groups"
          onChange={vi.fn()}
          schema={fieldSchema}
          value={[{ jargon_groups: [{ platform: 'qq', item_id: '1', type: 'private' }] }]}
        />,
      )
      expect(screen.getByText('黑话共享组')).toBeInTheDocument()
      expect(screen.getByText('qq:1')).toBeInTheDocument()
      expect(screen.getByText('私聊')).toBeInTheDocument()
      await waitFor(() => {
        expect(getBotConfigCachedMock).toHaveBeenCalled()
      })
    })

    it('行为共享组空成员和 Focus 组标题走各自文案', async () => {
      const user = userEvent.setup()
      const onChange = vi.fn()

      render(
        <BehaviorGroupsHook
          fieldPath="behavior.behavior_groups"
          onChange={onChange}
          schema={fieldSchema}
          value={[{ behavior_groups: [] }]}
        />,
      )
      expect(screen.getByText('行为共享组')).toBeInTheDocument()
      expect(screen.getByText('这个行为共享组还没有成员。')).toBeInTheDocument()

      await user.click(screen.getByLabelText('删除行为共享组 1'))
      expect(onChange).toHaveBeenLastCalledWith([])
    })

    it('兼容 expression_groups 字段，并补全空平台聊天流的平台值', async () => {
      const onChange = vi.fn()
      render(
        <ExpressionGroupsHook
          fieldPath="expression.expression_groups"
          onChange={onChange}
          schema={fieldSchema}
          value={[
            'invalid',
            null,
            {
              expression_groups: [
                { platform: '', item_id: '123', type: 'group' },
                { platform: 'qq', item_id: '*', rule_type: 'group' },
                { platform: '*', item_id: '555', rule_type: 'private' },
                { platform: '', item_id: '*', rule_type: 'group' },
                { platform: '*', item_id: '', rule_type: 'group' },
                { platform: '', item_id: '', rule_type: 'private' },
              ],
            },
          ]}
        />,
      )

      expect(screen.getByText('未填写:123')).toBeInTheDocument()
      expect(screen.getByText('qq:全部目标')).toBeInTheDocument()
      expect(screen.getByText('任意平台:555')).toBeInTheDocument()
      expect(screen.getByText('未填写:全部目标')).toBeInTheDocument()
      expect(screen.getByText('任意平台:未填写')).toBeInTheDocument()
      expect(screen.getByText('未填写:未填写')).toBeInTheDocument()
      expect(screen.getAllByText('这个表达共享组还没有成员。')).toHaveLength(2)

      fireEvent.change(screen.getAllByPlaceholderText('群号或用户 ID')[0], {
        target: { value: '999' },
      })
      expect(onChange.mock.calls.at(-1)?.[0][2].targets[0]).toMatchObject({
        platform: 'qq',
        item_id: '999',
      })

      fireEvent.change(screen.getAllByPlaceholderText('qq')[1], {
        target: { value: 'kook' },
      })
      expect(onChange.mock.calls.at(-1)?.[0][2].targets).toEqual(
        expect.arrayContaining([expect.objectContaining({ platform: 'kook', item_id: '*' })]),
      )
    })

    it('Focus 共享组使用独立标题，并可取消添加成员对话框', async () => {
      const user = userEvent.setup()
      const onChange = vi.fn()
      render(
        <BehaviorFocusGroupsHook
          fieldPath="behavior.focus_groups"
          onChange={onChange}
          schema={fieldSchema}
          value={[]}
        />,
      )

      expect(screen.getByText('Focus 共享组')).toBeInTheDocument()
      expect(screen.getByText('配置后只有同组聊天流共享 Focus，不同组可以分别进入 Focus。')).toBeInTheDocument()
      expect(screen.getByText('暂无Focus 共享组，点击上方按钮选择第一个成员。')).toBeInTheDocument()

      await user.click(screen.getByLabelText('添加Focus 共享组'))
      const dialog = await screen.findByRole('dialog')
      await user.click(within(dialog).getByRole('button', { name: /指定聊天流/ }))
      await user.click(within(dialog).getByRole('button', { name: '添加' }))
      expect(onChange).toHaveBeenLastCalledWith([
        { targets: [{ platform: 'qq', item_id: '', rule_type: 'group' }] },
      ])
    })
  })

  describe('共享记忆组', () => {
    it('空列表、全局共享占位和加载聊天流失败', async () => {
      const user = userEvent.setup()
      const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})
      getChatStreamsMock.mockRejectedValueOnce(new Error('聊天流失败'))
      const onParentChange = vi.fn()

      const { rerender } = render(
        <AMemorixSharedMemoryGroupsHook
          fieldPath="a_memorix.shared_memory_groups"
          onChange={vi.fn()}
          onParentChange={onParentChange}
          parentValues={{ global_memory_sharing_enabled: false }}
          schema={sharedMemorySchema}
          value={[]}
        />,
      )

      expect(screen.getByText('暂无共享记忆组，点击上方按钮添加后可直接选择群聊或私聊。')).toBeInTheDocument()
      expect(screen.getByRole('link', { name: '聊天管理' })).toHaveAttribute(
        'href',
        '/chat-management?view=groups&kind=memory',
      )
      await waitFor(() => {
        expect(consoleError).toHaveBeenCalled()
      })

      rerender(
        <AMemorixSharedMemoryGroupsHook
          fieldPath="a_memorix.shared_memory_groups"
          onChange={vi.fn()}
          onParentChange={onParentChange}
          parentValues={{ global_memory_sharing_enabled: true }}
          schema={sharedMemorySchema}
          value={[]}
        />,
      )
      expect(screen.getByText('全局共享已开启，暂不需要配置共享记忆组。')).toBeInTheDocument()
      expect(screen.getByLabelText('添加共享记忆组')).toBeDisabled()

      await user.click(screen.getByRole('switch', { name: '全局共享记忆' }))
      expect(onParentChange).toHaveBeenLastCalledWith('global_memory_sharing_enabled', false)
      consoleError.mockRestore()
    })

    it('可搜索、选择已知聊天流，并支持手动填写与收起', async () => {
      const user = userEvent.setup()
      const onChange = vi.fn()
      const groupChat = createChatStream({
        display_name: '测试群',
        target_id: '10001',
        group_id: '10001',
        group_name: '测试群',
        account_id: 'bot-1',
      })
      const privateChat = createChatStream({
        id: 2,
        session_id: 'session-private',
        display_name: '小明的私聊',
        chat_type: 'private',
        target_id: '20002',
        user_id: '20002',
        user_nickname: '小明',
        group_id: null,
        group_name: null,
      })
      getChatStreamsMock.mockResolvedValue([
        createChatStream({ platform: '', target_id: '1' }),
        createChatStream({ id: 8, platform: 'qq', target_id: '', group_id: '', chat_type: 'group' }),
        // 模拟后端返回未知 chat_type 的历史数据，用于覆盖过滤分支
        { ...groupChat, id: 3, chat_type: 'unknown' } as unknown as ChatStream,
        groupChat,
        { ...groupChat, id: 9, display_name: '旧版测试群' },
        privateChat,
      ])

      const { rerender } = render(
        <AMemorixSharedMemoryGroupsHook
          fieldPath="a_memorix.shared_memory_groups"
          onChange={onChange}
          parentValues={{ global_memory_sharing_enabled: false }}
          schema={sharedMemorySchema}
          value={[{ targets: [{ platform: 'qq', item_id: '', rule_type: 'group' }] }]}
        />,
      )

      await user.click(screen.getByLabelText('展开共享记忆组 1'))
      expect(screen.getByText('未选择聊天流')).toBeInTheDocument()
      expect(screen.getByText('待选择')).toBeInTheDocument()

      await waitFor(() => {
        expect(screen.getByRole('combobox', { name: '选择群聊或私聊' })).toHaveTextContent(
          '搜索或选择已知聊天流',
        )
      })

      await user.click(screen.getByRole('combobox', { name: '选择群聊或私聊' }))
      const searchInput = await screen.findByPlaceholderText('搜索群名、私聊名、群号或用户 ID...')
      await user.type(searchInput, 'zzz-none')
      expect(await screen.findByText('未找到匹配的聊天流')).toBeInTheDocument()

      await user.clear(searchInput)
      await user.type(searchInput, '测试群')
      await user.click(await screen.findByText(/测试群 · 账号 bot-1 · 群聊/))
      expect(onChange.mock.calls.at(-1)?.[0][0].targets[0]).toMatchObject({
        platform: 'qq',
        item_id: '10001',
        rule_type: 'group',
      })

      rerender(
        <AMemorixSharedMemoryGroupsHook
          fieldPath="a_memorix.shared_memory_groups"
          onChange={onChange}
          parentValues={{ global_memory_sharing_enabled: false }}
          schema={sharedMemorySchema}
          value={[{ targets: [{ platform: 'qq', item_id: '10001', rule_type: 'group' }] }]}
        />,
      )
      expect(await screen.findByText('测试群')).toBeInTheDocument()
      expect(screen.queryByText('未选择聊天流')).not.toBeInTheDocument()

      await user.click(screen.getByRole('combobox', { name: '选择群聊或私聊' }))
      expect((await screen.findAllByText(/测试群 · 账号 bot-1 · 群聊/)).length).toBeGreaterThan(0)
      expect(screen.getAllByText(/小明的私聊 · 私聊/).length).toBeGreaterThan(0)
    })

    it('未匹配目标会打开手动填写，匹配成功后收起，删除成员并剪枝折叠状态', async () => {
      const user = userEvent.setup()
      const onChange = vi.fn()
      getChatStreamsMock.mockResolvedValue([
        createChatStream({
          display_name: '匹配群',
          target_id: '10001',
          group_id: '10001',
        }),
      ])

      const { rerender } = render(
        <AMemorixSharedMemoryGroupsHook
          fieldPath="a_memorix.shared_memory_groups"
          onChange={onChange}
          parentValues={{ global_memory_sharing_enabled: false }}
          schema={sharedMemorySchema}
          value={[
            { targets: [{ platform: 'qq', item_id: 'not-found', rule_type: 'group' }] },
            { targets: [{ platform: 'qq', item_id: 'also-missing', rule_type: 'private' }] },
          ]}
        />,
      )

      await user.click(screen.getByLabelText('展开共享记忆组 1'))
      expect(await screen.findByText('未匹配')).toBeInTheDocument()
      expect(screen.getByText('群号或用户 ID')).toBeInTheDocument()

      fireEvent.change(screen.getByPlaceholderText('群号或用户 ID'), {
        target: { value: '30003' },
      })
      expect(onChange.mock.calls.at(-1)?.[0][0].targets[0].item_id).toBe('30003')
      fireEvent.change(screen.getByPlaceholderText('qq'), { target: { value: 'wx' } })
      expect(onChange.mock.calls.at(-1)?.[0][0].targets[0].platform).toBe('wx')
      await user.click(screen.getByRole('combobox', { name: '选择聊天类型' }))
      await user.click(await screen.findByRole('option', { name: '私聊' }))
      expect(onChange.mock.calls.at(-1)?.[0][0].targets[0].rule_type).toBe('private')
      await user.click(screen.getByRole('button', { name: '收起手动填写' }))
      expect(screen.queryByPlaceholderText('群号或用户 ID')).not.toBeInTheDocument()

      rerender(
        <AMemorixSharedMemoryGroupsHook
          fieldPath="a_memorix.shared_memory_groups"
          onChange={onChange}
          parentValues={{ global_memory_sharing_enabled: false }}
          schema={sharedMemorySchema}
          value={[
            { targets: [{ platform: 'qq', item_id: '10001', rule_type: 'group' }] },
            { targets: [{ platform: 'qq', item_id: 'also-missing', rule_type: 'private' }] },
          ]}
        />,
      )
      expect(await screen.findByText('匹配群')).toBeInTheDocument()
      expect(screen.queryByText('群号或用户 ID')).not.toBeInTheDocument()

      await user.click(screen.getByLabelText('添加共享记忆组 1 的成员'))
      const memberDialog = await screen.findByRole('dialog')
      await user.click(within(memberDialog).getByRole('button', { name: '取消' }))
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()

      await user.click(screen.getByLabelText('添加共享记忆组 1 的成员'))
      const addDialog = await screen.findByRole('dialog')
      await user.click(within(addDialog).getByRole('button', { name: '添加' }))
      expect(onChange.mock.calls.at(-1)?.[0][0].targets).toHaveLength(2)

      await user.click(screen.getByLabelText('删除共享记忆组 1 的成员 1'))
      expect(onChange.mock.calls.at(-1)?.[0][0].targets).toHaveLength(0)

      await user.click(screen.getByLabelText('折叠共享记忆组 1'))
      await user.click(screen.getByLabelText('删除共享记忆组 2'))
      rerender(
        <AMemorixSharedMemoryGroupsHook
          fieldPath="a_memorix.shared_memory_groups"
          onChange={onChange}
          parentValues={{ global_memory_sharing_enabled: false }}
          schema={sharedMemorySchema}
          value={[{ targets: [{ platform: 'qq', item_id: '10001', rule_type: 'group' }] }]}
        />,
      )
      expect(screen.getByText('共享记忆组 1')).toBeInTheDocument()
      expect(screen.queryByText('共享记忆组 2')).not.toBeInTheDocument()
    })

    it('无已知聊天流时展示占位，空成员组显示空态', async () => {
      const user = userEvent.setup()
      const onChange = vi.fn()
      getChatStreamsMock.mockResolvedValue([])

      const { rerender } = render(
        <AMemorixSharedMemoryGroupsHook
          fieldPath="a_memorix.shared_memory_groups"
          onChange={onChange}
          parentValues={{ global_memory_sharing_enabled: false }}
          schema={sharedMemorySchema}
          value={[{ targets: [] }]}
        />,
      )

      await user.click(screen.getByLabelText('展开共享记忆组 1'))
      expect(screen.getByText('这个共享记忆组还没有成员。')).toBeInTheDocument()

      await user.click(screen.getByLabelText('添加共享记忆组 1 的成员'))
      const dialog = await screen.findByRole('dialog')
      await user.click(within(dialog).getByRole('button', { name: '添加' }))
      expect(onChange).toHaveBeenLastCalledWith([
        { targets: [{ platform: 'qq', item_id: '', rule_type: 'group' }] },
      ])

      rerender(
        <AMemorixSharedMemoryGroupsHook
          fieldPath="a_memorix.shared_memory_groups"
          onChange={onChange}
          parentValues={{ global_memory_sharing_enabled: false }}
          schema={sharedMemorySchema}
          value={[{ targets: [{ platform: 'qq', item_id: '', rule_type: 'group' }] }]}
        />,
      )
      expect(screen.getByRole('combobox', { name: '选择群聊或私聊' })).toHaveTextContent(
        '暂无已知聊天流',
      )
    })
  })

  describe('其他导出 hook', () => {
    it('HiddenFieldHook 不渲染任何内容', () => {
      const { container } = render(
        <HiddenFieldHook fieldPath="hidden" onChange={vi.fn()} schema={fieldSchema} value={null} />,
      )
      expect(container).toBeEmptyDOMElement()
    })

    it('TalkValueDisabledByJevHook 在非 Jev 模式下原样渲染字段', () => {
      render(
        <TalkValueDisabledByJevHook
          fieldPath="chat.reply_timing.talk_value"
          onChange={vi.fn()}
          parentValues={{ reply_trigger_mode: 'frequency' }}
          schema={fieldSchema}
          value={0.5}
        >
          <span>群聊频率滑块</span>
        </TalkValueDisabledByJevHook>,
      )

      expect(screen.getByText('群聊频率滑块')).toBeInTheDocument()
      expect(screen.queryByText(/Jev 决策模式下回复频率不可用/)).not.toBeInTheDocument()
    })

    it.each(['jev', 'jev_batch'])('TalkValueDisabledByJevHook 在 %s 模式下置灰并提示', (mode) => {
      render(
        <TalkValueDisabledByJevHook
          fieldPath="chat.reply_timing.talk_value"
          onChange={vi.fn()}
          parentValues={{ reply_trigger_mode: mode }}
          schema={fieldSchema}
          value={0.5}
        >
          <span>群聊频率滑块</span>
        </TalkValueDisabledByJevHook>,
      )

      // 字段仍然可见，便于用户看到当前配置值
      expect(screen.getByText('群聊频率滑块')).toBeInTheDocument()
      expect(screen.getByText(/Jev 决策模式下回复频率不可用，是否回复由 Jev 判断结果决定。/)).toBeInTheDocument()
    })

    it('ChatTalkValueRulesHook 在 Jev 模式下置灰规则列表并提示', () => {
      render(
        <ChatTalkValueRulesHook
          fieldPath="chat.reply_timing.talk_value_rules"
          onChange={vi.fn()}
          parentValues={{ reply_trigger_mode: 'jev_batch', enable_talk_value_rules: true }}
          schema={fieldSchema}
          nestedSchema={talkRuleSchema}
          value={[]}
        />,
      )

      expect(screen.getByText(/Jev 决策模式下动态发言频率规则不可用，规则不会参与判断。/)).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: '添加发言频率规则' })).not.toBeInTheDocument()
    })

    it('ChatPromptsHook 添加条目并按行编辑剩余 prompt 字段', async () => {
      const user = userEvent.setup()
      const onChange = vi.fn()

      const { rerender } = render(
        <ChatPromptsHook
          fieldPath="chat.chat_prompts"
          onChange={onChange}
          schema={fieldSchema}
          nestedSchema={promptSchema}
          value={[]}
        />,
      )

      expect(screen.getByText('尚未配置任何聊天额外 Prompt。')).toBeInTheDocument()
      await user.click(screen.getByRole('button', { name: '添加额外 Prompt' }))
      expect(onChange).toHaveBeenCalledWith([
        { platform: 'qq', item_id: '', rule_type: 'group', prompt: '' },
      ])

      rerender(
        <ChatPromptsHook
          fieldPath="chat.chat_prompts"
          onChange={onChange}
          schema={fieldSchema}
          nestedSchema={promptSchema}
          value={[{ platform: 'qq', item_id: '1', rule_type: 'group', prompt: '你好' }]}
        />,
      )

      expect(screen.getByText('qq:1 · 群聊')).toBeInTheDocument()
      fireEvent.change(screen.getByDisplayValue('你好'), { target: { value: '新提示' } })
      expect(onChange.mock.calls.at(-1)?.[0][0].prompt).toBe('新提示')

      fireEvent.change(screen.getByDisplayValue('qq'), { target: { value: '' } })
      fireEvent.change(screen.getByDisplayValue('1'), { target: { value: '' } })
      await user.click(screen.getByRole('combobox'))
      await user.click(await screen.findByRole('option', { name: /private|私聊/ }))
      expect(onChange).toHaveBeenCalled()

      rerender(
        <ChatPromptsHook
          fieldPath="chat.chat_prompts"
          onChange={onChange}
          schema={fieldSchema}
          nestedSchema={promptSchema}
          value={[
            { platform: '', item_id: '', rule_type: '', prompt: '空' },
            { platform: 'qq', item_id: '', rule_type: 'group', prompt: '仅平台' },
            { platform: '', item_id: '9', rule_type: 'private', prompt: '仅ID' },
          ]}
        />,
      )
      expect(screen.getByText('全局 · 未指定')).toBeInTheDocument()
      expect(screen.getByText('qq · 群聊')).toBeInTheDocument()
      expect(screen.getByText('9 · 私聊')).toBeInTheDocument()
      await user.click(screen.getByRole('button', { name: '删除全局 · 未指定' }))
      expect(onChange.mock.calls.at(-1)?.[0]).toHaveLength(2)
    })

    it('KeywordRulesHook 与 RegexRulesHook 规范化隐藏字段并生成标题', async () => {
      const user = userEvent.setup()
      const keywordChange = vi.fn()
      const regexChange = vi.fn()

      const { rerender } = render(
        <KeywordRulesHook
          fieldPath="personality.keyword_rules"
          onChange={keywordChange}
          schema={fieldSchema}
          nestedSchema={keywordSchema}
          value={[]}
        />,
      )
      await user.click(screen.getByRole('button', { name: '添加关键词规则' }))
      expect(keywordChange).toHaveBeenLastCalledWith([{ keywords: [], reaction: '', regex: [] }])

      rerender(
        <KeywordRulesHook
          fieldPath="personality.keyword_rules"
          onChange={keywordChange}
          schema={fieldSchema}
          nestedSchema={keywordSchema}
          value={[{ keywords: ['hi', 'hello'], reaction: '打招呼', regex: ['should-clear'] }]}
        />,
      )
      expect(screen.getByText('关键词 2 条 → 打招呼')).toBeInTheDocument()

      render(
        <RegexRulesHook
          fieldPath="personality.regex_rules"
          onChange={regexChange}
          schema={fieldSchema}
          nestedSchema={keywordSchema}
          value={[{ regex: ['a+'], reaction: '', keywords: ['x'] }]}
        />,
      )
      expect(screen.getByText('正则 1 条 → 未填写反应')).toBeInTheDocument()

      rerender(
        <KeywordRulesHook
          fieldPath="personality.keyword_rules"
          onChange={keywordChange}
          schema={fieldSchema}
          nestedSchema={keywordSchema}
          value={[{ keywords: ['', 1, '  '], reaction: `很长的反应文案${'哈'.repeat(40)}` }]}
        />,
      )
      expect(screen.getByText(/未配置关键词 → 很长的反应文案/)).toBeInTheDocument()
      expect(screen.getByText(/…/)).toBeInTheDocument()

      cleanup()
      render(
        <RegexRulesHook
          fieldPath="personality.regex_rules"
          onChange={regexChange}
          schema={fieldSchema}
          nestedSchema={keywordSchema}
          value={[]}
        />,
      )
      await user.click(screen.getByRole('button', { name: '添加正则规则' }))
      expect(regexChange).toHaveBeenLastCalledWith([{ keywords: [], reaction: '', regex: [] }])
      cleanup()
      render(
        <RegexRulesHook
          fieldPath="personality.regex_rules"
          onChange={regexChange}
          schema={fieldSchema}
          nestedSchema={keywordSchema}
          value={[{ regex: [], reaction: 'ok' }]}
        />,
      )
      expect(screen.getByText('未配置正则 → ok')).toBeInTheDocument()
    })

    it('FocusWhitelistHook 使用兜底 schema 添加默认项', async () => {
      const user = userEvent.setup()
      const onChange = vi.fn()

      const { rerender } = render(
        <FocusWhitelistHook
          fieldPath="focus.whitelist"
          onChange={onChange}
          schema={fieldSchema}
          value={[]}
        />,
      )

      expect(screen.getByText('尚未配置 Focus 白名单。')).toBeInTheDocument()
      await user.click(screen.getByRole('button', { name: '添加 Focus 白名单' }))
      expect(onChange).toHaveBeenLastCalledWith([
        { platform: '', item_id: '', type: 'group', use: true, learn: true },
      ])

      rerender(
        <FocusWhitelistHook
          fieldPath="focus.whitelist"
          onChange={onChange}
          schema={fieldSchema}
          value={[{ platform: 'qq', item_id: '1', type: 'group', use: true, learn: true }]}
        />,
      )
      expect(screen.getByText('qq:1 · 群聊')).toBeInTheDocument()
      await user.click(screen.getByRole('button', { name: '删除qq:1 · 群聊' }))
      expect(onChange).toHaveBeenLastCalledWith([])
      rerender(
        <FocusWhitelistHook
          fieldPath="focus.whitelist"
          onChange={onChange}
          schema={fieldSchema}
          value={null}
        />,
      )
      expect(screen.getByText('尚未配置 Focus 白名单。')).toBeInTheDocument()
    })

    it('BotPlatformAccountsHook 处理加载失败、空列表和备用账号编辑', async () => {
      const user = userEvent.setup()
      const onChange = vi.fn()
      const onParentChange = vi.fn()
      vi.mocked(botAccountsApi.getDiscoveredBotAccounts).mockRejectedValueOnce('读取失败')

      const { rerender } = render(
        <BotPlatformAccountsHook
          fieldPath="bot.platform"
          onChange={onChange}
          onParentChange={onParentChange}
          parentValues={{ qq_account: 123, platforms: ['wx'] }}
          schema={fieldSchema}
          value="qq"
        />,
      )

      expect(await screen.findByText('读取适配器账号失败')).toBeInTheDocument()

      vi.mocked(botAccountsApi.getDiscoveredBotAccounts).mockResolvedValueOnce([])
      rerender(
        <BotPlatformAccountsHook
          fieldPath="bot.platform"
          onChange={onChange}
          onParentChange={onParentChange}
          parentValues={{ qq_account: '123', platforms: ['wx:abc', ''] }}
          schema={fieldSchema}
          value="qq"
        />,
      )
      expect(await screen.findByText('尚未收到适配器上报的账号。')).toBeInTheDocument()

      await user.click(screen.getByRole('button', { name: '备用平台账号' }))
      fireEvent.change(screen.getByPlaceholderText('qq'), { target: { value: 'telegram' } })
      expect(onChange).toHaveBeenCalledWith('telegram')
      fireEvent.change(screen.getByPlaceholderText('2814567326'), { target: { value: '999' } })
      expect(onParentChange).toHaveBeenCalledWith('qq_account', '999')

      fireEvent.change(screen.getByDisplayValue('wx'), { target: { value: 'kook' } })
      expect(onParentChange).toHaveBeenCalledWith('platforms', ['kook:abc', ''])

      await user.click(screen.getByLabelText('添加平台'))
      expect(onParentChange).toHaveBeenCalledWith('platforms', ['wx:abc', '', ''])

      await user.click(screen.getByLabelText('删除其他平台 1'))
      expect(onParentChange).toHaveBeenCalledWith('platforms', [''])
    })

    it('禁用适配器账号失败时展示错误，离线账号显示离线', async () => {
      vi.mocked(botAccountsApi.getDiscoveredBotAccounts).mockResolvedValue([
        {
          id: 8,
          platform: 'qq',
          account_id: 'bot-offline',
          disabled: false,
          first_seen_at: '2026-08-08T08:00:00',
          last_seen_at: '2026-08-08T09:00:00',
          disabled_at: null,
          last_source: 'message',
          last_adapter_id: 'adapter-1',
          last_plugin_id: 'plugin-1',
          last_gateway_name: 'gateway-1',
          online: false,
        },
      ])
      vi.mocked(botAccountsApi.setDiscoveredBotAccountDisabled).mockRejectedValue(new Error('更新失败'))

      render(
        <BotPlatformAccountsHook
          fieldPath="bot.platform"
          onChange={vi.fn()}
          onParentChange={vi.fn()}
          parentValues={{ qq_account: '', platforms: [] }}
          schema={fieldSchema}
          value="qq"
        />,
      )

      expect(await screen.findByText('离线')).toBeInTheDocument()
      expect(screen.getByText(/入站消息/)).toBeInTheDocument()
      await userEvent.click(screen.getByRole('button', { name: '排除身份' }))
      expect(await screen.findByText('更新失败')).toBeInTheDocument()
    })

    it('MCP JSON hook 解析成功后写回对象数组', () => {
      const onChange = vi.fn()
      render(
        <MCPRootItemsHook
          fieldPath="mcp.roots"
          onChange={onChange}
          schema={fieldSchema}
          value={[{ uri: 'file:///tmp' }]}
        />,
      )

      fireEvent.change(screen.getByPlaceholderText(/file:\/\/\/Users\/example\/project/), {
        target: { value: '[{"enabled":true}]' },
      })
      expect(onChange).toHaveBeenCalledWith([{ enabled: true }])
    })

    it('MCPServersHook 解析失败时展示错误，别名非数组时走空列表', async () => {
      const user = userEvent.setup()
      const onChange = vi.fn()
      render(
        <MCPServersHook
          fieldPath="mcp.servers"
          onChange={onChange}
          schema={fieldSchema}
          value={[]}
        />,
      )
      expect(screen.getByText('MCP 服务器配置结构较复杂，使用 JSON 编辑。')).toBeInTheDocument()
      fireEvent.change(screen.getByPlaceholderText(/example-server/), {
        target: { value: '{bad' },
      })
      expect(screen.getByText(/JSON 解析失败：/)).toBeInTheDocument()

      cleanup()
      render(
        <AliasNamesHook fieldPath="bot.alias_names" onChange={onChange} schema={fieldSchema} value={null} />,
      )
      expect(screen.getByText('暂无别名。')).toBeInTheDocument()
      await user.click(screen.getByRole('button', { name: '添加别名' }))
      expect(onChange).toHaveBeenLastCalledWith([''])

      cleanup()
      render(
        <MultipleReplyStyleHook
          fieldPath="personality.multiple_reply_style"
          onChange={onChange}
          schema={fieldSchema}
          value={[]}
        />,
      )
      expect(screen.getByText('暂无备用表达风格。')).toBeInTheDocument()
      await user.click(screen.getByRole('button', { name: '添加表达风格' }))
      expect(onChange).toHaveBeenLastCalledWith([''])
    })

    it('适配器账号加载中、就绪来源、恢复身份和非 Error 更新失败', async () => {
      const user = userEvent.setup()
      const deferred = createDeferred<
        Array<{
          id: number
          platform: string
          account_id: string
          disabled: boolean
          first_seen_at: string
          last_seen_at: string
          disabled_at: string | null
          last_source: string
          last_adapter_id: string
          last_plugin_id: string
          last_gateway_name: string
          online: boolean
        }>
      >()
      vi.mocked(botAccountsApi.getDiscoveredBotAccounts).mockReturnValue(deferred.promise)

      const { rerender } = render(
        <BotPlatformAccountsHook
          fieldPath="bot.platform"
          onChange={vi.fn()}
          onParentChange={vi.fn()}
          parentValues={{ qq_account: { invalid: true }, platforms: 'bad' }}
          schema={fieldSchema}
          value={null}
        />,
      )
      expect(screen.getByText('正在读取适配器账号…')).toBeInTheDocument()

      deferred.resolve([
        {
          id: 3,
          platform: 'qq',
          account_id: 'bot-ready',
          disabled: false,
          first_seen_at: '2026-08-08T08:00:00',
          last_seen_at: '2026-08-08T09:00:00',
          disabled_at: null,
          last_source: 'ready',
          last_adapter_id: 'adapter-1',
          last_plugin_id: 'plugin-1',
          last_gateway_name: 'gateway-1',
          online: true,
        },
        {
          id: 4,
          platform: 'wx',
          account_id: 'bot-disabled',
          disabled: true,
          first_seen_at: '2026-08-08T08:00:00',
          last_seen_at: '2026-08-08T09:00:00',
          disabled_at: '2026-08-08T10:00:00',
          last_source: 'message',
          last_adapter_id: 'adapter-2',
          last_plugin_id: 'plugin-2',
          last_gateway_name: 'gateway-2',
          online: false,
        },
      ])

      expect(await screen.findByText('bot-ready')).toBeInTheDocument()
      expect(screen.getByText(/就绪状态/)).toBeInTheDocument()
      expect(screen.getByRole('button', { name: '已排除账号 1' })).toBeInTheDocument()

      vi.mocked(botAccountsApi.setDiscoveredBotAccountDisabled).mockResolvedValueOnce({
        id: 4,
        platform: 'wx',
        account_id: 'bot-disabled',
        disabled: false,
        first_seen_at: '2026-08-08T08:00:00',
        last_seen_at: '2026-08-08T09:00:00',
        disabled_at: null,
        last_source: 'message',
        last_adapter_id: 'adapter-2',
        last_plugin_id: 'plugin-2',
        last_gateway_name: 'gateway-2',
        online: true,
      })
      await user.click(screen.getByRole('button', { name: '已排除账号 1' }))
      await user.click(screen.getByRole('button', { name: '恢复身份' }))
      await waitFor(() => {
        expect(botAccountsApi.setDiscoveredBotAccountDisabled).toHaveBeenCalledWith(4, false)
      })

      vi.mocked(botAccountsApi.setDiscoveredBotAccountDisabled).mockRejectedValueOnce('boom')
      const excludeButtons = screen.getAllByRole('button', { name: '排除身份' })
      await user.click(excludeButtons[excludeButtons.length - 1])
      expect(await screen.findByText('更新适配器账号失败')).toBeInTheDocument()

      vi.mocked(botAccountsApi.getDiscoveredBotAccounts).mockRejectedValueOnce(new Error('网络错误'))
      rerender(
        <BotPlatformAccountsHook
          fieldPath="bot.platform"
          onChange={vi.fn()}
          onParentChange={vi.fn()}
          parentValues={{ qq_account: 88, platforms: [':only-account', 'plat:'] }}
          schema={fieldSchema}
          value="telegram"
        />,
      )
      await user.click(screen.getByRole('button', { name: '备用平台账号' }))
      fireEvent.change(screen.getAllByPlaceholderText('114514')[0], { target: { value: '200' } })
      fireEvent.change(screen.getAllByPlaceholderText('wx')[0], { target: { value: '' } })

      cleanup()
      vi.mocked(botAccountsApi.getDiscoveredBotAccounts).mockRejectedValueOnce(new Error('网络错误'))
      render(
        <BotPlatformAccountsHook
          fieldPath="bot.platform"
          onChange={vi.fn()}
          schema={fieldSchema}
          value="qq"
        />,
      )
      expect(await screen.findByText('网络错误')).toBeInTheDocument()
    })
  })
})
