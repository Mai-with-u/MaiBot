// @vitest-environment node

import { describe, expect, it } from 'vitest'

import type {
  ContextItemSnapshot,
  ReasoningPromptFile,
  ReasoningPromptSessionInfo,
} from '@/lib/reasoning-process-api'

import {
  buildAvatarFallbackText,
  buildStructuredPromptCopyText,
  decodeSimpleHtmlEntity,
  extractBotSelfNames,
  extractReasoningHeaderMeta,
  formatPromptPreviewText,
  formatSchemaType,
  formatSchemaValue,
  formatSessionType,
  formatStageName,
  getContextItemImages,
  getContextItemReadableText,
  getContextItemRole,
  getContextItemToolCalls,
  getFirstMessageTagAttrs,
  getReasoningRecordTitle,
  getSessionDisplayName,
  getSessionSubtitle,
  getToolCallSourceClassName,
  isBotSelfContextItem,
  normalizeDisplayName,
  normalizeToolCallForDisplay,
  normalizeToolDefinition,
  parseMessageTagAttributes,
  parseNaturalTextBlocks,
  toStringList,
} from './context-items'
import type { StructuredPromptPayload } from './schema'

const itemMeta = {
  item_id: 'item-1',
  logical_turn_id: null,
  timestamp: '2026-08-10T00:00:00.000Z',
}

function createItem(overrides: Partial<ContextItemSnapshot> = {}): ContextItemSnapshot {
  return {
    item_type: 'UserMessageItem',
    meta: itemMeta,
    ...overrides,
  }
}

function createPayload(
  overrides: Partial<StructuredPromptPayload> = {}
): StructuredPromptPayload {
  return {
    request_items: [],
    output_items: [],
    generation_attempts: [],
    ...overrides,
  }
}

function createPromptFile(overrides: Partial<ReasoningPromptFile> = {}): ReasoningPromptFile {
  return {
    stage: 'planner',
    session_id: 'sess-1',
    resolved_session_id: 'resolved-sess-1',
    session_display_name: '测试群',
    platform: 'qq',
    chat_type: 'group',
    target_id: '10001',
    stem: 'stem-1',
    timestamp: 1,
    text_path: null,
    html_path: null,
    json_path: null,
    output_preview: null,
    action_preview: null,
    display_title: '标题',
    related_json_paths: [],
    has_behavior_choice_insert: false,
    model_name: null,
    duration_ms: null,
    prompt_tokens: null,
    completion_tokens: null,
    total_tokens: null,
    size: 1,
    modified_at: 1,
    ...overrides,
  }
}

function createSessionInfo(
  overrides: Partial<ReasoningPromptSessionInfo> = {}
): ReasoningPromptSessionInfo {
  return {
    name: 'sess-1',
    platform: 'qq',
    chat_type: 'group',
    target_id: '10001',
    resolved_session_id: 'resolved1',
    display_name: '测试群',
    account_id: '123',
    matched_current_account: true,
    ...overrides,
  }
}

