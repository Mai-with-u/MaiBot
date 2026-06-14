/**
 * usePluginConfigEditor —— 单个插件配置编辑器领域 hook（页面逻辑下沉）。
 *
 * config（可视化表单值）与 sourceCode（TOML 文本）是「可编辑草稿」：
 * loadConfig 把服务端 schema/config/raw 加载进本地态，原始基线另存
 * （originalConfig / originalSourceCode）；hasChanges 直接由草稿与基线比较「派生」，
 * 不再用 effect 写 state。保存按编辑模式分流：可视化 → updatePluginConfig；
 * 源代码 → 先 parseToml 校验、失败弹提示并标记 hasTomlError，成功才 updatePluginConfigRaw。
 *
 * 离开拦截用 @tanstack/react-router 的 useBlocker（hasChanges 为真时拦截），
 * 配合内部「返回」按钮的确认对话框（internalLeavePromptOpen）。
 */
import { useBlocker } from '@tanstack/react-router'
import { useCallback, useEffect, useState } from 'react'
import { parse as parseToml } from 'smol-toml'

import {
  getPluginConfig,
  getPluginConfigRaw,
  getPluginConfigSchema,
  resetPluginConfig,
  togglePlugin,
  updatePluginConfig,
  updatePluginConfigRaw,
} from '@/lib/plugin-api'
import type { InstalledPlugin, PluginConfigSchema } from '@/lib/plugin-api'
import { useToast } from '@/hooks/use-toast'

import { setNestedField } from '../utils'

export interface UsePluginConfigEditorOptions {
  plugin: InstalledPlugin
  /** 返回列表（无未保存更改或确认离开后调用） */
  onBack: () => void
  /** 深链接初始配置标签页 */
  initialTab?: string
}

