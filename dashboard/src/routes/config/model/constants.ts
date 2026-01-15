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
export const modelListCache = new Map<string, { models: ModelListItem[], timestamp: number }>()

/**
 * 任务配置信息
 */
export const TASK_CONFIGS = [
  {
    key: 'utils' as const,
    title: '组件模型 (utils)',
    description: '用于表情包、取名、关系、情绪变化等组件',
  },
  {
    key: 'utils_small' as const,
    title: '组件小模型 (utils_small)',
    description: '消耗量较大的组件，建议使用速度较快的小模型',
  },
  {
    key: 'tool_use' as const,
    title: '工具调用模型 (tool_use)',
    description: '需要使用支持工具调用的模型',
  },
  {
    key: 'replyer' as const,
    title: '首要回复模型 (replyer)',
    description: '用于表达器和表达方式学习',
  },
  {
    key: 'planner' as const,
    title: '决策模型 (planner)',
    description: '负责决定麦麦该什么时候回复',
  },
  {
    key: 'vlm' as const,
    title: '图像识别模型 (vlm)',
    description: '视觉语言模型',
    hideTemperature: true,
  },
  {
    key: 'voice' as const,
    title: '语音识别模型 (voice)',
    description: '语音转文字',
    hideTemperature: true,
    hideMaxTokens: true,
  },
  {
    key: 'embedding' as const,
    title: '嵌入模型 (embedding)',
    description: '用于向量化',
    hideTemperature: true,
    hideMaxTokens: true,
  },
] as const

/**
 * LPMM 任务配置信息
 */
export const LPMM_TASK_CONFIGS = [
  {
    key: 'lpmm_entity_extract' as const,
    title: '实体提取模型 (lpmm_entity_extract)',
    description: '从文本中提取实体',
  },
  {
    key: 'lpmm_rdf_build' as const,
    title: 'RDF 构建模型 (lpmm_rdf_build)',
    description: '构建知识图谱',
  },
  {
    key: 'lpmm_qa' as const,
    title: '问答模型 (lpmm_qa)',
    description: '知识库问答',
  },
] as const

/**
 * 默认模型信息
 */
export const DEFAULT_MODEL_INFO = {
  model_identifier: '',
  name: '',
  api_provider: '',
  price_in: 0,
  price_out: 0,
  temperature: null,
  max_tokens: null,
  force_stream_mode: false,
  extra_params: {},
} as const

/**
 * 分页大小选项
 */
export const PAGE_SIZE_OPTIONS = [10, 20, 50, 100] as const