describe('getContextItemImages', () => {
  it('提取日志中省略 base64 后保留的本地图片路径', () => {
    const item: ContextItemSnapshot = {
      item_type: 'UserMessageItem',
      meta: itemMeta,
      parts: [
        { type: 'text', text: '请选择图片' },
        {
          type: 'image',
          image_format: 'png',
          size_bytes: 1024,
          base64_omitted: true,
          image_path: 'data/prompt_imgs/example.png',
        },
      ],
    }

    expect(getContextItemImages(item)).toEqual([
      {
        path: 'data/prompt_imgs/example.png',
        mimeType: 'image/png',
        sizeBytes: 1024,
      },
    ])
  })

  it('兼容引用对象中的图片路径，忽略 Base64 和非消息 Item', () => {
    const messageItem: ContextItemSnapshot = {
      item_type: 'UserMessageItem',
      meta: itemMeta,
      parts: [
        { type: 'input_image', format: 'jpeg', image_base64: 'YWJj' },
        {
          type: 'input_image',
          format: 'jpeg',
          image_reference: { image_path: 'data/prompt_imgs/reference.jpg' },
        },
      ],
    }
    const reasoningItem: ContextItemSnapshot = {
      item_type: 'ReasoningItem',
      meta: itemMeta,
      parts: [{ type: 'image', image_path: 'data/prompt_imgs/hidden.png' }],
    }

    expect(getContextItemImages(messageItem)).toEqual([
      {
        path: 'data/prompt_imgs/reference.jpg',
        mimeType: 'image/jpeg',
        sizeBytes: undefined,
      },
    ])
    expect(getContextItemImages(reasoningItem)).toEqual([])
  })

  it('兼容 image_url、image/ 前缀格式，并跳过无路径或非图片 part', () => {
    const item = createItem({
      item_type: 'AssistantMessageItem',
      parts: [
        { type: 'IMAGE_URL', image_format: 'image/webp', image_path: 'data/prompt_imgs/a.webp' },
        { type: 'image', format: '  ', image_path: 'data/prompt_imgs/fallback.png' },
        { type: 'image', image_format: 'image/', image_path: 'data/prompt_imgs/empty-format.png' },
        { type: 'image', image_reference: 'not-a-record' },
        { type: 'image', image_path: '   ' },
        { type: 'text', text: '不是图片' },
        { image_path: 'data/prompt_imgs/no-type.png' },
        {
          type: 'image',
          format: 'gif',
          size_bytes: '1024',
          image_path: 'data/prompt_imgs/no-size.gif',
        },
      ],
    })

    expect(getContextItemImages(item)).toEqual([
      { path: 'data/prompt_imgs/a.webp', mimeType: 'image/webp', sizeBytes: undefined },
      { path: 'data/prompt_imgs/fallback.png', mimeType: 'image/png', sizeBytes: undefined },
      { path: 'data/prompt_imgs/empty-format.png', mimeType: 'image/png', sizeBytes: undefined },
      { path: 'data/prompt_imgs/no-size.gif', mimeType: 'image/gif', sizeBytes: undefined },
    ])
  })

  it('消息 Item 缺少 parts 时返回空列表', () => {
    expect(getContextItemImages(createItem({ item_type: 'SystemMessageItem' }))).toEqual([])
  })
})

describe('formatStageName', () => {
  it.each([
    ['behavior_consolidator', '行为整合'],
    ['behavior_feedback', '行为反馈'],
    ['behavior_learner', '行为学习'],
    ['behavior_scenario_analyzer', '行为场景分析'],
    ['behavior_selector', '行为选择'],
    ['emotion', '表情包发送'],
    ['expression_learner', '表达学习'],
    ['expression_selection', '表达选择'],
    ['expression_selector', '表达选择'],
    ['jargon_learner', '黑话抽取'],
    ['jargon_learning_update', '黑话含义推断'],
    ['llm_error', 'LLM 请求异常'],
    ['planner', '规划器'],
    ['reply_effect_judge', '回复效果评估'],
    ['replyer', '回复器'],
    ['timing_gate', '时机判断'],
    ['custom_stage', 'custom_stage'],
  ] as const)('将 %s 格式化为 %s', (stage, label) => {
    expect(formatStageName(stage)).toBe(label)
  })
})

describe('getContextItemRole', () => {
  it.each([
    ['SystemMessageItem', 'system'],
    ['UserMessageItem', 'user'],
    ['AssistantMessageItem', 'assistant'],
    ['ReasoningItem', 'reasoning'],
    ['FunctionCallItem', 'function_call'],
    ['FunctionCallOutputItem', 'tool'],
    ['ProviderActivityItem', 'provider_activity'],
    ['ProviderOpaqueItem', 'provider_opaque'],
    ['UnknownItem', 'UnknownItem'],
  ] as const)('%s 映射为 %s', (itemType, role) => {
    expect(getContextItemRole(createItem({ item_type: itemType }))).toBe(role)
  })
})

