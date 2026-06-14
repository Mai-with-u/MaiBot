/**
 * 主后端业务级响应包络。
 *
 * 多数主后端 endpoint 在 HTTP 200 的响应体里再带一层 success 标记
 * （{ success: boolean, message?: string, data?: ... }），
 * success 为 false 表示业务级失败，需要与 HTTP 层失败同样以 ApiError 暴露。
 */
import { ApiError } from './errors'

export interface SuccessEnvelope {
  success: boolean
  message?: string
}

/** 校验响应体中的业务级 success 标记，失败时抛出 ApiError（message 优先取后端给出的 message） */
export function requireSuccess<T extends SuccessEnvelope>(data: T, fallback: string): T {
  if (!data.success) {
    throw new ApiError(data.message || fallback, { detail: data })
  }
  return data
}
