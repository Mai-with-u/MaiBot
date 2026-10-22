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
 * 任务配置信息
 */
export const TASK_CONFIGS = [
  {
    key: 'replyer' as const,
    title: '回复模型 (replyer)',
    description: '用于表达器和表达方式学习',
  },
  {
    key: 'planner' as const,
    title: '规划模型 (planner)',
    description: '负责决定麦麦该什么时候回复',
  },
  {
    key: 'memory' as const,
    title: '长期记忆 (memory)',
    description: '用于长期记忆总结、抽取和写回',
  },
  {
    key: 'mid_memory' as const,
    title: '聊天回想 (mid_memory)',
    description: '用于回想聊天内容',
  },
  {
    key: 'learner' as const,
    title: '表达学习 (learner)',
    description: '用于表达方式和黑话学习',
  },
  {
    key: 'fast_model' as const,
    title: '快速模型 (fast_model)',
    description: '用于表达方式选择、回复断句等快速任务',
  },
  {
    key: 'emoji' as const,
    title: '表情包选择 (emoji)',
    description: '用于表情包发送',
  },
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
    key: 'vlm' as const,
    title: '图像识别模型 (vlm)',
    description: '视觉语言模型',
  },
  {
    key: 'voice' as const,
    title: '语音识别模型 (voice)',
    description: '语音转文字',
    hideTemperature: true,
    hideMaxTokens: true,
    advanced: true,
  },
  {
    key: 'embedding' as const,
    title: '嵌入模型 (embedding)',
    description: '用于向量化',
    hideTemperature: true,
    hideMaxTokens: true,
    hideSelectionStrategy: true,
    singleModel: true,
  },
  {
    key: 'image_embedding' as const,
    title: '图片嵌入模型 (image_embedding)',
    description: '用于图片向量化，必须实现图片输入到向量的协议',
    hideTemperature: true,
    hideMaxTokens: true,
    hideSelectionStrategy: true,
    singleModel: true,
  },
  {
    key: 'web_search' as const,
    title: '联网搜索 (web_search)',
    description: '使用已分配的联网服务搜索和读取网页',
  },
] as const

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
