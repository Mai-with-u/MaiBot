import { describe, expect, it } from 'vitest'

import type {
  MemoryFeedbackActionLogPayload,
  MemoryFeedbackCorrectionDetailTaskPayload,
  MemoryFeedbackCorrectionSummaryPayload,
} from '@/lib/memory-api'

import type { DeleteOperationItem } from './utils'
import {
  buildFeedbackImpactSummary,
  describeFeedbackActionLog,
  formatDeleteOperationMode,
  formatDeleteOperationStatus,
  formatDeleteOperationTime,
  formatDeleteRelationText,
  formatFeedbackActionType,
  formatFeedbackDecision,
  formatFeedbackRelationTriplet,
  formatFeedbackRollbackStatus,
  formatFeedbackTaskStatus,
  formatImportTime,
  formatProgressPercent,
  getDeleteOperationItemLabel,
  getDeleteOperationItemPreview,
  getDeleteOperationItemSource,
  getFeedbackCorrectionPreview,
  getFeedbackStatusVariant,
  getImportStatusLabel,
  getImportStatusVariant,
  getImportStepLabel,
  normalizeImportInputMode,
  normalizeProgress,
  parseCommaSeparatedList,
  parseOptionalNonNegativeInt,
  parseOptionalPositiveInt,
  pickFeedbackRelationTriplet,
  summarizeFeedbackActionPayload,
  trimDeleteItemText,
} from './utils'

const ZH_DATE_OPTIONS: Intl.DateTimeFormatOptions = {
  hour12: false,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
}

/** 与实现相同的 zh-CN 本地化，避免测试依赖机器时区的固定字符串 */
function expectedZhDateTime(timestampMs: number): string {
  return new Date(timestampMs).toLocaleString('zh-CN', ZH_DATE_OPTIONS)
}

function makeDeleteItem(overrides: Partial<DeleteOperationItem> = {}): DeleteOperationItem {
  return {
    item_type: 'entity',
    item_hash: 'hash-1',
    ...overrides,
  }
}

function makeFeedbackSummary(
  overrides: Partial<MemoryFeedbackCorrectionSummaryPayload> = {}
): MemoryFeedbackCorrectionSummaryPayload {
  return {
    task_id: 1,
    query_tool_id: 'qt-1',
    session_id: 'session-1',
    query_text: '',
    task_status: 'applied',
    decision: 'correct',
    decision_confidence: 1,
    feedback_message_count: 0,
    rollback_status: 'none',
    affected_counts: {},
    ...overrides,
  }
}

function makeFeedbackDetail(
  overrides: Partial<MemoryFeedbackCorrectionDetailTaskPayload> = {}
): MemoryFeedbackCorrectionDetailTaskPayload {
  return {
    ...makeFeedbackSummary(),
    ...overrides,
  }
}

function makeActionLog(
  overrides: Partial<MemoryFeedbackActionLogPayload> = {}
): MemoryFeedbackActionLogPayload {
  return {
    id: 11,
    task_id: 1,
    query_tool_id: 'qt-1',
    action_type: 'skip',
    target_hash: 'target-1',
    ...overrides,
  }
}

const OLD_TRIPLET = { subject: 'Alice', predicate: '认识', object: 'Bob' }
const NEW_TRIPLET = { subject: 'Alice', predicate: '是', object: '同事' }

describe('normalizeProgress', () => {
  it('空值与非有限数字归零', () => {
    expect(normalizeProgress(null)).toBe(0)
    expect(normalizeProgress(undefined)).toBe(0)
    expect(normalizeProgress('')).toBe(0)
    expect(normalizeProgress('abc')).toBe(0)
    expect(normalizeProgress(Number.NaN)).toBe(0)
    expect(normalizeProgress(Number.POSITIVE_INFINITY)).toBe(0)
    expect(normalizeProgress(Number.NEGATIVE_INFINITY)).toBe(0)
  })

  it('0 到 1 的比例值乘以 100，1 视为 100%', () => {
    expect(normalizeProgress(0)).toBe(0)
    expect(normalizeProgress(0.5)).toBe(50)
    expect(normalizeProgress('0.25')).toBe(25)
    expect(normalizeProgress(1)).toBe(100)
    expect(normalizeProgress('1')).toBe(100)
  })

  it('已是百分数时不再换算，并夹到 [0, 100]', () => {
    expect(normalizeProgress(1.5)).toBe(1.5)
    expect(normalizeProgress(50)).toBe(50)
    expect(normalizeProgress('80')).toBe(80)
    expect(normalizeProgress(-10)).toBe(0)
    expect(normalizeProgress(150)).toBe(100)
    expect(normalizeProgress('200')).toBe(100)
  })
})

