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
  custom_version_count: number
}

export interface PromptValidationResult {
  valid: boolean
  missing_placeholders: string[]
  extra_placeholders: string[]
  message: string
}

export interface PromptVersionInfo {
  id: string
  label: string
  created_at: number
  modified_at: number
  size: number
  active: boolean
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
  active_version_id: string | null
  versions: PromptVersionInfo[]
  validation: PromptValidationResult
}

export interface PromptUpdateOptions {
  versionId?: string | null
  label?: string
  createVersion?: boolean
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
  content: string,
  options: PromptUpdateOptions = {}
): Promise<PromptFileContent> {
  return backendApi.put<PromptFileContent>(
    `${API_BASE}/${encodeURIComponent(language)}/${encodeURIComponent(filename)}`,
    {
      body: {
        content,
        version_id: options.versionId,
        label: options.label ?? '',
        create_version: options.createVersion ?? false,
      },
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

export async function getPromptVersionFile(
  language: string,
  filename: string,
  versionId: string
): Promise<PromptFileContent> {
  return backendApi.get<PromptFileContent>(
    `${API_BASE}/${encodeURIComponent(language)}/${encodeURIComponent(filename)}/versions/${encodeURIComponent(versionId)}`,
    {
      errorMessage: '获取 Prompt 版本失败',
    }
  )
}

export async function activatePromptVersion(
  language: string,
  filename: string,
  versionId: string
): Promise<PromptFileContent> {
  return backendApi.post<PromptFileContent>(
    `${API_BASE}/${encodeURIComponent(language)}/${encodeURIComponent(filename)}/versions/${encodeURIComponent(versionId)}/activate`,
    {
      errorMessage: '启用 Prompt 版本失败',
    }
  )
}

export async function deletePromptVersion(
  language: string,
  filename: string,
  versionId: string
): Promise<PromptFileContent> {
  return backendApi.delete<PromptFileContent>(
    `${API_BASE}/${encodeURIComponent(language)}/${encodeURIComponent(filename)}/versions/${encodeURIComponent(versionId)}`,
    {
      errorMessage: '删除 Prompt 版本失败',
    }
  )
}
