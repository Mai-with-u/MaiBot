/**
 * 模型提供商模板配置
 *
 * 这些预设模板帮助用户快速配置常用的 API 提供商
 */

import type { ThinkingFormatConfig } from './model/thinkingFormats'

// 模型获取器配置定义
export interface ModelFetcherConfig {
  // 获取模型列表的端点（相对于 base_url）
  endpoint: string
  // 响应解析器类型
  parser: 'openai' | 'gemini'
}

// 提供商模板定义
export interface ProviderTemplate {
  id: string
  name: string
  base_url: string
  // 额外匹配的 base_url 正则（可选，用于含账号/地域变量的官方地址；对规范化后的 URL 整体匹配）
  baseUrlPattern?: RegExp
  client_type: 'openai' | 'openai_responses' | 'gemini'
  allowed_client_types?: Array<'openai' | 'openai_responses' | 'gemini'>
  display_name: string
  // 模型列表获取配置（可选，未配置则不支持自动获取）
  modelFetcher?: ModelFetcherConfig
  // 思考开关格式元数据（可选，未配置则该提供商不显示思考开关）
  thinking?: ThinkingFormatConfig
}

// 内置提供商模板
export const PROVIDER_TEMPLATES: ProviderTemplate[] = [
  // 国内提供商
  {
    id: 'deepseek',
    name: 'DeepSeek',
    base_url: 'https://api.deepseek.com',
    client_type: 'openai',
    allowed_client_types: ['openai', 'openai_responses'],
    display_name: 'DeepSeek',
    modelFetcher: { endpoint: '/models', parser: 'openai' },
    // 思考开关由 model.tsx 中 DeepSeek 专用段处理（含 Responses 客户端的 reasoning.effort 与联网搜索）
  },
  {
    id: 'zhipu',
    name: 'ZhipuAI',
    base_url: 'https://open.bigmodel.cn/api/paas/v4',
    client_type: 'openai',
    display_name: '智谱 AI (ZhipuAI / GLM)',
    modelFetcher: { endpoint: '/models', parser: 'openai' },
    thinking: {
      kind: 'thinking_type',
      onValue: 'enabled',
      canDisable: true,
      effortParam: 'reasoning_effort',
      efforts: ['max', 'xhigh', 'high', 'medium', 'low', 'minimal', 'none'],
      defaultEffort: 'max',
    },
  },
  {
    id: 'zhipu_coding',
    name: 'ZhipuAI Coding',
    base_url: 'https://open.bigmodel.cn/api/coding/paas/v4',
    client_type: 'openai',
    display_name: '智谱编程套餐 (GLM Coding Plan)',
    modelFetcher: { endpoint: '/models', parser: 'openai' },
    thinking: {
      kind: 'thinking_type',
      onValue: 'enabled',
      canDisable: true,
      effortParam: 'reasoning_effort',
      efforts: ['max', 'xhigh', 'high', 'medium', 'low', 'minimal', 'none'],
      defaultEffort: 'max',
    },
  },
  {
    id: 'moonshot',
    name: 'Moonshot',
    base_url: 'https://api.moonshot.cn/v1',
    client_type: 'openai',
    display_name: '月之暗面 (Moonshot / Kimi)',
    modelFetcher: { endpoint: '/models', parser: 'openai' },
    thinking: {
      kind: 'thinking_type',
      onValue: 'enabled',
      canDisable: true,
      // k3 系列不收 thinking 参数，只收 reasoning_effort；k2.7-code 传 disabled 会报错
      disableNote: 'k2.7-code 仅支持开启思考；k3 系列不使用 thinking 参数，仅支持 reasoning_effort 调档',
    },
  },
  {
    id: 'doubao',
    name: 'Doubao',
    base_url: 'https://ark.cn-beijing.volces.com/api/v3',
    client_type: 'openai',
    display_name: '字节豆包 (Doubao)',
    modelFetcher: { endpoint: '/models', parser: 'openai' },
    thinking: {
      kind: 'thinking_type',
      // 豆包的 type 支持 auto/enabled/disabled，开启时写入 auto 由模型自行决定
      onValue: 'auto',
      canDisable: true,
      budgetParam: 'thinking_budget',
    },
  },
  {
    id: 'doubao_coding',
    name: 'Doubao Coding',
    base_url: 'https://ark.cn-beijing.volces.com/api/coding/v3',
    client_type: 'openai',
    display_name: '火山方舟编程套餐 (Ark Coding Plan)',
    // 套餐 Base URL 可能因账号而异，以控制台给出的专属地址为准
    thinking: {
      kind: 'thinking_type',
      onValue: 'auto',
      canDisable: true,
      budgetParam: 'thinking_budget',
      note: '套餐凭证与按量付费 API Key 不通用；若控制台给出专属地址，请以专属地址为准',
    },
  },
  {
    id: 'alibaba',
    name: 'Alibaba',
    base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    // 业务空间专属域名 https://{WorkspaceId}.{region}.maas.aliyuncs.com/compatible-mode/v1
    baseUrlPattern: /^https?:\/\/[a-z0-9-]+\.[a-z0-9-]+\.maas\.aliyuncs\.com\/compatible-mode\/v1$/,
    client_type: 'openai',
    display_name: '阿里云百炼 (Alibaba Qwen)',
    modelFetcher: { endpoint: '/models', parser: 'openai' },
    thinking: {
      kind: 'enable_thinking',
      budgetParam: 'thinking_budget',
      // 混合思考模型还支持在 prompt 末尾追加 /think 或 /no_think 临时切换
      note: '混合思考模型另支持 prompt 后缀 /think、/no_think 临时切换',
    },
  },
  {
    id: 'baichuan',
    name: 'Baichuan',
    base_url: 'https://api.baichuan-ai.com/v1',
    client_type: 'openai',
    display_name: '百川智能 (Baichuan)',
    modelFetcher: { endpoint: '/models', parser: 'openai' },
  },
  {
    id: 'minimax',
    name: 'MiniMax',
    base_url: 'https://api.minimax.chat/v1',
    client_type: 'openai',
    display_name: 'MiniMax (海螺 AI)',
    modelFetcher: { endpoint: '/models', parser: 'openai' },
    thinking: {
      kind: 'thinking_type',
      // MiniMax 的 type 值域为 adaptive/disabled，默认 adaptive
      onValue: 'adaptive',
      canDisable: true,
      // M2.x 系列思考无法关闭，disabled 仅对 M3 生效
      disableNote: 'M2.x 系列思考无法关闭，disabled 仅对 M3 生效',
      note: '思维链内容需配合 reasoning_split: true 才会返回到 reasoning_content',
    },
  },
  {
    id: 'stepfun',
    name: 'StepFun',
    base_url: 'https://api.stepfun.com/v1',
    client_type: 'openai',
    display_name: '阶跃星辰 (StepFun)',
    modelFetcher: { endpoint: '/models', parser: 'openai' },
    thinking: {
      kind: 'reasoning_effort',
      efforts: ['low', 'medium', 'high'],
      defaultEffort: 'medium',
    },
  },
  {
    id: 'stepfun_plan',
    name: 'StepFun Plan',
    base_url: 'https://api.stepfun.com/step_plan/v1',
    client_type: 'openai',
    display_name: '阶跃 Step Plan 套餐',
    modelFetcher: { endpoint: '/models', parser: 'openai' },
    thinking: {
      kind: 'reasoning_effort',
      efforts: ['low', 'medium', 'high'],
      defaultEffort: 'medium',
    },
  },
  {
    id: 'siliconflow',
    name: 'SiliconFlow',
    base_url: 'https://api.siliconflow.cn/v1',
    client_type: 'openai',
    display_name: '硅基流动 (SiliconFlow)',
    modelFetcher: { endpoint: '/models', parser: 'openai' },
    thinking: {
      kind: 'enable_thinking',
      budgetParam: 'thinking_budget',
    },
  },

  // 国际提供商
  {
    id: 'openai',
    name: 'OpenAI',
    base_url: 'https://api.openai.com/v1',
    client_type: 'openai',
    display_name: 'OpenAI',
    modelFetcher: { endpoint: '/models', parser: 'openai' },
    thinking: {
      kind: 'reasoning_effort',
      efforts: ['minimal', 'low', 'medium', 'high'],
      defaultEffort: 'medium',
    },
  },
  {
    id: 'xai',
    name: 'xAI',
    base_url: 'https://api.x.ai/v1',
    client_type: 'openai',
    display_name: 'xAI (Grok)',
    modelFetcher: { endpoint: '/models', parser: 'openai' },
    thinking: {
      kind: 'reasoning_effort',
      efforts: ['low', 'high'],
    },
  },
  {
    id: 'anthropic',
    name: 'Anthropic',
    base_url: 'https://api.anthropic.com/v1',
    client_type: 'openai',
    display_name: 'Anthropic (Claude)',
    // Anthropic 使用不同的 API 格式，暂不支持自动获取
  },
  {
    id: 'gemini',
    name: 'Gemini',
    base_url: 'https://generativelanguage.googleapis.com/v1beta',
    client_type: 'gemini',
    display_name: 'Google Gemini',
    modelFetcher: { endpoint: '/models', parser: 'gemini' },
  },
  {
    id: 'cohere',
    name: 'Cohere',
    base_url: 'https://api.cohere.ai/v1',
    client_type: 'openai',
    display_name: 'Cohere',
    // Cohere 使用不同的 API 格式，暂不支持自动获取
  },
  {
    id: 'groq',
    name: 'Groq',
    base_url: 'https://api.groq.com/openai/v1',
    client_type: 'openai',
    display_name: 'Groq',
    modelFetcher: { endpoint: '/models', parser: 'openai' },
  },
  {
    id: 'mistral',
    name: 'Mistral',
    base_url: 'https://api.mistral.ai/v1',
    client_type: 'openai',
    display_name: 'Mistral AI',
    modelFetcher: { endpoint: '/models', parser: 'openai' },
  },
  {
    id: 'perplexity',
    name: 'Perplexity',
    base_url: 'https://api.perplexity.ai',
    client_type: 'openai',
    display_name: 'Perplexity AI',
    // Perplexity 不支持 /models 端点，自动获取失败后会提示手动填写
  },

  // 自定义选项
  {
    id: 'custom',
    name: '',
    base_url: '',
    client_type: 'openai',
    display_name: '自定义',
  },
]