describe('formatProgressPercent', () => {
  it('把规范化后的进度格式化为一位小数加百分号', () => {
    expect(formatProgressPercent(null)).toBe('0.0%')
    expect(formatProgressPercent(0.5)).toBe('50.0%')
    expect(formatProgressPercent(33.333)).toBe('33.3%')
    expect(formatProgressPercent(100)).toBe('100.0%')
    expect(formatProgressPercent(150)).toBe('100.0%')
  })
})

describe('parseOptionalPositiveInt', () => {
  it('空白字符串返回 undefined', () => {
    expect(parseOptionalPositiveInt('')).toBeUndefined()
    expect(parseOptionalPositiveInt('   ')).toBeUndefined()
  })

  it('非正整数返回 undefined', () => {
    expect(parseOptionalPositiveInt('0')).toBeUndefined()
    expect(parseOptionalPositiveInt('-1')).toBeUndefined()
    expect(parseOptionalPositiveInt('1.5')).toBeUndefined()
    expect(parseOptionalPositiveInt('abc')).toBeUndefined()
  })

  it('合法正整数会去掉首尾空白后返回', () => {
    expect(parseOptionalPositiveInt('7')).toBe(7)
    expect(parseOptionalPositiveInt('  12  ')).toBe(12)
    // Number('1e2') 是整数 100，当前实现会接受科学计数法
    expect(parseOptionalPositiveInt('1e2')).toBe(100)
  })
})

describe('parseOptionalNonNegativeInt', () => {
  it('空白或非法输入返回 undefined', () => {
    expect(parseOptionalNonNegativeInt('')).toBeUndefined()
    expect(parseOptionalNonNegativeInt('   ')).toBeUndefined()
    expect(parseOptionalNonNegativeInt('-1')).toBeUndefined()
    expect(parseOptionalNonNegativeInt('2.2')).toBeUndefined()
    expect(parseOptionalNonNegativeInt('nope')).toBeUndefined()
  })

  it('0 与正整数都合法', () => {
    expect(parseOptionalNonNegativeInt('0')).toBe(0)
    expect(parseOptionalNonNegativeInt(' 3 ')).toBe(3)
  })
})

describe('parseCommaSeparatedList', () => {
  it('按逗号拆分、去掉空白并丢弃空段', () => {
    expect(parseCommaSeparatedList('')).toEqual([])
    expect(parseCommaSeparatedList('  ')).toEqual([])
    expect(parseCommaSeparatedList('only')).toEqual(['only'])
    expect(parseCommaSeparatedList('a, b, c')).toEqual(['a', 'b', 'c'])
    expect(parseCommaSeparatedList('a,, b , ,c,')).toEqual(['a', 'b', 'c'])
  })
})

describe('normalizeImportInputMode', () => {
  it('仅精确匹配 json，其余一律视为 text', () => {
    expect(normalizeImportInputMode('json')).toBe('json')
    expect(normalizeImportInputMode('text')).toBe('text')
    expect(normalizeImportInputMode('JSON')).toBe('text')
    expect(normalizeImportInputMode('')).toBe('text')
    expect(normalizeImportInputMode('xml')).toBe('text')
  })
})

describe('getImportStatusLabel', () => {
  it('空状态显示短横线，已知状态走对照表，未知状态原样返回', () => {
    expect(getImportStatusLabel('')).toBe('-')
    expect(getImportStatusLabel('   ')).toBe('-')
    expect(getImportStatusLabel('queued')).toBe('排队中')
    expect(getImportStatusLabel('  failed  ')).toBe('失败')
    expect(getImportStatusLabel('completed_with_errors')).toBe('完成（有错误）')
    expect(getImportStatusLabel('mystery')).toBe('mystery')
  })
})

describe('getImportStepLabel', () => {
  it('空步骤显示短横线，已知步骤走对照表，未知步骤原样返回', () => {
    expect(getImportStepLabel('')).toBe('-')
    expect(getImportStepLabel('   ')).toBe('-')
    expect(getImportStepLabel('splitting')).toBe('分块中')
    expect(getImportStepLabel('extracting')).toBe('抽取中')
    expect(getImportStepLabel('  writing  ')).toBe('写入中')
    expect(getImportStepLabel('custom_step')).toBe('custom_step')
  })
})