describe('getContextItemReadableText', () => {
  it('拼接系统/用户/助手消息的 parts 文本', () => {
    expect(
      getContextItemReadableText(
        createItem({
          item_type: 'SystemMessageItem',
          parts: [{ type: 'text', text: '系统提示' }],
        })
      )
    ).toBe('系统提示')
  })

  it('优先使用 ReasoningItem 的 summary_parts，否则回退 text_parts', () => {
    expect(
      getContextItemReadableText(
        createItem({
          item_type: 'ReasoningItem',
          summary_parts: ['摘要甲', '摘要乙'],
          text_parts: ['不会使用'],
        })
      )
    ).toBe('摘要甲\n摘要乙')
    expect(
      getContextItemReadableText(
        createItem({
          item_type: 'ReasoningItem',
          summary_parts: [],
          text_parts: ['正文甲', '正文乙'],
        })
      )
    ).toBe('正文甲\n正文乙')
    expect(getContextItemReadableText(createItem({ item_type: 'ReasoningItem' }))).toBe('')
  })

  it('读取工具输出和 Provider 摘要，其它类型返回空串', () => {
    expect(
      getContextItemReadableText(
        createItem({ item_type: 'FunctionCallOutputItem', output: '工具结果' })
      )
    ).toBe('工具结果')
    expect(getContextItemReadableText(createItem({ item_type: 'FunctionCallOutputItem' }))).toBe(
      ''
    )
    expect(
      getContextItemReadableText(
        createItem({ item_type: 'ProviderActivityItem', display_summary: '活动摘要' })
      )
    ).toBe('活动摘要')
    expect(
      getContextItemReadableText(
        createItem({ item_type: 'ProviderOpaqueItem', display_summary: '不透明摘要' })
      )
    ).toBe('不透明摘要')
    expect(getContextItemReadableText(createItem({ item_type: 'FunctionCallItem' }))).toBe('')
  })
})

describe('getContextItemToolCalls', () => {
  it('非 FunctionCallItem 或缺少 tool_call 时返回空数组', () => {
    expect(getContextItemToolCalls(createItem({ item_type: 'UserMessageItem' }))).toEqual([])
    expect(getContextItemToolCalls(createItem({ item_type: 'FunctionCallItem' }))).toEqual([])
    expect(
      getContextItemToolCalls(
        createItem({
          item_type: 'FunctionCallItem',
          tool_call: 1 as unknown as Record<string, unknown>,
        })
      )
    ).toEqual([])
  })

  it('从 tool_call 和 extra_content 组装展示结构', () => {
    expect(
      getContextItemToolCalls(
        createItem({
          item_type: 'FunctionCallItem',
          tool_call: {
            call_id: 'call-1',
            func_name: 'lookup',
            args: { q: '晚饭' },
            extra_content: {
              tool_call_source: 'reasoning',
              tool_call_source_label: '推理中调用',
            },
          },
        })
      )
    ).toEqual([
      {
        id: 'call-1',
        function: { name: 'lookup', arguments: { q: '晚饭' } },
        source: 'reasoning',
        source_label: '推理中调用',
        extra_content: {
          tool_call_source: 'reasoning',
          tool_call_source_label: '推理中调用',
        },
      },
    ])
  })

  it('extra_content 非对象且缺少 args 时使用空对象兜底', () => {
    expect(
      getContextItemToolCalls(
        createItem({
          item_type: 'FunctionCallItem',
          tool_call: {
            call_id: 'call-2',
            func_name: 'ping',
            extra_content: 'invalid',
          },
        })
      )
    ).toEqual([
      {
        id: 'call-2',
        function: { name: 'ping', arguments: {} },
        source: undefined,
        source_label: undefined,
        extra_content: {},
      },
    ])
  })
})