export function usePluginConfigEditor(options: UsePluginConfigEditorOptions) {
  const { plugin, onBack, initialTab } = options
  const { toast } = useToast()

  const [editMode, setEditMode] = useState<'visual' | 'source'>('visual')
  const [pluginPageTab, setPluginPageTab] = useState<'settings' | 'details'>('settings')
  const [schema, setSchema] = useState<PluginConfigSchema | null>(null)
  const [activeConfigTab, setActiveConfigTab] = useState<string | undefined>(initialTab)
  // 可编辑草稿 + 加载基线
  const [config, setConfig] = useState<Record<string, unknown>>({})
  const [originalConfig, setOriginalConfig] = useState<Record<string, unknown>>({})
  const [sourceCode, setSourceCode] = useState('')
  const [originalSourceCode, setOriginalSourceCode] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [hasTomlError, setHasTomlError] = useState(false)
  const [resetDialogOpen, setResetDialogOpen] = useState(false)
  const [internalLeavePromptOpen, setInternalLeavePromptOpen] = useState(false)

  // hasChanges 直接由草稿与基线比较派生（不用 effect 写 state）
  const hasChanges = editMode === 'visual'
    ? JSON.stringify(config) !== JSON.stringify(originalConfig)
    : sourceCode !== originalSourceCode

  const navigationBlocker = useBlocker({
    shouldBlockFn: () => hasChanges,
    enableBeforeUnload: hasChanges,
    withResolver: true,
  })

  // 加载配置
  const loadConfig = useCallback(async () => {
    setLoading(true)
    try {
      const [schemaData, configData, rawData] = await Promise.all([
        getPluginConfigSchema(plugin.id),
        getPluginConfig(plugin.id),
        getPluginConfigRaw(plugin.id)
      ])

      setSchema(schemaData)
      setConfig(configData)
      setOriginalConfig(JSON.parse(JSON.stringify(configData)))
      setSourceCode(rawData)
      setOriginalSourceCode(rawData)
    } catch (error) {
      toast({
        title: '加载配置失败',
        description: error instanceof Error ? error.message : '未知错误',
        variant: 'destructive'
      })
    } finally {
      setLoading(false)
    }
  }, [plugin.id, toast])

  useEffect(() => {
    loadConfig()
  }, [loadConfig])

  // 处理字段变化
  const handleFieldChange = useCallback((sectionName: string, fieldName: string, value: unknown) => {
    setConfig(prev => setNestedField(prev, sectionName, fieldName, value))
  }, [])

  // 源代码草稿变更（编辑后清除上次 TOML 错误标记）
  const handleSourceCodeChange = useCallback((value: string) => {
    setSourceCode(value)
    setHasTomlError(prev => (prev ? false : prev))
  }, [])

  // 保存配置
  const handleSave = useCallback(async (): Promise<boolean> => {
    setSaving(true)
    try {
      if (editMode === 'source') {
        // 源代码模式：先验证 TOML 格式
        try {
          parseToml(sourceCode)
        } catch (error) {
          setHasTomlError(true)
          toast({
            title: 'TOML 格式错误',
            description: error instanceof Error ? error.message : '无法解析 TOML 配置，请检查语法',
            variant: 'destructive'
          })
          setSaving(false)
          return false
        }

        // 格式正确，保存原始配置
        await updatePluginConfigRaw(plugin.id, sourceCode)
        setOriginalSourceCode(sourceCode)
        setHasTomlError(false)
      } else {
        // 可视化模式
        await updatePluginConfig(plugin.id, config)
        setOriginalConfig(JSON.parse(JSON.stringify(config)))
      }

      toast({
        title: '配置已保存',
        description: '更改将在插件重新加载后生效'
      })
      return true
    } catch (error) {
      toast({
        title: '保存失败',
        description: error instanceof Error ? error.message : '未知错误',
        variant: 'destructive'
      })
      return false
    } finally {
      setSaving(false)
    }
  }, [config, editMode, plugin.id, sourceCode, toast])

  const handleBack = useCallback(() => {
    if (!hasChanges) {
      onBack()
      return
    }
    setInternalLeavePromptOpen(true)
  }, [hasChanges, onBack])

  const closeLeavePrompt = useCallback(() => {
    if (navigationBlocker.status === 'blocked') {
      navigationBlocker.reset?.()
    }
    setInternalLeavePromptOpen(false)
  }, [navigationBlocker])

  const leaveWithoutSaving = useCallback(() => {
    if (internalLeavePromptOpen) {
      setInternalLeavePromptOpen(false)
      onBack()
      return
    }
    navigationBlocker.proceed?.()
  }, [internalLeavePromptOpen, navigationBlocker, onBack])

  const saveAndLeave = useCallback(async () => {
    const saved = await handleSave()
    if (!saved) {
      return
    }
    if (internalLeavePromptOpen) {
      setInternalLeavePromptOpen(false)
      onBack()
      return
    }
    navigationBlocker.proceed?.()
  }, [handleSave, internalLeavePromptOpen, navigationBlocker, onBack])

  // 重置配置
  const handleReset = useCallback(async () => {
    try {
      await resetPluginConfig(plugin.id)
      toast({
        title: '配置已重置',
        description: '下次加载插件时将使用默认配置'
      })
      setResetDialogOpen(false)
      loadConfig()
    } catch (error) {
      toast({
        title: '重置失败',
        description: error instanceof Error ? error.message : '未知错误',
        variant: 'destructive'
      })
    }
  }, [loadConfig, plugin.id, toast])

  // 切换启用状态
  const handleToggle = useCallback(async () => {
    try {
      const toggleResult = await togglePlugin(plugin.id)
      toast({
        title: toggleResult.message,
        description: toggleResult.note
      })
      loadConfig()
    } catch (error) {
      toast({
        title: '切换状态失败',
        description: error instanceof Error ? error.message : '未知错误',
        variant: 'destructive'
      })
    }
  }, [loadConfig, plugin.id, toast])

  // 配置标签页切换（同步 URL）
  const handleConfigTabChange = useCallback((nextTab: string) => {
    setActiveConfigTab(nextTab)
    const params = new URLSearchParams({ plugin: plugin.id, tab: nextTab })
    window.history.replaceState(null, '', `/plugin-config?${params.toString()}`)
  }, [plugin.id])

  return {
    // 模式 / 页签
    editMode,
    setEditMode,
    pluginPageTab,
    setPluginPageTab,
    activeConfigTab,
    handleConfigTabChange,
    // schema / 草稿
    schema,
    config,
    sourceCode,
    handleSourceCodeChange,
    handleFieldChange,
    // 状态
    loading,
    saving,
    hasChanges,
    hasTomlError,
    // 保存 / 重置 / 启停
    handleSave,
    handleReset,
    handleToggle,
    resetDialogOpen,
    setResetDialogOpen,
    // 离开拦截
    navigationBlocker,
    internalLeavePromptOpen,
    handleBack,
    closeLeavePrompt,
    leaveWithoutSaving,
    saveAndLeave,
  }
}