describe('getImportStatusVariant', () => {
  it.each([
    ['failed', 'destructive'],
    ['completed', 'default'],
    ['completed_with_errors', 'secondary'],
    ['cancelled', 'secondary'],
    ['preparing', 'outline'],
    ['running', 'outline'],
    ['cancel_requested', 'outline'],
    ['queued', 'outline'],
    ['unknown', 'secondary'],
    ['', 'secondary'],
  ] as const)('状态 %s 对应变体 %s', (status, variant) => {
    expect(getImportStatusVariant(status)).toBe(variant)
  })
})

describe('formatImportTime', () => {
  it('空值和 0 显示短横线', () => {
    expect(formatImportTime()).toBe('-')
    expect(formatImportTime(null)).toBe('-')
    expect(formatImportTime(0)).toBe('-')
    expect(formatImportTime(Number.NaN)).toBe('-')
  })

  it('秒级时间戳乘 1000，毫秒级直接使用，二者对同一时刻结果相同', () => {
    const seconds = 1_704_067_200
    const millis = seconds * 1000
    expect(formatImportTime(seconds)).toBe(expectedZhDateTime(millis))
    expect(formatImportTime(millis)).toBe(expectedZhDateTime(millis))
  })

  it('恰好 1e12 仍按秒处理，超过则按毫秒', () => {
    const threshold = 1_000_000_000_000
    expect(formatImportTime(threshold)).toBe(expectedZhDateTime(threshold * 1000))
    expect(formatImportTime(threshold + 1)).toBe(expectedZhDateTime(threshold + 1))
  })

  it('无法构成合法日期时回退短横线', () => {
    expect(formatImportTime(Number.POSITIVE_INFINITY)).toBe('-')
    expect(formatImportTime(Number.MAX_VALUE)).toBe('-')
  })
})

describe('formatDeleteOperationMode', () => {
  it.each([
    ['entity', '实体'],
    ['relation', '关系'],
    ['paragraph', '段落'],
    ['source', '来源'],
    ['mixed', '混合'],
    ['custom', 'custom'],
    ['', '未知'],
  ] as const)('模式 %s 显示为 %s', (mode, label) => {
    expect(formatDeleteOperationMode(mode)).toBe(label)
  })
})

describe('formatDeleteOperationStatus', () => {
  it.each([
    ['executed', '已执行'],
    ['restored', '已恢复'],
    ['pending', 'pending'],
    ['', '未知'],
  ] as const)('状态 %s 显示为 %s', (status, label) => {
    expect(formatDeleteOperationStatus(status)).toBe(label)
  })
})

describe('formatDeleteOperationTime', () => {
  it('空值显示未知时间', () => {
    expect(formatDeleteOperationTime()).toBe('未知时间')
    expect(formatDeleteOperationTime(null)).toBe('未知时间')
    expect(formatDeleteOperationTime(0)).toBe('未知时间')
  })

  it('秒与毫秒时间戳都能格式化', () => {
    const seconds = 1_704_067_200
    const millis = seconds * 1000
    expect(formatDeleteOperationTime(seconds)).toBe(expectedZhDateTime(millis))
    expect(formatDeleteOperationTime(millis)).toBe(expectedZhDateTime(millis))
  })

  it('非法时间戳回退未知时间', () => {
    expect(formatDeleteOperationTime(Number.POSITIVE_INFINITY)).toBe('未知时间')
  })
})

describe('trimDeleteItemText', () => {
  it('空白归一为空串，连续空白折叠成单空格', () => {
    expect(trimDeleteItemText('')).toBe('')
    expect(trimDeleteItemText('   ')).toBe('')
    expect(trimDeleteItemText('  hello   world  ')).toBe('hello world')
    expect(trimDeleteItemText('\nfoo\t\tbar\n')).toBe('foo bar')
  })

  it('不超过上限时原样返回，超出则截断并加省略号', () => {
    const exact = 'a'.repeat(140)
    const overflow = `${exact}b`
    expect(trimDeleteItemText(exact)).toBe(exact)
    expect(trimDeleteItemText(overflow)).toBe(`${exact}...`)
    expect(trimDeleteItemText('abcdef', 3)).toBe('abc...')
    expect(trimDeleteItemText('abc', 3)).toBe('abc')
  })
})