describe('buildStructuredPromptCopyText', () => {
  it('空 payload 返回空串', () => {
    expect(buildStructuredPromptCopyText(null)).toBe('')
    expect(buildStructuredPromptCopyText(createPayload())).toBe('')
  })

  it('拼接元信息、调用链、输出、请求 Items 和工具定义', () => {
    const functionCallItem = createItem({
      item_type: 'FunctionCallItem',
      tool_call: {
        call_id: 'call-1',
        func_name: 'lookup',
        args: { q: 1 },
        extra_content: {},
      },
    })
    const emptyOutputItem = createItem({ item_type: 'FunctionCallItem' })
    const text = buildStructuredPromptCopyText(
      createPayload({
        request: { kind: 'planner', selection_reason: '选这个模型' },
        metadata: { model_name: 'gpt-test', duration_ms: 12 },
        presentation: { output_title: '规划结果' },
        generation_attempts: [{ attempt_id: 'att-1' }] as StructuredPromptPayload['generation_attempts'],
        output_items: [
          createItem({
            item_type: 'AssistantMessageItem',
            parts: [{ type: 'text', text: '你好' }],
          }),
          functionCallItem,
          emptyOutputItem,
        ],
        request_items: [
          createItem({
            item_type: 'UserMessageItem',
            parts: [{ type: 'text', text: '今晚吃什么' }],
          }),
          functionCallItem,
          emptyOutputItem,
        ],
        tool_definitions: [{ name: 'lookup' }],
      })
    )

    expect(text).toContain('[元信息]')
    expect(text).toContain('请求类型：planner')
    expect(text).toContain('选择原因：选这个模型')
    expect(text).toContain('模型：gpt-test')
    expect(text).toContain('耗时：12 ms')
    expect(text).toContain('[Provider 调用链]')
    expect(text).toContain('[规划结果]')
    expect(text).toContain('#1 AssistantMessageItem\n你好')
    expect(text).toContain('lookup')
    expect(text).toContain('[请求 Items]')
    expect(text).toContain('今晚吃什么')
    expect(text).toContain('[工具定义]')
    expect(text).toContain('='.repeat(80))
  })

  it('缺少可选元信息时使用默认输出标题，且不写空段落', () => {
    const text = buildStructuredPromptCopyText(
      createPayload({
        request: {},
        metadata: { duration_ms: 'slow' as unknown as number },
        output_items: [createItem({ item_type: 'FunctionCallItem' })],
      })
    )

    expect(text).not.toContain('[元信息]')
    expect(text).toContain('[输出 Items]')
    expect(text).not.toContain('[请求 Items]')
    expect(text).not.toContain('[工具定义]')
    expect(text).not.toContain('[Provider 调用链]')
  })
})

describe('normalizeToolCallForDisplay', () => {
  it('非对象输入使用 unknown 名称和空参数', () => {
    expect(normalizeToolCallForDisplay('bad')).toEqual({
      id: '',
      name: 'unknown',
      arguments: {},
      source: '',
      sourceLabel: '',
    })
  })

  it('从 function / extra_content 读取字段，并翻译 reasoning/response 来源', () => {
    expect(
      normalizeToolCallForDisplay({
        id: 'call-1',
        function: { name: 'search', arguments: { q: 'a' } },
        extra_content: { tool_call_source: 'REASONING' },
      })
    ).toEqual({
      id: 'call-1',
      name: 'search',
      arguments: { q: 'a' },
      source: 'reasoning',
      sourceLabel: '推理中调用',
    })
    expect(
      normalizeToolCallForDisplay({
        call_id: 'call-2',
        name: 'reply',
        arguments: { text: 'hi' },
        tool_call_source: 'response',
      })
    ).toMatchObject({
      id: 'call-2',
      name: 'reply',
      source: 'response',
      sourceLabel: '正文调用',
    })
  })

  it('按 source / source_label / func_name / args 逐级回退', () => {
    expect(
      normalizeToolCallForDisplay({
        func_name: 'fallback',
        args: { n: 1 },
        source: 'provider',
        source_label: '供应商调用',
      })
    ).toEqual({
      id: '',
      name: 'fallback',
      arguments: { n: 1 },
      source: 'provider',
      sourceLabel: '供应商调用',
    })
    expect(
      normalizeToolCallForDisplay({
        tool_call_source: 'other',
        tool_call_source_label: '其它来源',
        function: 'not-a-record',
      })
    ).toMatchObject({
      name: 'unknown',
      source: 'other',
      sourceLabel: '其它来源',
    })
  })
})

describe('getToolCallSourceClassName', () => {
  it('按来源返回对应样式类', () => {
    expect(getToolCallSourceClassName('reasoning')).toContain('teal')
    expect(getToolCallSourceClassName('response')).toContain('amber')
    expect(getToolCallSourceClassName('provider')).toContain('sky')
    expect(getToolCallSourceClassName('other')).toContain('muted')
  })
})