/**
 * 规范化 URL（去掉尾部斜杠，统一格式）
 */
export function normalizeUrl(url: string): string {
  if (!url) return ''
  // 去掉尾部斜杠
  const normalized = url.replace(/\/+$/, '')
  // 转小写用于比较
  return normalized.toLowerCase()
}

/**
 * 根据 base_url 查找匹配的模板
 * @param baseUrl 提供商的 base_url
 * @returns 匹配的模板，如果未找到则返回 null
 */
export function findTemplateByBaseUrl(baseUrl: string): ProviderTemplate | null {
  if (!baseUrl) return null

  const normalizedUrl = normalizeUrl(baseUrl)

  return (
    PROVIDER_TEMPLATES.find(
      (template) =>
        template.id !== 'custom' &&
        (normalizeUrl(template.base_url) === normalizedUrl ||
          (template.baseUrlPattern?.test(normalizedUrl) ?? false))
    ) || null
  )
}

export function resolveThinkingFormatForModel(
  template: ProviderTemplate | null,
  modelIdentifier: string
): ThinkingFormatConfig | null {
  const thinking = template?.thinking
  if (!thinking) return null

  // 普通智谱 API 的 GLM-5.3 系列拒绝关闭思考；Coding 套餐使用独立模板，不受此限制。
  if (template?.id === 'zhipu' && /^glm-5\.3(?:$|-)/i.test(modelIdentifier.trim())) {
    return {
      ...thinking,
      canDisable: false,
      efforts: ['low', 'high', 'max'],
      defaultEffort: 'max',
      disableNote: '此模型不支持关闭思考',
    }
  }

  return thinking
}

