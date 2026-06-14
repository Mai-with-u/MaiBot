/**
 * 请求客户端统一的错误类型。
 *
 * 请求层所有失败（HTTP 错误、解析失败、网络异常、认证失效）都以 ApiError 抛出：
 * - message 已经过 formatApiError 格式化，可直接用于 toast / 页面渲染；
 * - status 是 HTTP 状态码，请求未到达服务器（网络层失败）时为 undefined；
 * - detail 保留后端返回的原始错误体，便于调试与精细化处理。
 */
export class ApiError extends Error {
  /** HTTP 状态码；网络层失败（请求未到达服务器）时为 undefined */
  readonly status?: number
  /** 后端返回的原始错误体（JSON 解析结果或原始文本） */
  readonly detail?: unknown

  constructor(
    message: string,
    options: { status?: number; detail?: unknown; cause?: unknown } = {}
  ) {
    super(message, options.cause === undefined ? undefined : { cause: options.cause })
    this.name = 'ApiError'
    this.status = options.status
    this.detail = options.detail
  }
}