describe('formatSchemaType / formatSchemaValue / toStringList', () => {
  it('识别联合类型、普通类型、数组、enum 和 unknown', () => {
    expect(formatSchemaType({ type: ['string', 'null'] })).toBe('string | null')
    expect(formatSchemaType({ type: 'integer' })).toBe('integer')
    expect(formatSchemaType({ items: { type: 'string' } })).toBe('string[]')
    expect(formatSchemaType({ items: { items: { type: 'number' } } })).toBe('number[][]')
    expect(formatSchemaType({ enum: ['a', 'b'] })).toBe('enum')
    expect(formatSchemaType({})).toBe('unknown')
    expect(formatSchemaType({ type: 1, items: 'nope' })).toBe('unknown')
  })

  it('按值类型格式化 schema 默认值', () => {
    expect(formatSchemaValue(undefined)).toBe('')
    expect(formatSchemaValue('plain')).toBe('plain')
    expect(formatSchemaValue({ a: 1 })).toBe('{"a":1}')
    expect(formatSchemaValue(3)).toBe('3')
  })

  it('只把数组转成字符串列表', () => {
    expect(toStringList('nope')).toEqual([])
    expect(toStringList(null)).toEqual([])
    expect(toStringList([1, 'a', true])).toEqual(['1', 'a', 'true'])
  })
})

describe('normalizeToolDefinition', () => {
  it('非对象输入返回未命名工具', () => {
    expect(normalizeToolDefinition('bad')).toEqual({
      name: '未命名工具',
      type: 'function',
      description: '',
      parameters: [],
      raw: 'bad',
    })
  })

  it('解析 OpenAI function 包装和参数元数据', () => {
    const raw = {
      type: 'function',
      function: {
        name: 'search',
        description: '搜索工具',
        parameters: {
          required: ['query'],
          properties: {
            query: { type: 'string', description: '查询词' },
            mode: { type: ['string', 'null'], enum: ['fast', 'slow'], default: 'fast' },
            count: { type: 'integer', default: 3 },
            tags: { items: { type: 'string' } },
            status: { enum: ['on', 'off'] },
            extra: { description: 123 },
            weird: 'not-a-schema',
          },
        },
      },
    }

    const view = normalizeToolDefinition(raw)
    expect(view).toMatchObject({
      name: 'search',
      type: 'function',
      description: '搜索工具',
      raw,
    })
    expect(view.parameters).toEqual([
      {
        name: 'query',
        type: 'string',
        description: '查询词',
        required: true,
        enumValues: [],
        defaultValue: '',
      },
      {
        name: 'mode',
        type: 'string | null',
        description: '',
        required: false,
        enumValues: ['fast', 'slow'],
        defaultValue: 'fast',
      },
      {
        name: 'count',
        type: 'integer',
        description: '',
        required: false,
        enumValues: [],
        defaultValue: '3',
      },
      {
        name: 'tags',
        type: 'string[]',
        description: '',
        required: false,
        enumValues: [],
        defaultValue: '',
      },
      {
        name: 'status',
        type: 'enum',
        description: '',
        required: false,
        enumValues: ['on', 'off'],
        defaultValue: '',
      },
      {
        name: 'extra',
        type: 'unknown',
        description: '',
        required: false,
        enumValues: [],
        defaultValue: '',
      },
      {
        name: 'weird',
        type: 'unknown',
        description: '',
        required: false,
        enumValues: [],
        defaultValue: '',
      },
    ])
  })

  it('没有 function 包装时直接读取顶层字段', () => {
    expect(
      normalizeToolDefinition({
        name: 123,
        description: null,
        parameters: { required: 'query', properties: null },
      })
    ).toEqual({
      name: '未命名工具',
      type: 'function',
      description: '',
      parameters: [],
      raw: {
        name: 123,
        description: null,
        parameters: { required: 'query', properties: null },
      },
    })
  })
})