/**
 * 按客户端类型推导默认的模型列表获取器。
 * 所有提供商统一乐观尝试 /models：端点不支持时由调用方提示手动填写。
 */
function defaultModelFetcher(clientType: string): ModelFetcherConfig {
  return {
    endpoint: '/models',
    parser: clientType === 'gemini' ? 'gemini' : 'openai',
  }
}

/**
 * 根据提供商配置查找可用于获取模型列表的模板。
 *
 * 所有提供商默认都能获取模型列表：命中内置模板时按模板配置，
 * 未配置 modelFetcher 的内置模板与未命中内置模板的自定义端点，
 * 均按客户端类型乐观尝试 /models（Gemini 使用 Gemini 解析器）。
 */
export function resolveModelFetcherTemplate(
  baseUrl: string,
  clientType = 'openai'
): ProviderTemplate | null {
  const matchedTemplate = findTemplateByBaseUrl(baseUrl)
  if (matchedTemplate) {
    return matchedTemplate.modelFetcher
      ? matchedTemplate
      : { ...matchedTemplate, modelFetcher: defaultModelFetcher(clientType) }
  }
  if (!baseUrl) return null

  const isGemini = clientType === 'gemini'
  const isOpenAIResponses = clientType === 'openai_responses'
  return {
    id: isGemini
      ? 'custom-gemini'
      : isOpenAIResponses
        ? 'custom-openai-responses'
        : 'custom-openai-compatible',
    name: '',
    base_url: baseUrl,
    client_type: isGemini ? 'gemini' : isOpenAIResponses ? 'openai_responses' : 'openai',
    display_name: isGemini
      ? '自定义 Gemini 端点'
      : isOpenAIResponses
        ? '自定义 OpenAI Responses 端点'
        : '自定义 OpenAI 兼容端点',
    modelFetcher: {
      endpoint: '/models',
      parser: isGemini ? 'gemini' : 'openai',
    },
  }
}
