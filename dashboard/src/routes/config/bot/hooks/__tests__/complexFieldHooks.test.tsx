import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  AliasNamesHook,
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
  MultipleReplyStyleHook,
  RegexRulesHook,
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
      expect(within(dialog).getByRole('button', { name: '添加' })).toBeDisabled()

      fireEvent.change(within(dialog).getByPlaceholderText('qq'), { target: { value: 'qq' } })
      fireEvent.change(within(dialog).getByPlaceholderText('群号或用户 ID'), { target: { value: '90001' } })
      await user.click(within(dialog).getByRole('combobox', { name: '选择聊天类型' }))
      await user.click(await screen.findByRole('option', { name: '私聊' }))
      await user.click(within(dialog).getByRole('button', { name: '添加' }))

      expect(onChange).toHaveBeenLastCalledWith([
        {
          platform: 'qq',
          item_id: '90001',
          rule_type: 'private',
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
  })

  describe('其他导出 hook', () => {
    it('HiddenFieldHook 不渲染任何内容', () => {
      const { container } = render(
        <HiddenFieldHook fieldPath="hidden" onChange={vi.fn()} schema={fieldSchema} value={null} />,
      )
      expect(container).toBeEmptyDOMElement()
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
    })

    it('FocusWhitelistHook 使用兜底 schema 添加默认项', async () => {
      const user = userEvent.setup()
      const onChange = vi.fn()

      render(
        <FocusWhitelistHook
          fieldPath="focus.whitelist"
          onChange={onChange}
          schema={fieldSchema}
          value={[]}
        />,
      )

      await user.click(screen.getByRole('button', { name: '添加 Focus 白名单' }))
      expect(onChange).toHaveBeenLastCalledWith([
        { platform: '', item_id: '', type: 'group', use: true, learn: true },
      ])
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
  })
})
