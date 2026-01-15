/**
 * 模型提供商模板配置
 * 
 * 这些预设模板帮助用户快速配置常用的 API 提供商
 */

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
  client_type: 'openai' | 'gemini'
  display_name: string
  // 模型列表获取配置（可选，未配置则不支持自动获取）
  modelFetcher?: ModelFetcherConfig
}

// 内置提供商模板
export const PROVIDER_TEMPLATES: ProviderTemplate[] = [
  // 国内提供商
  {
    id: 'siliconflow',
    name: 'SiliconFlow',
    base_url: 'https://api.siliconflow.cn/v1',
    client_type: 'openai',
    display_name: '硅基流动 (SiliconFlow)',
    modelFetcher: { endpoint: '/models', parser: 'openai' },
  },
  {
    id: 'deepseek',
    name: 'DeepSeek',
    base_url: 'https://api.deepseek.com',
    client_type: 'openai',
    display_name: 'DeepSeek',
    modelFetcher: { endpoint: '/models', parser: 'openai' },
  },
  {
    id: 'rinkoai',
    name: 'RinkoAI',
    base_url: 'https://rinkoai.com/v1',
    client_type: 'openai',
    display_name: 'RinkoAI',
    modelFetcher: { endpoint: '/models', parser: 'openai' },
  },
  {
    id: 'zhipu',
    name: 'ZhipuAI',
    base_url: 'https://open.bigmodel.cn/api/paas/v4',
    client_type: 'openai',
    display_name: '智谱 AI (ZhipuAI / GLM)',
    modelFetcher: { endpoint: '/models', parser: 'openai' },
  },
  {
    id: 'moonshot',
    name: 'Moonshot',
    base_url: 'https://api.moonshot.cn/v1',
    client_type: 'openai',
    display_name: '月之暗面 (Moonshot / Kimi)',
    modelFetcher: { endpoint: '/models', parser: 'openai' },
  },
  {
    id: 'doubao',
    name: 'Doubao',
    base_url: 'https://ark.cn-beijing.volces.com/api/v3',
    client_type: 'openai',
    display_name: '字节豆包 (Doubao)',
    modelFetcher: { endpoint: '/models', parser: 'openai' },
  },
  {
    id: 'alibaba',
    name: 'Alibaba',
    base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    client_type: 'openai',
    display_name: '阿里云百炼 (Alibaba Qwen)',
    modelFetcher: { endpoint: '/models', parser: 'openai' },
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
  },
  {
    id: 'stepfun',
    name: 'StepFun',
    base_url: 'https://api.stepfun.com/v1',
    client_type: 'openai',
    display_name: '阶跃星辰 (StepFun)',
    modelFetcher: { endpoint: '/models', parser: 'openai' },
  },
  {
    id: 'lingyi',
    name: 'Lingyi',
    base_url: 'https://api.lingyiwanwu.com/v1',
    client_type: 'openai',
    display_name: '零一万物 (Lingyi / Yi)',
    modelFetcher: { endpoint: '/models', parser: 'openai' },
  },

  // 国际提供商
  {
    id: 'openai',
    name: 'OpenAI',
    base_url: 'https://api.openai.com/v1',
    client_type: 'openai',
    display_name: 'OpenAI',
    modelFetcher: { endpoint: '/models', parser: 'openai' },
  },
  {
    id: 'xai',
    name: 'xAI',
    base_url: 'https://api.x.ai/v1',
    client_type: 'openai',
    display_name: 'xAI (Grok)',
    modelFetcher: { endpoint: '/models', parser: 'openai' },
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
    id: 'together',
    name: 'Together AI',
    base_url: 'https://api.together.xyz/v1',
    client_type: 'openai',
    display_name: 'Together AI',
    modelFetcher: { endpoint: '/models', parser: 'openai' },
  },
  {
    id: 'fireworks',
    name: 'Fireworks',
    base_url: 'https://api.fireworks.ai/inference/v1',
    client_type: 'openai',
    display_name: 'Fireworks AI',
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
    // Perplexity 不支持 /models 端点
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
  
  return PROVIDER_TEMPLATES.find(template => 
    template.id !== 'custom' && 
    normalizeUrl(template.base_url) === normalizedUrl
  ) || null
}
