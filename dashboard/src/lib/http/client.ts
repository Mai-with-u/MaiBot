/**
 * 请求客户端（ApiClient）深模块。
 *
 * 收编了此前散落在各 *-api.ts 中的全部请求样板：
 * - base URL 解析（Electron 动态后端 / 浏览器同源）
 * - Cookie 认证与 401 处理（通过 onUnauthorized 注入，便于测试与多实例差异化）
 * - JSON / FormData 请求体编码
 * - 响应解析与错误格式化（formatApiError）
 * - 路由未命中诊断：响应体是前端 HTML 页面时报出明确错误，而不是静默重试
 *
 * 所有失败统一抛出 ApiError（见 errors.ts），调用方不再手写 response.ok 分支。
 */
import { formatApiError } from '@/lib/api-error'

import { ApiError } from './errors'

export type HttpMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'

/** query 参数值：undefined / null 会被跳过，布尔值序列化为 'true' / 'false' */
export type QueryValue = string | number | boolean | null | undefined

export interface RequestOptions {
  /** 拼接到 URL 的 query 参数；数组值会展开为多个同名参数 */
  query?: Record<string, QueryValue | QueryValue[]>
  /** 请求体：FormData 原样发送（不设 Content-Type），其余值 JSON 序列化 */
  body?: unknown
  /** 额外请求头，会覆盖默认头 */
  headers?: HeadersInit
  signal?: AbortSignal
  /**
   * 响应解析方式，默认 'json'。
   * 'response' 返回原始 Response（仅在 HTTP 成功时；失败仍抛 ApiError）。
   */
  parse?: 'json' | 'text' | 'blob' | 'response'
  /** 透传 fetch 的缓存模式（如配置读取需要 'no-store' 跳过 HTTP 缓存） */
  cache?: RequestCache
  /** 该请求的业务上下文错误文案，后端未给出可用错误信息时作为 ApiError.message */
  errorMessage?: string
}

export interface ApiClientOptions {
  /** 解析请求的 base URL；浏览器同源部署返回空字符串即可 */
  resolveBaseUrl: () => string | Promise<string>
  /** 认证方式：'cookie' 携带 HttpOnly Cookie 并处理 401；'none' 不携带凭据 */
  auth?: 'cookie' | 'none'
  /** 401 未授权时的回调（如跳转登录页）；仅 auth: 'cookie' 时生效 */
  onUnauthorized?: () => void
}

export interface ApiClient {
  request<T>(method: HttpMethod, path: string, options?: RequestOptions): Promise<T>
  get<T>(path: string, options?: RequestOptions): Promise<T>
  post<T>(path: string, options?: RequestOptions): Promise<T>
  put<T>(path: string, options?: RequestOptions): Promise<T>
  patch<T>(path: string, options?: RequestOptions): Promise<T>
  delete<T>(path: string, options?: RequestOptions): Promise<T>
}

/** 把 query 对象序列化为 URLSearchParams，跳过 undefined / null */
function buildSearchParams(query: Record<string, QueryValue | QueryValue[]>): URLSearchParams {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    const values = Array.isArray(value) ? value : [value]
    for (const item of values) {
      if (item === undefined || item === null) {
        continue
      }
      params.append(key, typeof item === 'boolean' ? String(item) : String(item))
    }
  }
  return params
}

/** 判断响应体是否为前端 HTML 页面（路由未命中后端 API 的典型症状） */
function isHtmlResponse(rawText: string): boolean {
  const normalizedText = rawText.trimStart().toLowerCase()
  return normalizedText.startsWith('<!doctype') || normalizedText.startsWith('<html')
}

/** 将（可能是相对路径的）请求 URL 转为绝对地址，便于诊断信息阅读 */
function formatRequestUrl(url: string): string {
  if (typeof window === 'undefined') {
    return url
  }
  try {
    return new URL(url, window.location.href).toString()
  } catch {
    return url
  }
}

/** 路由未命中诊断文案 */
function htmlRouteDiagnostic(url: string): string {
  return `接口返回了前端页面，未命中后端 API 路由；当前请求：${formatRequestUrl(url)}`
}

export function createApiClient(clientOptions: ApiClientOptions): ApiClient {
  const { resolveBaseUrl, auth = 'cookie', onUnauthorized } = clientOptions

  async function request<T>(
    method: HttpMethod,
    path: string,
    options: RequestOptions = {}
  ): Promise<T> {
    const { query, body, headers, signal, parse = 'json', errorMessage, cache } = options

    // 拼接完整 URL：base + path + query
    const base = await resolveBaseUrl()
    let url = `${base}${path}`
    if (query) {
      const params = buildSearchParams(query)
      const queryString = params.toString()
      if (queryString) {
        url += (url.includes('?') ? '&' : '?') + queryString
      }
    }

    // 构建请求体与请求头：FormData 交给浏览器设置 multipart 边界，其余 JSON 序列化
    const isFormData = body instanceof FormData
    const requestHeaders: HeadersInit = isFormData
      ? { ...headers }
      : { 'Content-Type': 'application/json', ...headers }

    let response: Response
    try {
      response = await fetch(url, {
        method,
        headers: requestHeaders,
        body: isFormData ? body : body === undefined ? undefined : JSON.stringify(body),
        credentials: auth === 'cookie' ? 'include' : undefined,
        signal,
        cache,
      })
    } catch (error) {
      // 请求未到达服务器（断网、DNS、CORS 等），统一包装但不掩盖原因
      const reason = error instanceof Error ? error.message : String(error)
      throw new ApiError(`网络请求失败：${reason}`, { cause: error })
    }

    // 401 拦截与 onUnauthorized 回调绑定：配置了回调的实例（如主后端）跳转登录页并抛固定文案；
    // 未配置回调的实例（如登录流程的 authApi）让 401 走普通错误路径，透传后端的真实错误信息
    if (response.status === 401 && auth === 'cookie' && onUnauthorized) {
      onUnauthorized()
      throw new ApiError('认证失败，请重新登录', { status: 401 })
    }

    // HTTP 成功且要求原始形态时直接返回
    if (response.ok && parse === 'response') {
      return response as T
    }
    if (response.ok && parse === 'blob') {
      return (await response.blob()) as T
    }

    const rawText = await response.text()
    const htmlBody = isHtmlResponse(rawText)

    if (!response.ok) {
      const fallback = errorMessage ?? `请求失败（HTTP ${response.status}）`
      let detail: unknown = rawText
      let message: string
      try {
        detail = JSON.parse(rawText)
        message = formatApiError(detail, fallback)
      } catch {
        message = htmlBody ? htmlRouteDiagnostic(url) : response.statusText || fallback
      }
      throw new ApiError(message, { status: response.status, detail })
    }

    if (parse === 'text') {
      return rawText as T
    }

    // parse === 'json'：HTML / 空响应 / 非法 JSON 都是异常，必须显式暴露
    if (htmlBody) {
      throw new ApiError(htmlRouteDiagnostic(url), { status: response.status, detail: rawText })
    }
    if (!rawText) {
      throw new ApiError('接口返回了空响应', { status: response.status })
    }
    try {
      return JSON.parse(rawText) as T
    } catch {
      throw new ApiError('接口响应不是合法 JSON', { status: response.status, detail: rawText })
    }
  }

  return {
    request,
    get: (path, options) => request('GET', path, options),
    post: (path, options) => request('POST', path, options),
    put: (path, options) => request('PUT', path, options),
    patch: (path, options) => request('PATCH', path, options),
    delete: (path, options) => request('DELETE', path, options),
  }
}
