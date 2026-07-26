import { describe, expect, it } from 'vitest'

import { requireSuccess } from '../envelope'
import { ApiError } from '../errors'

describe('requireSuccess', () => {
  it('success 为 true 时原样返回响应体（同一引用）', () => {
    const data = { success: true, message: '完成', payload: [1, 2, 3] }

    expect(requireSuccess(data, '兜底文案')).toBe(data)
  })

  it('success 为 false 时抛出 ApiError：message 优先取后端 message，detail 保留原始响应体', () => {
    const data = { success: false, message: '业务校验失败' }
    let caught: unknown
    try {
      requireSuccess(data, '兜底文案')
    } catch (error) {
      caught = error
    }

    expect(caught).toBeInstanceOf(ApiError)
    const apiError = caught as ApiError
    expect(apiError.message).toBe('业务校验失败')
    expect(apiError.detail).toBe(data)
    // 业务级失败没有 HTTP 状态码
    expect(apiError.status).toBeUndefined()
  })

  it('后端未给 message 时使用 fallback 文案', () => {
    expect(() => requireSuccess({ success: false }, '获取数据失败')).toThrow('获取数据失败')
  })

  it('后端 message 为空字符串时同样退回 fallback 文案', () => {
    expect(() => requireSuccess({ success: false, message: '' }, '操作失败')).toThrow('操作失败')
  })
})
