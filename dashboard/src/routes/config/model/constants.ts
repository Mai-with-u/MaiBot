/**
 * Model 配置页面常量
 */

import type { ModelListItem } from '@/lib/config-api'

/**
 * 模型列表缓存 TTL (5 分钟)
 */
export const CACHE_TTL = 5 * 60 * 1000

/**
 * 模型列表缓存
 */
export const modelListCache = new Map<string, { models: ModelListItem[]; timestamp: number }>()

/**
 * 默认模型信息
 */
export const DEFAULT_MODEL_INFO = {
  model_type: 'llm',
  model_identifier: '',
  name: '',
  api_provider: '',
  price_in: 0,
  price_out: 0,
  cache_price_in: 0,
  price_periods: [],
  temperature: null,
  max_tokens: null,
  visual: false,
  force_stream_mode: false,
  extra_params: {},
} as const

/**
 * 分页大小选项
 */
export const PAGE_SIZE_OPTIONS = [10, 20, 50, 100] as const