describe('formatDeleteRelationText', () => {
  it('用箭头拼接非空主谓宾', () => {
    expect(formatDeleteRelationText('Alice', '认识', 'Bob')).toBe('Alice -> 认识 -> Bob')
    expect(formatDeleteRelationText('Alice', '', 'Bob')).toBe('Alice -> Bob')
    expect(formatDeleteRelationText('', '认识', '')).toBe('认识')
    expect(formatDeleteRelationText('  ', '  ', '  ')).toBe('')
    expect(formatDeleteRelationText('', '', '')).toBe('')
  })
})

describe('getDeleteOperationItemLabel', () => {
  it('实体优先用 name，空 name 不会回退到 key', () => {
    expect(
      getDeleteOperationItemLabel(
        makeDeleteItem({
          item_type: 'entity',
          payload: { entity: { name: '麦麦' } },
        })
      )
    ).toBe('麦麦')
    expect(
      getDeleteOperationItemLabel(
        makeDeleteItem({
          item_type: 'entity',
          item_key: 'key-entity',
          payload: { entity: {} },
        })
      )
    ).toBe('key-entity')
    expect(
      getDeleteOperationItemLabel(
        makeDeleteItem({
          item_type: 'entity',
          item_hash: 'hash-entity',
          payload: {},
        })
      )
    ).toBe('hash-entity')
    // name 为空串时 ?? 不会落到 item_key
    expect(
      getDeleteOperationItemLabel(
        makeDeleteItem({
          item_type: 'entity',
          item_key: 'should-not-use',
          payload: { entity: { name: '' } },
        })
      )
    ).toBe('')
  })

  it('关系用主谓宾文本，拼不出时回退 key / hash / 未命名关系', () => {
    expect(
      getDeleteOperationItemLabel(
        makeDeleteItem({
          item_type: 'relation',
          payload: { relation: OLD_TRIPLET },
        })
      )
    ).toBe('Alice -> 认识 -> Bob')
    expect(
      getDeleteOperationItemLabel(
        makeDeleteItem({
          item_type: 'relation',
          item_key: 'rel-key',
          payload: { relation: { subject: '', predicate: '', object: '' } },
        })
      )
    ).toBe('rel-key')
    expect(
      getDeleteOperationItemLabel(
        makeDeleteItem({
          item_type: 'relation',
          item_hash: 'rel-hash',
        })
      )
    ).toBe('rel-hash')
    expect(
      getDeleteOperationItemLabel({
        item_type: 'relation',
        item_hash: undefined as unknown as string,
      })
    ).toBe('未命名关系')
  })

  it('段落优先用 source，空 source 才回退 key / hash / 未命名段落', () => {
    expect(
      getDeleteOperationItemLabel(
        makeDeleteItem({
          item_type: 'paragraph',
          item_key: 'p-key',
          payload: { paragraph: { source: '  聊天记录  ' } },
        })
      )
    ).toBe('聊天记录')
    expect(
      getDeleteOperationItemLabel(
        makeDeleteItem({
          item_type: 'paragraph',
          item_key: 'p-key',
          payload: { paragraph: { source: '   ' } },
        })
      )
    ).toBe('p-key')
    expect(
      getDeleteOperationItemLabel(
        makeDeleteItem({
          item_type: 'paragraph',
          item_hash: 'p-hash',
        })
      )
    ).toBe('p-hash')
    expect(
      getDeleteOperationItemLabel({
        item_type: 'paragraph',
        item_hash: undefined as unknown as string,
      })
    ).toBe('未命名段落')
  })

  it('其他类型回退 item_key / item_hash / 未命名对象', () => {
    expect(
      getDeleteOperationItemLabel(
        makeDeleteItem({
          item_type: 'source',
          item_key: 'src-key',
        })
      )
    ).toBe('src-key')
    expect(
      getDeleteOperationItemLabel(
        makeDeleteItem({
          item_type: 'mixed',
          item_hash: 'obj-hash',
        })
      )
    ).toBe('obj-hash')
    expect(
      getDeleteOperationItemLabel({
        item_type: 'unknown',
        item_hash: undefined as unknown as string,
      })
    ).toBe('未命名对象')
  })
})

