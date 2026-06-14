import { backendApi } from '@/lib/http'

const API_BASE = '/api/webui/config/prompts'

export interface PromptFileInfo {
  name: string
  size: number
  modified_at: number
  display_name: string
  advanced: boolean
  description: string
  customized: boolean
}

export interface PromptCatalog {
  success: boolean
  languages: string[]
  files: Record<string, PromptFileInfo[]>
}

export interface PromptFileContent {
  success: boolean
  language: string
  filename: string
  content: string
  customized: boolean
}

export async function getPromptCatalog(): Promise<PromptCatalog> {
  return backendApi.get<PromptCatalog>(API_BASE, {
    errorMessage: '获取 Prompt 文件列表失败',
  })
}

export async function getPromptFile(
  language: string,
  filename: string
): Promise<PromptFileContent> {
  return backendApi.get<PromptFileContent>(
    `${API_BASE}/${encodeURIComponent(language)}/${encodeURIComponent(filename)}`,
    {
      errorMessage: '获取 Prompt 文件失败',
    }
  )
}

export async function getDefaultPromptFile(
  language: string,
  filename: string
): Promise<PromptFileContent> {
  return backendApi.get<PromptFileContent>(
    `${API_BASE}/${encodeURIComponent(language)}/${encodeURIComponent(filename)}/default`,
    {
      errorMessage: '获取默认 Prompt 文件失败',
    }
  )
}

export async function updatePromptFile(
  language: string,
  filename: string,
  content: string
): Promise<PromptFileContent> {
  return backendApi.put<PromptFileContent>(
    `${API_BASE}/${encodeURIComponent(language)}/${encodeURIComponent(filename)}`,
    {
      body: { content },
      errorMessage: '保存 Prompt 文件失败',
    }
  )
}

export async function resetPromptFile(
  language: string,
  filename: string
): Promise<PromptFileContent> {
  return backendApi.delete<PromptFileContent>(
    `${API_BASE}/${encodeURIComponent(language)}/${encodeURIComponent(filename)}`,
    {
      errorMessage: '重置 Prompt 文件失败',
    }
  )
}
