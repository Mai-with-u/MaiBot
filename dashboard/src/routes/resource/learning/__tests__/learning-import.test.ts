import { beforeEach, describe, expect, it, vi } from 'vitest'

const importExpressions = vi.fn()
const importJargons = vi.fn()
const post = vi.fn()

vi.mock('@/lib/expression-api', () => ({
  importExpressions: (...args: unknown[]) => importExpressions(...args),
}))
vi.mock('@/lib/jargon-api', () => ({
  importJargons: (...args: unknown[]) => importJargons(...args),
}))
vi.mock('@/lib/emoji-api', () => ({ getEmojiUploadUrl: () => '/mock-upload' }))
vi.mock('@/lib/http', () => ({ backendApi: { post: (...args: unknown[]) => post(...args) } }))

import { classifyLearningJson, readLearningFiles, runLearningImport } from '../learning-import'

const expressionItem = {
  situation: '引出新话题',
  style: '对了',
  content_list: '[]',
  count: 1,
  last_active_time: null,
  create_time: null,
  checked: false,
  modified_by: null,
}
const jargonItem = {
  content: '麦麦',
  meaning: '机器人',
  count: 1,
  is_jargon: true,
  is_complete: true,
  is_global: false,
  created_by: 'manual' as const,
}

function jsonFile(name: string, payload: unknown) {
  return new File([JSON.stringify(payload)], name, { type: 'application/json' })
}

describe('classifyLearningJson', () => {
  it('按导出类型和字段识别类别', () => {
    expect(
      classifyLearningJson({ type: 'maibot.expression.export', expressions: [expressionItem] })
        ?.kind
    ).toBe('expression')
    expect(
      classifyLearningJson({ type: 'maibot.jargon.export', jargons: [jargonItem] })?.kind
    ).toBe('jargon')
    expect(classifyLearningJson([expressionItem])?.kind).toBe('expression')
    expect(classifyLearningJson([jargonItem])?.kind).toBe('jargon')
    expect(classifyLearningJson({ foo: 1 })).toBeNull()
  })
})

describe('readLearningFiles', () => {
  it('把多个文件归到三类，拒绝无法识别的文件', async () => {
    const bundle = await readLearningFiles([
      jsonFile('expressions.json', {
        type: 'maibot.expression.export',
        expressions: [expressionItem],
      }),
      jsonFile('jargons.json', { type: 'maibot.jargon.export', jargons: [jargonItem, jargonItem] }),
      new File(['x'], 'a.png', { type: 'image/png' }),
      new File(['not json'], 'broken.json', { type: 'application/json' }),
      new File(['x'], 'notes.txt', { type: 'text/plain' }),
    ])
    expect(bundle.expressions).toHaveLength(1)
    expect(bundle.jargons).toHaveLength(2)
    expect(bundle.emojis).toHaveLength(1)
    expect(bundle.rejected.map((item) => item.name)).toEqual(['broken.json', 'notes.txt'])
  })
})

describe('runLearningImport', () => {
  beforeEach(() => {
    importExpressions
      .mockReset()
      .mockResolvedValue({ imported_count: 1, skipped_count: 0, failed_count: 0 })
    importJargons
      .mockReset()
      .mockResolvedValue({ imported_count: 2, skipped_count: 0, failed_count: 0 })
    post.mockReset().mockResolvedValue({})
  })

  it('指定多个聊天时表达方式逐个导入并合计，黑话一次传入全部聊天', async () => {
    const bundle = await readLearningFiles([
      jsonFile('e.json', [expressionItem]),
      jsonFile('j.json', { jargons: [jargonItem] }),
      new File(['x'], 'a.gif', { type: 'image/gif' }),
    ])
    const results = await runLearningImport(bundle, {
      kinds: { expression: true, jargon: true, emoji: true },
      chatIds: ['chat-1', 'chat-2'],
      conflict: 'overwrite',
      emojiEmotion: '开心',
    })
    expect(importExpressions).toHaveBeenNthCalledWith(1, {
      chat_id: 'chat-1',
      expressions: [expressionItem],
    })
    expect(importExpressions).toHaveBeenNthCalledWith(2, {
      chat_id: 'chat-2',
      expressions: [expressionItem],
    })
    expect(importJargons).toHaveBeenCalledWith(
      expect.objectContaining({
        target_session_ids: ['chat-1', 'chat-2'],
        is_global: false,
        conflict_strategy: 'overwrite',
      })
    )
    expect(post).toHaveBeenCalledTimes(1)
    expect(results.map((item) => item.kind)).toEqual(['expression', 'jargon', 'emoji'])
    expect(results[0]).toMatchObject({ imported: 2 })
  })

  it('全局导入、跳过未勾选的类别，一类失败不影响其他类', async () => {
    importExpressions.mockRejectedValue(new Error('boom'))
    const bundle = await readLearningFiles([
      jsonFile('e.json', [expressionItem]),
      jsonFile('j.json', { jargons: [jargonItem] }),
      new File(['x'], 'a.png', { type: 'image/png' }),
    ])
    const results = await runLearningImport(bundle, {
      kinds: { expression: true, jargon: true, emoji: false },
      chatIds: null,
      conflict: 'skip',
      emojiEmotion: '',
    })
    expect(importJargons).toHaveBeenCalledWith(
      expect.objectContaining({ target_session_ids: [], is_global: true })
    )
    expect(post).not.toHaveBeenCalled()
    expect(results[0]).toMatchObject({ kind: 'expression', error: 'boom', failed: 1 })
    expect(results[1]).toMatchObject({ kind: 'jargon', imported: 2 })
  })
})