describe('getDeleteOperationItemPreview', () => {
  it('实体有关联段落时显示数量，否则显示实体快照', () => {
    expect(
      getDeleteOperationItemPreview(
        makeDeleteItem({
          item_type: 'entity',
          payload: { paragraph_links: ['a', 'b', 'c'] },
        })
      )
    ).toBe('关联段落 3 个')
    expect(
      getDeleteOperationItemPreview(
        makeDeleteItem({
          item_type: 'entity',
          payload: { paragraph_links: [] },
        })
      )
    ).toBe('实体快照')
    expect(
      getDeleteOperationItemPreview(
        makeDeleteItem({
          item_type: 'entity',
          payload: { paragraph_links: { not: 'array' } },
        })
      )
    ).toBe('实体快照')
    expect(getDeleteOperationItemPreview(makeDeleteItem({ item_type: 'entity' }))).toBe('实体快照')
  })

  it('关系拼接证据段落数与数值置信度，都没有时显示关系快照', () => {
    expect(
      getDeleteOperationItemPreview(
        makeDeleteItem({
          item_type: 'relation',
          payload: {
            paragraph_hashes: ['p1', 'p2'],
            relation: { confidence: 0.856 },
          },
        })
      )
    ).toBe('证据段落 2 个，置信度 0.86')
    expect(
      getDeleteOperationItemPreview(
        makeDeleteItem({
          item_type: 'relation',
          payload: { relation: { confidence: 0 } },
        })
      )
    ).toBe('置信度 0.00')
    expect(
      getDeleteOperationItemPreview(
        makeDeleteItem({
          item_type: 'relation',
          payload: { relation: { confidence: '0.9' } },
        })
      )
    ).toBe('关系快照')
    expect(getDeleteOperationItemPreview(makeDeleteItem({ item_type: 'relation' }))).toBe('关系快照')
  })

  it('段落预览折叠正文；其他类型返回空串', () => {
    expect(
      getDeleteOperationItemPreview(
        makeDeleteItem({
          item_type: 'paragraph',
          payload: { paragraph: { content: '  第一段   第二段  ' } },
        })
      )
    ).toBe('第一段 第二段')
    expect(
      getDeleteOperationItemPreview(
        makeDeleteItem({
          item_type: 'paragraph',
          payload: { paragraph: {} },
        })
      )
    ).toBe('')
    expect(getDeleteOperationItemPreview(makeDeleteItem({ item_type: 'source' }))).toBe('')
  })
})

describe('getDeleteOperationItemSource', () => {
  it('段落读取 paragraph.source，其他类型读取 payload.source', () => {
    expect(
      getDeleteOperationItemSource(
        makeDeleteItem({
          item_type: 'paragraph',
          payload: { paragraph: { source: '  群聊  ' }, source: 'payload-source' },
        })
      )
    ).toBe('群聊')
    expect(
      getDeleteOperationItemSource(
        makeDeleteItem({
          item_type: 'paragraph',
          payload: { paragraph: {} },
        })
      )
    ).toBe('')
    expect(
      getDeleteOperationItemSource(
        makeDeleteItem({
          item_type: 'entity',
          payload: { source: '  外部导入  ' },
        })
      )
    ).toBe('外部导入')
    expect(getDeleteOperationItemSource(makeDeleteItem({ item_type: 'relation' }))).toBe('')
  })
})

describe('formatFeedbackDecision', () => {
  it.each([
    ['correct', '纠正'],
    ['reject', '否定'],
    ['confirm', '确认'],
    ['supplement', '补充'],
    ['none', '无动作'],
    ['custom', 'custom'],
    ['', '未知'],
  ] as const)('判定 %s 显示为 %s', (decision, label) => {
    expect(formatFeedbackDecision(decision)).toBe(label)
  })
})

describe('formatFeedbackTaskStatus', () => {
  it.each([
    ['pending', '待处理'],
    ['running', '处理中'],
    ['applied', '已应用'],
    ['skipped', '已跳过'],
    ['error', '失败'],
    ['custom', 'custom'],
    ['', '未知'],
  ] as const)('任务状态 %s 显示为 %s', (status, label) => {
    expect(formatFeedbackTaskStatus(status)).toBe(label)
  })
})

describe('formatFeedbackRollbackStatus', () => {
  it.each([
    ['none', '未回退'],
    ['running', '回退中'],
    ['rolled_back', '已回退'],
    ['error', '回退失败'],
    ['custom', 'custom'],
    ['', '未知'],
  ] as const)('回退状态 %s 显示为 %s', (status, label) => {
    expect(formatFeedbackRollbackStatus(status)).toBe(label)
  })
})

