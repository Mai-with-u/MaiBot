import { ApiError, backendApi } from '@/lib/http'

const PROMPT_GENERATOR_METHOD_NOT_ALLOWED_MESSAGE =
  '生成失败：可能是模型不支持或前后端版本不匹配，请换用文本聊天模型，或刷新并重启 WebUI 后再试。'

// 把 405/method not allowed 这类底层错误归一化为对用户更友好的提示后重新抛出
function normalizePromptGeneratorError(error: unknown): never {
  if (error instanceof ApiError) {
    const normalizedError = error.message.toLowerCase()
    if (normalizedError.includes('method not allowed') || normalizedError.includes('405')) {
      throw new ApiError(PROMPT_GENERATOR_METHOD_NOT_ALLOWED_MESSAGE)
    }
  }
  throw error
}

export interface PromptGeneratorChatPrompt {
  platform: string
  item_id: string
  rule_type: string
  prompt: string
}

export interface PromptGeneratorConfigBlock {
  id: string
  section: string
  field: string
  title: string
  description: string
  value: unknown
  toml: string
}

export interface PromptGeneratorResult {
  personality: string
  behavior_style: string
  reply_style: string
  multiple_reply_style: string[]
  group_chat_prompt: string
  private_chat_prompts: string
  chat_prompts: PromptGeneratorChatPrompt[]
  notes: string[]
}

export interface PromptGeneratorRequest {
  model_name: string
  source_text: string
  target_scene: string
  language: string
  extra_requirements: string
  temperature: number
  max_tokens: number
}

export interface PromptGeneratorResponse {
  success: boolean
  model_name: string
  result: PromptGeneratorResult
  config_blocks: PromptGeneratorConfigBlock[]
  toml_snippet: string
  raw_response: string
  reasoning: string
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
}

export async function generatePromptPersona(
  payload: PromptGeneratorRequest
): Promise<PromptGeneratorResponse> {
  try {
    return await backendApi.post<PromptGeneratorResponse>('/api/webui/config/prompt-generator/generate', {
      body: payload,
      errorMessage: '生成人设 Prompt 失败',
    })
  } catch (error) {
    normalizePromptGeneratorError(error)
  }
}

export interface PromptGeneratorApplyResponse {
  success: boolean
  message: string
  applied_blocks: number
  sections: string[]
}

export async function applyPromptGeneratorBlocks(
  blocks: PromptGeneratorConfigBlock[]
): Promise<PromptGeneratorApplyResponse> {
  return backendApi.post<PromptGeneratorApplyResponse>('/api/webui/config/prompt-generator/apply', {
    body: { blocks },
    errorMessage: '应用生成结果失败',
  })
}
