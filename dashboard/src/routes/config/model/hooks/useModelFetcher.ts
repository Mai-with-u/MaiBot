/**
 * 模型列表获取 Hook
 */
import { useState, useCallback, useEffect } from 'react'
import { fetchProviderModels, type ModelListItem } from '@/lib/config-api'
import { findTemplateByBaseUrl, type ProviderTemplate } from '../../providerTemplates'
import { modelListCache, CACHE_TTL } from '../constants'
import type { ProviderConfig } from '../types'

interface UseModelFetcherOptions {
  /** 获取提供商配置的函数 */
  getProviderConfig: (providerName: string) => ProviderConfig | undefined
}

interface UseModelFetcherReturn {
  /** 可用模型列表 */
  availableModels: ModelListItem[]
  /** 是否正在获取模型列表 */
  fetchingModels: boolean
  /** 模型获取错误信息 */
  modelFetchError: string | null
  /** 匹配的模板 */
  matchedTemplate: ProviderTemplate | null
  /** 获取指定提供商的模型列表 */
  fetchModelsForProvider: (providerName: string, forceRefresh?: boolean) => Promise<void>
  /** 清空模型列表和错误状态 */
  clearModels: () => void
}

/**
 * 模型列表获取 Hook
 */
export function useModelFetcher(options: UseModelFetcherOptions): UseModelFetcherReturn {
  const { getProviderConfig } = options

  const [availableModels, setAvailableModels] = useState<ModelListItem[]>([])
  const [fetchingModels, setFetchingModels] = useState(false)
  const [modelFetchError, setModelFetchError] = useState<string | null>(null)
  const [matchedTemplate, setMatchedTemplate] = useState<ProviderTemplate | null>(null)

  // 清空模型列表和错误状态
  const clearModels = useCallback(() => {
    setAvailableModels([])
    setModelFetchError(null)
    setMatchedTemplate(null)
  }, [])

  // 获取提供商的模型列表
  const fetchModelsForProvider = useCallback(async (providerName: string, forceRefresh = false) => {
    const config = getProviderConfig(providerName)
    if (!config?.base_url) {
      setAvailableModels([])
      setMatchedTemplate(null)
      setModelFetchError('提供商配置不完整，请先在"模型提供商配置"中配置')
      return
    }

    // 检查 API Key 是否已配置
    if (!config.api_key) {
      setAvailableModels([])
      setMatchedTemplate(null)
      setModelFetchError('该提供商未配置 API Key，请先在"模型提供商配置"中填写')
      return
    }

    // 查找匹配的模板
    const template = findTemplateByBaseUrl(config.base_url)
    setMatchedTemplate(template)

    // 如果没有模板或模板不支持获取模型列表
    if (!template?.modelFetcher) {
      setAvailableModels([])
      setModelFetchError(null)
      return
    }

    // 检查缓存
    const cacheKey = `${providerName}:${config.base_url}`
    const cached = modelListCache.get(cacheKey)
    if (!forceRefresh && cached && Date.now() - cached.timestamp < CACHE_TTL) {
      setAvailableModels(cached.models)
      setModelFetchError(null)
      return
    }

    // 获取模型列表
    setFetchingModels(true)
    setModelFetchError(null)

    try {
      const models = await fetchProviderModels(
        providerName,
        template.modelFetcher.parser,
        template.modelFetcher.endpoint
      )
      setAvailableModels(models)
      // 更新缓存
      modelListCache.set(cacheKey, { models, timestamp: Date.now() })
    } catch (error) {
      console.error('获取模型列表失败:', error)
      const errorMessage = (error as Error).message || '获取模型列表失败'
      // 根据错误类型提供更友好的提示
      if (errorMessage.includes('无效') || errorMessage.includes('过期') || errorMessage.includes('API Key')) {
        setModelFetchError('API Key 无效或已过期，请检查"模型提供商配置"中的密钥')
      } else if (errorMessage.includes('权限')) {
        setModelFetchError('没有权限获取模型列表，请检查 API Key 权限')
      } else if (errorMessage.includes('timeout') || errorMessage.includes('超时')) {
        setModelFetchError('请求超时，请检查网络连接后重试')
      } else if (errorMessage.includes('不支持')) {
        setModelFetchError('该提供商不支持自动获取模型列表，请手动输入')
      } else {
        setModelFetchError(errorMessage)
      }
      setAvailableModels([])
    } finally {
      setFetchingModels(false)
    }
  }, [getProviderConfig])

  return {
    availableModels,
    fetchingModels,
    modelFetchError,
    matchedTemplate,
    fetchModelsForProvider,
    clearModels,
  }
}

/**
 * 当选择的提供商变化时自动获取模型列表的 Hook
 */
export function useAutoFetchModels(
  editDialogOpen: boolean,
  apiProvider: string | undefined,
  fetchModelsForProvider: (providerName: string, forceRefresh?: boolean) => Promise<void>
) {
  useEffect(() => {
    if (editDialogOpen && apiProvider) {
      fetchModelsForProvider(apiProvider)
    }
  }, [editDialogOpen, apiProvider, fetchModelsForProvider])
}
