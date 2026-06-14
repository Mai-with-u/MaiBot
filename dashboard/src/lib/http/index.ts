/**
 * 请求客户端（ApiClient）统一出口。
 *
 * 业务 API 模块应从这里导入 backendApi / statsApi 与 ApiError，
 * 不要再直接使用 fetch / fetchWithAuth 手写请求样板。
 */
export { createApiClient } from './client'
export { ApiError } from './errors'
export { authApi, backendApi, statsApi, STATS_SERVICE_BASE_URL } from './instances'
export { requireSuccess } from './envelope'
export type { ApiClient, ApiClientOptions, HttpMethod, QueryValue, RequestOptions } from './client'
export type { SuccessEnvelope } from './envelope'