describe('getFeedbackStatusVariant', () => {
  it.each([
    ['applied', 'default'],
    ['rolled_back', 'default'],
    ['error', 'destructive'],
    ['running', 'outline'],
    ['pending', 'outline'],
    ['skipped', 'secondary'],
    ['none', 'secondary'],
    ['', 'secondary'],
  ] as const)('状态 %s 对应变体 %s', (status, variant) => {
    expect(getFeedbackStatusVariant(status)).toBe(variant)
  })
})

describe('summarizeFeedbackActionPayload', () => {
  it('缺省载荷返回空串', () => {
    expect(summarizeFeedbackActionPayload(undefined)).toBe('')
  })

  it('完整三元组优先于 hash 和 target_hashes', () => {
    expect(
      summarizeFeedbackActionPayload({
        ...OLD_TRIPLET,
        hash: 'should-not-use',
        target_hashes: ['t1'],
      })
    ).toBe('Alice -> 认识 -> Bob')
  })

  it('没有三元组时依次回退 hash、target 数量、JSON 摘要', () => {
    expect(summarizeFeedbackActionPayload({ hash: '  abc123  ' })).toBe('abc123')
    expect(summarizeFeedbackActionPayload({ target_hashes: ['a', 'b'] })).toBe('targets 2')
    // 空数组不满足 length > 0，整份载荷走 JSON 摘要
    expect(summarizeFeedbackActionPayload({ target_hashes: [] })).toBe('{ "target_hashes": [] }')
    expect(summarizeFeedbackActionPayload({})).toBe('{}')
    expect(summarizeFeedbackActionPayload({ note: 'x' })).toBe('{ "note": "x" }')

    const long = summarizeFeedbackActionPayload({ note: 'x'.repeat(200) })
    expect(long.endsWith('...')).toBe(true)
    expect(long.length).toBe(123)
  })
})

describe('pickFeedbackRelationTriplet / formatFeedbackRelationTriplet', () => {
  it('非对象或缺任一主谓宾时返回 null / 空串', () => {
    expect(pickFeedbackRelationTriplet(null)).toBeNull()
    expect(pickFeedbackRelationTriplet(undefined)).toBeNull()
    expect(pickFeedbackRelationTriplet('Alice')).toBeNull()
    expect(pickFeedbackRelationTriplet(12)).toBeNull()
    expect(pickFeedbackRelationTriplet({ subject: 'A', predicate: 'p' })).toBeNull()
    expect(
      pickFeedbackRelationTriplet({ subject: 'A', predicate: '  ', object: 'B' })
    ).toBeNull()
    expect(formatFeedbackRelationTriplet(null)).toBe('')
    expect(formatFeedbackRelationTriplet({ subject: 'A' })).toBe('')
  })

  it('完整三元组返回原对象，并格式化为箭头文本', () => {
    const record = { ...OLD_TRIPLET, extra: true }
    expect(pickFeedbackRelationTriplet(record)).toBe(record)
    expect(formatFeedbackRelationTriplet(record)).toBe('Alice -> 认识 -> Bob')
  })
})

