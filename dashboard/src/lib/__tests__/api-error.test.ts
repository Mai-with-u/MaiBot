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
})
