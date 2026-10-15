import { createContext, type ReactNode, useContext, useEffect, useState } from 'react'

import { getModelConfig } from '@/lib/config-api'
import type { FieldHookComponentProps } from '@/lib/field-hooks'
import type { ModelInfo, ModelTaskConfig, ProviderConfig } from '../model/types'

const MemorySwitchContext = createContext({
  memoryEnabled: false,
  embeddingStatus: 'loading' as 'loading' | 'ready' | 'missing' | 'error',
})

export function MemorySwitchProvider({ memoryEnabled, children }: {
  memoryEnabled: boolean
  children: ReactNode
}) {
  const [embeddingStatus, setEmbeddingStatus] = useState<'loading' | 'ready' | 'missing' | 'error'>('loading')

  useEffect(() => {
    let active = true
    const refresh = async () => {
      try {
        const config = await getModelConfig()
        const tasks = config.model_task_config as ModelTaskConfig
        const models = config.models as ModelInfo[]
        const providers = config.api_providers as ProviderConfig[]
        const configured = tasks.embedding.model_list.some((name) =>
          models.some((model) => model.name === name && model.model_identifier.trim() &&
            providers.some((provider) => provider.name === model.api_provider))
        )
        if (active) setEmbeddingStatus(configured ? 'ready' : 'missing')
      } catch (error) {
        console.error('检查嵌入模型配置失败:', error)
        if (active) setEmbeddingStatus('error')
      }
    }
    void refresh()
    window.addEventListener('focus', refresh)
    return () => {
      active = false
      window.removeEventListener('focus', refresh)
    }
  }, [])

  return (
    <MemorySwitchContext.Provider value={{ memoryEnabled, embeddingStatus }}>
      {children}
    </MemorySwitchContext.Provider>
  )
}

export function MemorySwitchGuard({ fieldPath, value, children }: FieldHookComponentProps) {
  const { memoryEnabled, embeddingStatus } = useContext(MemorySwitchContext)
  const isMainSwitch = fieldPath === 'a_memorix.plugin.enabled'
  // 已开启时允许关闭；检查尚未完成或失败时，不允许开启记忆。
  const disabled = isMainSwitch
    ? value !== true && embeddingStatus !== 'ready'
    : !memoryEnabled
  const hint = isMainSwitch && disabled
    ? ({ loading: '正在检查嵌入模型配置…', missing: '请先在模型配置中配置嵌入模型。', error: '嵌入模型配置检查失败，请刷新后重试。', ready: '' })[embeddingStatus]
    : ''

  return (
    <div className="space-y-1">
      <fieldset disabled={disabled} className={disabled ? 'min-w-0 opacity-50 grayscale' : 'min-w-0'}>
        {children}
      </fieldset>
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  )
}