describe('getFeedbackCorrectionPreview', () => {
  it('task 为 null 时给出没有摘要的标题', () => {
    expect(getFeedbackCorrectionPreview(null)).toEqual({
      headline: '当前没有纠错摘要',
      oldRelation: '',
      newRelation: '',
    })
  })

  it('旧关系与新关系都可读时生成“纠正为”标题', () => {
    expect(
      getFeedbackCorrectionPreview(
        makeFeedbackDetail({
          rollback_plan_summary: {
            forgotten_relations: [OLD_TRIPLET],
            corrected_write: { corrected_relations: [NEW_TRIPLET] },
          },
        })
      )
    ).toEqual({
      headline: '将“Alice -> 认识 -> Bob”纠正为“Alice -> 是 -> 同事”',
      oldRelation: 'Alice -> 认识 -> Bob',
      newRelation: 'Alice -> 是 -> 同事',
    })
  })

  it('只有新关系时走补充结论标题', () => {
    expect(
      getFeedbackCorrectionPreview(
        makeFeedbackDetail({
          rollback_plan_summary: {
            forgotten_relations: [{ subject: '残缺' }],
            corrected_write: { corrected_relations: [NEW_TRIPLET] },
          },
        })
      )
    ).toEqual({
      headline: '补充了新的纠错结论：“Alice -> 是 -> 同事”',
      oldRelation: '',
      newRelation: 'Alice -> 是 -> 同事',
    })
  })

  it('只有旧关系时走撤销旧记忆标题', () => {
    expect(
      getFeedbackCorrectionPreview(
        makeFeedbackDetail({
          rollback_plan_summary: {
            forgotten_relations: [OLD_TRIPLET],
            corrected_write: { corrected_relations: [] },
          },
        })
      )
    ).toEqual({
      headline: '撤销了旧记忆关系：“Alice -> 认识 -> Bob”',
      oldRelation: 'Alice -> 认识 -> Bob',
      newRelation: '',
    })
  })

  it('两边都不可读时回退 query_text，再回退固定文案', () => {
    expect(
      getFeedbackCorrectionPreview(
        makeFeedbackSummary({ query_text: '用户说记错了' })
      )
    ).toEqual({
      headline: '用户说记错了',
      oldRelation: '',
      newRelation: '',
    })

    expect(
      getFeedbackCorrectionPreview(
        makeFeedbackDetail({
          query_text: '',
          rollback_plan_summary: {
            forgotten_relations: 'not-array',
            corrected_write: 'not-object',
          },
        })
      )
    ).toEqual({
      headline: '当前纠错没有可读摘要',
      oldRelation: '',
      newRelation: '',
    })
  })
})

describe('buildFeedbackImpactSummary', () => {
  it('task 为空或计数都为 0 时返回空数组', () => {
    expect(buildFeedbackImpactSummary(null)).toEqual([])
    expect(buildFeedbackImpactSummary(makeFeedbackSummary())).toEqual([])
    expect(
      buildFeedbackImpactSummary(
        makeFeedbackSummary({
          affected_counts: {
            relations: 0,
            corrected_relations: 0,
            correction_paragraphs: 0,
            stale_paragraphs: 0,
            episode_sources: 0,
            profile_person_ids: 0,
          },
        })
      )
    ).toEqual([])
  })

  it('按字段拼出大于 0 的影响条目', () => {
    expect(
      buildFeedbackImpactSummary(
        makeFeedbackSummary({
          affected_counts: {
            relations: 2,
            corrected_relations: 1,
            correction_paragraphs: 3,
            stale_paragraphs: 4,
            episode_sources: 5,
            profile_person_ids: 6,
          },
        })
      )
    ).toEqual([
      '影响关系 2 条',
      '新增纠正关系 1 条',
      '写入纠错段落 3 条',
      '标记旧段落 4 条',
      '触发 Episode 修复 5 个来源',
      '触发 Profile 刷新 6 个对象',
    ])
  })
})

describe('formatFeedbackActionType', () => {
  it.each([
    ['classification', '判定纠错'],
    ['forget_relation', '撤销旧关系'],
    ['mark_stale_paragraph', '标记旧段落'],
    ['write_correction', '写入纠错'],
    ['rollback_restore_relation', '恢复旧关系'],
    ['rollback_delete_correction_paragraph', '隐藏纠错段落'],
    ['rollback_revert_corrected_relation', '撤销纠正关系'],
    ['rollback_clear_stale_mark', '清除脏段落标记'],
    ['rollback_enqueue_episode_rebuild', '加入 Episode 修复队列'],
    ['rollback_enqueue_profile_refresh', '加入 Profile 刷新队列'],
    ['rollback_error', '回退失败'],
    ['error', '处理失败'],
    ['skip', '跳过处理'],
    ['custom_action', 'custom_action'],
    ['', '未知动作'],
  ] as const)('动作类型 %s 显示为 %s', (actionType, label) => {
    expect(formatFeedbackActionType(actionType)).toBe(label)
  })
})

