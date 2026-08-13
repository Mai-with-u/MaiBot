import { describe, expect, it } from 'vitest'

import { formatApiError } from '../api-error'

describe('formatApiError', () => {
  it('returns string detail directly', () => {
    expect(formatApiError({ detail: '请求失败' }, '默认错误')).toBe('请求失败')
  })

  it('formats FastAPI validation detail arrays as text', () => {
    const error = formatApiError(
      {
        detail: [
          {
            type: 'int_parsing',
            loc: ['query', 'exclude_ids', 0],
            msg: 'Input should be a valid integer',
            input: 'abc',
          },
        ],
      },
      '获取审核列表失败'
    )

    expect(error).toBe('query.exclude_ids.0: Input should be a valid integer')
  })

  it('formats object details without returning an object', () => {
    const error = formatApiError(
      {
        detail: {
          loc: ['body', 'items'],
          msg: 'Field required',
        },
      },
      '批量审核失败'
    )

    expect(error).toBe('body.items: Field required')
  })

  it('falls back when response has no usable message', () => {
    expect(formatApiError({}, '默认错误')).toBe('默认错误')
  })

  it('uses message when detail is empty', () => {
    expect(formatApiError({ detail: '', message: '权限不足' }, '默认错误')).toBe('权限不足')
  })

  it('falsy 输入直接返回 fallback', () => {
    expect(formatApiError(null, '默认错误')).toBe('默认错误')
    expect(formatApiError(undefined, '默认错误')).toBe('默认错误')
    expect(formatApiError('', '默认错误')).toBe('默认错误')
    expect(formatApiError(0, '默认错误')).toBe('默认错误')
    expect(formatApiError(false, '默认错误')).toBe('默认错误')
  })

  it('字符串错误原样返回', () => {
    expect(formatApiError('网关超时', '默认错误')).toBe('网关超时')
  })

  it('非对象原始值转成字符串', () => {
    expect(formatApiError(true, '默认错误')).toBe('true')
    expect(formatApiError(42, '默认错误')).toBe('42')
  })

  it('detail 数组里的字符串项直接拼接', () => {
    expect(formatApiError({ detail: ['字段必填', '类型错误'] }, '默认错误')).toBe(
      '字段必填; 类型错误'
    )
  })

  it('对象详情优先使用 message，loc 为空时不拼路径前缀', () => {
    expect(
      formatApiError(
        {
          detail: {
            loc: '',
            message: 'Field required',
          },
        },
        '默认错误'
      )
    ).toBe('Field required')
  })

  it('非数组 loc 转成字符串路径；null/undefined 视为无路径', () => {
    expect(
      formatApiError({ detail: { loc: 'body.items', msg: 'too short' } }, '默认错误')
    ).toBe('body.items: too short')
    expect(formatApiError({ detail: { loc: 0, msg: 'invalid' } }, '默认错误')).toBe('0: invalid')
    expect(formatApiError({ detail: { loc: null, msg: 'missing' } }, '默认错误')).toBe('missing')
    expect(formatApiError({ detail: { loc: undefined, msg: 'missing' } }, '默认错误')).toBe(
      'missing'
    )
  })

  it('对象详情没有 msg/message 时退回 JSON 序列化', () => {
    expect(formatApiError({ detail: { type: 'value_error' } }, '默认错误')).toBe(
      '{"type":"value_error"}'
    )
  })

  it('无法 JSON 序列化的详情对象退回 String()', () => {
    // 循环引用会让 JSON.stringify 抛错，走 formatDetailItem 的 catch
    const circular: { loc: string; self?: unknown } = { loc: 'body' }
    circular.self = circular

    expect(formatApiError({ detail: circular }, '默认错误')).toBe('[object Object]')
  })

  it('detail 数组格式化后全为空时回落 fallback', () => {
    expect(formatApiError({ detail: [''] }, '默认错误')).toBe('默认错误')
  })

  it('可展示字段按 detail / message / error 顺序取值', () => {
    expect(formatApiError({ error: '底层失败' }, '默认错误')).toBe('底层失败')
  })
})