describe('normalizeDisplayName / extractBotSelfNames / isBotSelfContextItem', () => {
  it('规范化展示名为小写去空白', () => {
    expect(normalizeDisplayName('  麦麦  ')).toBe('麦麦')
    expect(normalizeDisplayName('MaiMai')).toBe('maimai')
  })

  it('从系统提示提取关注名、本名和别名，并默认包含麦麦', () => {
    const names = extractBotSelfNames(
      createPayload({
        request_items: [
          createItem({
            item_type: 'UserMessageItem',
            parts: [{ type: 'text', text: '你的名字是不会提取' }],
          }),
          createItem({
            item_type: 'SystemMessageItem',
            parts: [
              {
                type: 'text',
                text: '你需要关注    与用户。你的名字是MaiMai。也有人叫你小麦、，Mai，麦麦酱。',
              },
            ],
          }),
          createItem({
            item_type: 'SystemMessageItem',
            parts: [{ type: 'text', text: '你需要关注 焦点名 与用户聊天' }],
          }),
        ],
      })
    )

    expect(names.has('麦麦')).toBe(true)
    expect(names.has('maimai')).toBe(true)
    expect(names.has('小麦')).toBe(true)
    expect(names.has('mai')).toBe(true)
    expect(names.has('麦麦酱')).toBe(true)
    expect(names.has('焦点名')).toBe(true)
    expect(extractBotSelfNames(null).has('麦麦')).toBe(true)
  })

  it('仅当 UserMessageItem 的 message.user 命中 bot 名称时判定为自身', () => {
    const botSelfNames = new Set(['麦麦', 'maimai'])
    expect(
      isBotSelfContextItem(
        createItem({
          item_type: 'SystemMessageItem',
          parts: [{ type: 'text', text: '<message user="麦麦">hi</message>' }],
        }),
        botSelfNames
      )
    ).toBe(false)
    expect(
      isBotSelfContextItem(
        createItem({
          parts: [{ type: 'text', text: '<message user="麦麦">hi</message>' }],
        }),
        botSelfNames
      )
    ).toBe(true)
    expect(
      isBotSelfContextItem(
        createItem({
          parts: [{ type: 'text', text: '<message user="张三">hi</message>' }],
        }),
        botSelfNames
      )
    ).toBe(false)
    expect(isBotSelfContextItem(createItem({ parts: [{ type: 'text', text: '无标签' }] }), botSelfNames)).toBe(
      false
    )
  })
})

describe('getFirstMessageTagAttrs / parseMessageTagAttributes / decodeSimpleHtmlEntity', () => {
  it('没有 message 标签时返回空对象', () => {
    expect(getFirstMessageTagAttrs('普通文本')).toEqual({})
  })

  it('解析首个 message 标签属性并解码实体', () => {
    expect(
      getFirstMessageTagAttrs(
        '前缀 <MESSAGE user="&quot;麦麦&amp;bot&quot;" time="12:00" msg_id="m1">正文</message>'
      )
    ).toEqual({
      user: '"麦麦&bot"',
      time: '12:00',
      msg_id: 'm1',
    })
  })

  it('按顺序解码简单 HTML 实体', () => {
    expect(decodeSimpleHtmlEntity('&quot;&apos;&lt;&gt;&amp;')).toBe(`"'<>&`)
    expect(decodeSimpleHtmlEntity('&amp;lt;')).toBe('&lt;')
  })

  it('忽略无引号属性', () => {
    expect(parseMessageTagAttributes('user=麦麦 flag')).toEqual({})
    expect(parseMessageTagAttributes('data-id="a1" x:y="z"')).toEqual({
      'data-id': 'a1',
      'x:y': 'z',
    })
  })
})

describe('parseNaturalTextBlocks', () => {
  it('没有 message 标签时返回整段文本', () => {
    expect(parseNaturalTextBlocks('纯文本')).toEqual([{ type: 'text', text: '纯文本' }])
  })

  it('拆出标签前的文本、消息体，并丢掉空白文本和无内容空标签', () => {
    expect(
      parseNaturalTextBlocks(
        '前言 <message user="张三">你好</message><message></message>   <message user="李四"></message>'
      )
    ).toEqual([
      { type: 'text', text: '前言 ' },
      { type: 'message', attrs: { user: '张三' }, body: '你好' },
      { type: 'message', attrs: { user: '李四' }, body: '' },
    ])
  })

  it('闭合标签后的尾巴并入当前消息体', () => {
    expect(parseNaturalTextBlocks('<message user="a">hi</message> extra')).toEqual([
      { type: 'message', attrs: { user: 'a' }, body: 'hi</message> extra' },
    ])
  })
})