describe('describeFeedbackActionLog', () => {
  it('classification 在有/无 after 摘要时使用不同句式', () => {
    expect(
      describeFeedbackActionLog(
        makeActionLog({
          action_type: 'classification',
          after_payload: { hash: 'cls-1' },
        })
      )
    ).toBe('系统完成判定：cls-1')
    expect(describeFeedbackActionLog(makeActionLog({ action_type: 'classification' }))).toBe(
      '系统完成纠错判定'
    )
  })

  it('forget_relation 优先引用 before 摘要', () => {
    expect(
      describeFeedbackActionLog(
        makeActionLog({
          action_type: 'forget_relation',
          before_payload: OLD_TRIPLET,
        })
      )
    ).toBe('旧关系已失效：Alice -> 认识 -> Bob')
    expect(describeFeedbackActionLog(makeActionLog({ action_type: 'forget_relation' }))).toBe(
      '旧关系已被标记为失效'
    )
  })

  it('write_correction 与 rollback_restore_relation 在有 after 时带上摘要', () => {
    expect(
      describeFeedbackActionLog(
        makeActionLog({
          action_type: 'write_correction',
          after_payload: NEW_TRIPLET,
        })
      )
    ).toBe('已写入新的纠错结果：Alice -> 是 -> 同事')
    expect(describeFeedbackActionLog(makeActionLog({ action_type: 'write_correction' }))).toBe(
      '已写入新的纠错段落和关系'
    )
    expect(
      describeFeedbackActionLog(
        makeActionLog({
          action_type: 'rollback_restore_relation',
          after_payload: { hash: 'rel-9' },
        })
      )
    ).toBe('已恢复旧关系状态：rel-9')
    expect(
      describeFeedbackActionLog(makeActionLog({ action_type: 'rollback_restore_relation' }))
    ).toBe('已恢复旧关系状态')
  })

  it('固定文案的动作类型不依赖 payload', () => {
    expect(describeFeedbackActionLog(makeActionLog({ action_type: 'mark_stale_paragraph' }))).toBe(
      '旧段落已标记为待复核，后续检索会更谨慎地使用它'
    )
    expect(
      describeFeedbackActionLog(makeActionLog({ action_type: 'rollback_delete_correction_paragraph' }))
    ).toBe('已隐藏这次纠错写入的段落')
    expect(
      describeFeedbackActionLog(makeActionLog({ action_type: 'rollback_revert_corrected_relation' }))
    ).toBe('已撤销纠错阶段新增的关系')
    expect(describeFeedbackActionLog(makeActionLog({ action_type: 'rollback_clear_stale_mark' }))).toBe(
      '已清除旧段落的待复核标记'
    )
    expect(
      describeFeedbackActionLog(makeActionLog({ action_type: 'rollback_enqueue_episode_rebuild' }))
    ).toBe('已重新加入 Episode 修复队列')
    expect(
      describeFeedbackActionLog(makeActionLog({ action_type: 'rollback_enqueue_profile_refresh' }))
    ).toBe('已重新加入 Profile 刷新队列')
  })

  it('error / skip / rollback_error 优先使用 reason', () => {
    expect(
      describeFeedbackActionLog(makeActionLog({ action_type: 'rollback_error', reason: '回退超时' }))
    ).toBe('回退超时')
    expect(describeFeedbackActionLog(makeActionLog({ action_type: 'rollback_error' }))).toBe(
      '这次回退执行失败'
    )
    expect(
      describeFeedbackActionLog(makeActionLog({ action_type: 'error', reason: '模型失败' }))
    ).toBe('模型失败')
    expect(describeFeedbackActionLog(makeActionLog({ action_type: 'error' }))).toBe(
      '这次纠错处理失败'
    )
    expect(
      describeFeedbackActionLog(makeActionLog({ action_type: 'skip', reason: '置信度不足' }))
    ).toBe('置信度不足')
    expect(describeFeedbackActionLog(makeActionLog({ action_type: 'skip' }))).toBe('这次纠错被跳过')
  })

  it('未知动作按 after → before → reason → 默认文案回退', () => {
    expect(
      describeFeedbackActionLog(
        makeActionLog({
          action_type: 'mystery',
          after_payload: { hash: 'after-h' },
          before_payload: { hash: 'before-h' },
          reason: 'r',
        })
      )
    ).toBe('after-h')
    expect(
      describeFeedbackActionLog(
        makeActionLog({
          action_type: 'mystery',
          before_payload: { hash: 'before-h' },
          reason: 'r',
        })
      )
    ).toBe('before-h')
    expect(
      describeFeedbackActionLog(makeActionLog({ action_type: 'mystery', reason: '只剩原因' }))
    ).toBe('只剩原因')
    expect(describeFeedbackActionLog(makeActionLog({ action_type: 'mystery' }))).toBe(
      '记录了一条动作日志'
    )
  })
})
