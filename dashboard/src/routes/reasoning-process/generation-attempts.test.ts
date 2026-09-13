// @vitest-environment node

import { describe, expect, it } from 'vitest'

import type { ContextItemSnapshot } from '@/lib/reasoning-process-api'

import {
  extractProviderTextParts,
  getProviderItemPayload,
  getProviderItemReadableText,
  getProviderItemTypeLabel,
  getRequestItemDiff,
  parseProviderArguments,
} from './generation-attempts'

function createItem(itemId: string, text: string): ContextItemSnapshot {
  return {
    item_type: 'UserMessageItem',
    meta: {
      item_id: itemId,
      logical_turn_id: null,
      timestamp: '2026-08-06T00:00:00.000Z',
    },
    parts: [{ type: 'text', text }],
  }
}

describe('getProviderItemTypeLabel', () => {
  it.each([
    ['reasoning', '推理'],
    ['message', '消息输出'],
    ['web_search_call', '联网搜索'],
    ['function_call', 'Function 调用'],
    ['function_call_output', 'Function 结果'],
    ['file_search_call', '文件搜索'],
    ['code_interpreter_call', '代码解释器'],
    ['image_generation_call', '图像生成'],
    ['mcp_call', 'MCP 调用'],
    ['mcp_list_tools', 'MCP 工具列表'],
    ['computer_call', '计算机操作'],
    ['shell_call', 'Shell 调用'],
    ['apply_patch_call', '补丁调用'],
    ['custom_tool_call', '自定义工具调用'],
  ] as const)('将 %s 映射为 %s', (itemType, label) => {
    expect(getProviderItemTypeLabel(itemType)).toBe(label)
  })

  it('未知类型回退为原始 type，空字符串回退为未知 Item', () => {
    expect(getProviderItemTypeLabel('video_call')).toBe('video_call')
    expect(getProviderItemTypeLabel('')).toBe('未知 Item')
  })
})

describe('extractProviderTextParts', () => {
  it('字符串按 trim 后决定是否保留原文', () => {
    expect(extractProviderTextParts('  正文  ')).toEqual(['  正文  '])
    expect(extractProviderTextParts('   ')).toEqual([])
    expect(extractProviderTextParts('')).toEqual([])
  })

  it('非对象、非数组值返回空列表', () => {
    expect(extractProviderTextParts(null)).toEqual([])
    expect(extractProviderTextParts(12)).toEqual([])
    expect(extractProviderTextParts(true)).toEqual([])
  })

  it('从对象的 text/refusal 与嵌套 content/summary 递归提取', () => {
    expect(
      extractProviderTextParts({
        text: '可见文本',
        refusal: '拒绝理由',
        content: [{ text: 'content-1' }, { summary: { text: 'summary-1' } }],
        summary: '顶层 summary',
      })
    ).toEqual(['可见文本', '拒绝理由', 'content-1', 'summary-1', '顶层 summary'])
  })

  it('忽略空白 text/refusal，并扁平化嵌套数组', () => {
    expect(
      extractProviderTextParts([
        { text: '  ', refusal: 1 },
        '数组字符串',
        [{ text: '深层' }, ''],
      ])
    ).toEqual(['数组字符串', '深层'])
  })
})

describe('parseProviderArguments', () => {
  it('非字符串原样返回', () => {
    const payload = { city: '上海' }
    expect(parseProviderArguments(payload)).toBe(payload)
    expect(parseProviderArguments(3)).toBe(3)
  })

  it('空白字符串归一为空串，合法 JSON 解析，非法 JSON 保留原文', () => {
    expect(parseProviderArguments('')).toBe('')
    expect(parseProviderArguments('   ')).toBe('')
    expect(parseProviderArguments(' {"q":"天气"} ')).toEqual({ q: '天气' })
    expect(parseProviderArguments('[1,2]')).toEqual([1, 2])
    expect(parseProviderArguments('{not-json')).toBe('{not-json')
  })
})

describe('getProviderItemReadableText', () => {
  it('reasoning 拼接 summary 与 content，message 只读 content', () => {
    expect(
      getProviderItemReadableText(
        {
          summary: [{ text: '思考过程' }],
          content: { text: '推理正文' },
        },
        'reasoning'
      )
    ).toBe('思考过程\n\n推理正文')
    expect(
      getProviderItemReadableText(
        {
          content: [{ text: '第一段' }, { text: '第二段' }],
        },
        'message'
      )
    ).toBe('第一段\n\n第二段')
  })

  it('其他类型或空文本返回空串', () => {
    expect(getProviderItemReadableText({ output: '工具结果' }, 'function_call')).toBe('')
    expect(getProviderItemReadableText({ content: '   ' }, 'message')).toBe('')
  })
})

describe('getProviderItemPayload', () => {
  it('function_call / custom_tool_call 解析 arguments，并按需带上 name', () => {
    expect(
      getProviderItemPayload(
        { name: 'lookup', arguments: '{"q":"hi"}' },
        'function_call'
      )
    ).toEqual({ name: 'lookup', arguments: { q: 'hi' } })
    expect(getProviderItemPayload({ arguments: { raw: true } }, 'custom_tool_call')).toEqual({
      arguments: { raw: true },
    })
  })

  it('优先返回 action；非 message/reasoning 才返回 output', () => {
    expect(getProviderItemPayload({ action: { click: '#ok' } }, 'computer_call')).toEqual({
      click: '#ok',
    })
    expect(getProviderItemPayload({ output: '搜索结果' }, 'web_search_call')).toBe('搜索结果')
    expect(getProviderItemPayload({ output: '不该出现' }, 'message')).toBeNull()
    expect(getProviderItemPayload({ output: '不该出现' }, 'reasoning')).toBeNull()
    expect(getProviderItemPayload({ name: 'noop' }, 'message')).toBeNull()
  })
})

describe('getRequestItemDiff', () => {
  it('内容完全一致时不返回差异', () => {
    const items = [createItem('item-1', '相同内容')]

    expect(getRequestItemDiff(items, structuredClone(items))).toEqual({
      changedOrAdded: [],
      removed: [],
    })
  })

  it('只返回新增、变更和移除的 Item', () => {
    const unchangedItem = createItem('item-1', '保持不变')
    const changedItem = createItem('item-2', '修改后')
    const addedItem = createItem('item-4', '新增')
    const removedItem = createItem('item-3', '被移除')

    expect(
      getRequestItemDiff(
        [unchangedItem, createItem('item-2', '修改前'), removedItem],
        [unchangedItem, changedItem, addedItem]
      )
    ).toEqual({
      changedOrAdded: [changedItem, addedItem],
      removed: [removedItem],
    })
  })
})