describe('formatSessionType / getSessionDisplayName / getSessionSubtitle', () => {
  it('格式化会话类型', () => {
    expect(formatSessionType('group')).toBe('群聊')
    expect(formatSessionType('private')).toBe('私聊')
    expect(formatSessionType('channel')).toBe('未知类型')
  })

  it('按 display_name / fallback / sessionName 选择展示名', () => {
    expect(getSessionDisplayName('sess-1', createSessionInfo(), '备用名')).toBe('测试群')
    expect(
      getSessionDisplayName('sess-1', createSessionInfo({ display_name: '' }), '备用名')
    ).toBe('备用名')
    expect(getSessionDisplayName('sess-1')).toBe('sess-1')
    expect(getSessionDisplayName('sess-1', undefined, null)).toBe('sess-1')
  })

  it('拼接平台、账号和会话短 ID，缺失真实会话时给出提示', () => {
    expect(getSessionSubtitle()).toBe('')
    expect(getSessionSubtitle(createSessionInfo())).toBe('qq · 群聊 · 账号 123 · 会话 resolved')
    expect(
      getSessionSubtitle(
        createSessionInfo({
          platform: '',
          account_id: null,
          resolved_session_id: null,
          chat_type: 'private',
        })
      )
    ).toBe('未解析到真实会话')
  })
})

describe('extractReasoningHeaderMeta / getReasoningRecordTitle / formatPromptPreviewText', () => {
  it('空文本返回空元信息', () => {
    expect(extractReasoningHeaderMeta()).toEqual({
      sessionId: '',
      callId: '',
      remainingText: '',
    })
    expect(extractReasoningHeaderMeta('')).toEqual({
      sessionId: '',
      callId: '',
      remainingText: '',
    })
  })

  it('提取会话 ID、调用 ID，并保留其余行', () => {
    expect(
      extractReasoningHeaderMeta(
        '会话 ID：sess-abc\n调用 ID: call-1\n今晚吃什么\n其它：保留该行'
      )
    ).toEqual({
      sessionId: 'sess-abc',
      callId: 'call-1',
      remainingText: '今晚吃什么\n其它：保留该行',
    })
  })

  it('组合阶段、会话名和标题，完整身份信息时追加平台/类型/目标', () => {
    expect(
      getReasoningRecordTitle(
        createPromptFile(),
        createSessionInfo({ display_name: '展示群' })
      )
    ).toBe('规划器/展示群/标题/qq/群聊/10001')

    expect(
      getReasoningRecordTitle(
        createPromptFile({
          platform: null,
          chat_type: null,
          target_id: null,
          display_title: null,
          session_display_name: null,
        })
      )
    ).toBe('规划器/sess-1/stem-1')

    expect(
      getReasoningRecordTitle(
        createPromptFile({
          platform: null,
          chat_type: null,
          target_id: null,
          session_display_name: null,
        }),
        createSessionInfo({
          platform: 'telegram',
          chat_type: 'private',
          target_id: 'u-9',
          display_name: '',
        })
      )
    ).toBe('规划器/sess-1/标题/telegram/私聊/u-9')
  })

  it('去掉预览文案开头的动作前缀', () => {
    expect(formatPromptPreviewText('动作：安排晚饭')).toBe('安排晚饭')
    expect(formatPromptPreviewText('动作:  安排午饭')).toBe('安排午饭')
    expect(formatPromptPreviewText('回复：保持原样')).toBe('回复：保持原样')
  })
})

describe('buildAvatarFallbackText', () => {
  it('优先取展示名首字符，否则用户 ID 末两位，都空则用“用”', () => {
    expect(buildAvatarFallbackText('alice', '99')).toBe('A')
    expect(buildAvatarFallbackText('麦麦', '')).toBe('麦')
    expect(buildAvatarFallbackText('  ', '12345')).toBe('45')
    expect(buildAvatarFallbackText('', '')).toBe('用')
    expect(buildAvatarFallbackText('   ', '  ')).toBe('用')
  })
})
