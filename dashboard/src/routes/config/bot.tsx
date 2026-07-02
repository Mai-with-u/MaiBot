import { Fragment, type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from '@tanstack/react-router'
import {
  Check,
  ChevronLeft,
  ChevronRight,
  Code2,
  ExternalLink,
  Info,
  Layout,
  RefreshCw,
  Save,
  X,
} from 'lucide-react'
import { parse as parseToml } from 'smol-toml'

import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { DashboardTabBar, DashboardTabTrigger } from '@/components/ui/dashboard-tabs'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { ThinkingIllustration } from '@/components/ui/thinking-illustration'
import { CodeEditor } from '@/components/CodeEditor'
import { DynamicConfigForm } from '@/components/dynamic-form'
import { RestartOverlay } from '@/components/restart-overlay'
import { useToast } from '@/hooks/use-toast'
import {
  getBotConfig,
  getBotConfigCached,
  getBotConfigRaw,
  getBotConfigSchema,
  updateBotConfig,
  updateBotConfigRaw,
} from '@/lib/config-api'
import { fieldHooks } from '@/lib/field-hooks'
import { RestartProvider, useRestart } from '@/lib/restart-context'
import { cn } from '@/lib/utils'

import type { ConfigSchema } from '@/types/config-schema'
import {
  AliasNamesHook,
  AMemorixSharedMemoryGroupsHook,
  AMemorixRetrievalChatsHook,
  AMemorixRetrievalFilterGroupHook,
  BehaviorGroupsHook,
  BehaviorFocusGroupsHook,
  BehaviorLearningListHook,
  BotPlatformAccountsHook,
  ChatPromptsHook,
  ChatTalkValueRulesHook,
  ExpressionGroupsHook,
  ExpressionLearningListHook,
  FocusWhitelistHook,
  JargonGroupsHook,
  JargonLearningListHook,
  KeywordRulesHook,
  HiddenFieldHook,
  MCPRootItemsHook,
  MCPServersHook,
  MultipleReplyStyleHook,
  RegexRulesHook,
  useAutoSave,
} from './bot/hooks'

type ConfigSectionData = Record<string, unknown>
// ==================== 常量定义 ====================
/** Toast 显示前的延迟时间 (毫秒) */
const TOAST_DISPLAY_DELAY = 500
const FILE_MODE_NOTICE_DISMISSED_KEY = 'bot-config-file-mode-notice-dismissed'

// ==================== Tab 分组类型与构建 ====================
interface TabGroup {
  id: string
  label: string
  advanced: boolean
  order: number
  sections: string[]
}

interface SubtabPane {
  advanced: boolean
  content: ReactNode
  id: string
  label: string
}

/**
 * 从 schema 的 nested 字段解析出 tab 分组信息。
 * - 有 uiLabel 且无 uiParent → 独立 tab
 * - 有 uiParent → 递归找到最终 host，并归入对应 tab
 */
function buildTabGroupsFromSchema(schema: ConfigSchema): TabGroup[] {
  const nested = schema.nested || {}
  const nestedEntries = Object.entries(nested)
  const hosts = new Map<string, TabGroup>()

  const resolveHostId = (fieldName: string, visited: Set<string> = new Set()): string | null => {
    if (visited.has(fieldName)) {
      return null
    }

    const fieldSchema = nested[fieldName]
    if (!fieldSchema) {
      return null
    }

    if (!fieldSchema.uiParent) {
      return fieldSchema.uiLabel ? fieldName : null
    }

    visited.add(fieldName)
    return resolveHostId(fieldSchema.uiParent, visited)
  }

  for (const [fieldName, fieldSchema] of nestedEntries) {
    if (fieldSchema.uiLabel && !fieldSchema.uiParent) {
      hosts.set(fieldName, {
        id: fieldName,
        label: fieldSchema.uiLabel,
        advanced: Boolean(fieldSchema.uiAdvanced),
        order: fieldSchema.uiOrder ?? Number.POSITIVE_INFINITY,
        sections: [fieldName],
      })
    }
  }

  for (const [fieldName] of nestedEntries) {
    const hostId = resolveHostId(fieldName)
    if (!hostId || hostId === fieldName) {
      continue
    }

    const parent = hosts.get(hostId)
    if (parent && !parent.sections.includes(fieldName)) {
      parent.sections.push(fieldName)
    }
  }

  return Array.from(hosts.values()).sort((a, b) => {
    const orderDelta = a.order - b.order
    if (orderDelta !== 0) {
      return orderDelta
    }
    return a.label.localeCompare(b.label, 'zh-CN')
  })
}

// 主导出组件：包装 RestartProvider
export function BotConfigPage() {
  return (
    <RestartProvider>
      <BotConfigPageContent />
    </RestartProvider>
  )
}

// 内部实现组件
function BotConfigPageContent() {
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [autoSaving, setAutoSaving] = useState(false)
  const [hasUnsavedChanges, setHasUnsavedChanges] = useState(false)
  const [editMode, setEditMode] = useState<'visual' | 'source'>('visual')
  const [sourceCode, setSourceCode] = useState<string>('')
  const [hasTomlError, setHasTomlError] = useState(false)
  const [tomlErrorMessage, setTomlErrorMessage] = useState<string>('')
  const [showFileModeNotice, setShowFileModeNotice] = useState(
    () => localStorage.getItem(FILE_MODE_NOTICE_DISMISSED_KEY) !== 'true'
  )
  const { toast } = useToast()
  const { triggerRestart, isRestarting } = useRestart()

  const [sectionValues, setSectionValues] = useState<Record<string, ConfigSectionData | null>>({})

  // Schema 状态（用于动态 tab 分组）
  const [configSchema, setConfigSchema] = useState<ConfigSchema | null>(null)

  // 用于标记初始加载和配置缓存
  const initialLoadRef = useRef(true)
  const configRef = useRef<Record<string, unknown>>({})

  // ==================== 辅助函数 ====================

  /**
   * 翻译 TOML 错误信息为中文
   */
  const translateTomlError = (errorMessage: string): string => {
    // 分行处理，保留多行格式
    const lines = errorMessage.split('\n')

    // 翻译第一行（主要错误信息）
    let firstLine = lines[0]

    // 移除 "Error: " 前缀（如果有）
    firstLine = firstLine.replace(/^Error:\s*/, '')

    // 常见 TOML 错误模式匹配和翻译
    const translations: Array<[RegExp, string | ((match: RegExpMatchArray) => string)]> = [
      // Invalid TOML document 系列
      [
        /Invalid TOML document: unrecognized escape sequence/,
        'TOML 文档错误：无法识别的转义序列（提示：在双引号字符串中使用 \\\\ 转义反斜杠，或使用单引号字符串）',
      ],
      [
        /Invalid TOML document: only letter, numbers, dashes and underscores are allowed in keys/,
        'TOML 文档错误：键名只能包含字母、数字、短横线和下划线',
      ],
      [/Invalid TOML document: (.+)/, 'TOML 文档错误：$1'],

      // 位置错误系列
      [/Unexpected character.*at line (\d+), column (\d+)/, '第 $1 行第 $2 列：意外的字符'],
      [/Expected.*at line (\d+), column (\d+)/, '第 $1 行第 $2 列：缺少必要的字符'],
      [/Invalid.*at line (\d+), column (\d+)/, '第 $1 行第 $2 列：无效的语法'],
      [/Unterminated string at line (\d+)/, '第 $1 行：字符串未正常结束（缺少引号）'],
      [/Duplicate key.*at line (\d+)/, '第 $1 行：重复的键名'],
      [
        /Invalid escape sequence at line (\d+)/,
        '第 $1 行：无效的转义序列（提示：在双引号字符串中使用 \\\\ 转义反斜杠）',
      ],
      [/Expected.*but got.*at line (\d+)/, '第 $1 行：类型不匹配'],
      [/line (\d+), column (\d+)/, '第 $1 行第 $2 列'],

      // 通用错误系列
      [/Unexpected end of input/, '意外的文件结束（可能缺少闭合符号）'],
      [/Unexpected token/, '意外的标记'],
      [/Invalid number/, '无效的数字'],
      [/Invalid date/, '无效的日期格式'],
      [/Invalid boolean/, '无效的布尔值（应为 true 或 false）'],
      [/Unexpected character/, '意外的字符'],
      [/unrecognized escape sequence/, '无法识别的转义序列'],
    ]

    // 尝试翻译第一行
    for (const [pattern, replacement] of translations) {
      if (pattern.test(firstLine)) {
        firstLine = firstLine.replace(pattern, replacement as string)
        break
      }
    }

    // 重组多行错误信息
    if (lines.length > 1) {
      lines[0] = firstLine
      return lines.join('\n')
    }

    return firstLine
  }

  /**
   * 解析并设置所有配置状态
   * 抽取自 loadConfig 和 handleModeChange 中的重复逻辑
   */
  const parseAndSetConfig = useCallback((config: Record<string, unknown>) => {
    const { memory: _legacyMemory, ...configWithoutLegacyMemory } = config
    configRef.current = configWithoutLegacyMemory

    setSectionValues(
      Object.fromEntries(
        Object.entries(configWithoutLegacyMemory).map(([sectionName, sectionValue]) => [
          sectionName,
          (sectionValue ?? {}) as ConfigSectionData,
        ])
      )
    )
  }, [])

  /**
   * 构建完整的配置对象用于保存
   */
  const buildFullConfig = useCallback(() => {
    const cleanSectionValues = Object.fromEntries(
      Object.entries(sectionValues).filter(([, value]) => value !== null)
    )

    return {
      ...configRef.current,
      ...cleanSectionValues,
    }
  }, [sectionValues])

  // 加载源代码
  const loadSourceCode = useCallback(async () => {
    try {
      const result = await getBotConfigRaw()
      const raw = (result as unknown as Record<string, unknown>).content as string
      // 将 TOML 基本字符串中的转义序列转换为实际字符以便在编辑器中正确显示
      // 使用正则表达式只处理双引号字符串内的转义序列，不影响单引号字符串
      const unescaped = raw.replace(/"([^"]*)"/g, (_match, content) => {
        const decoded = content
          .replace(/\\n/g, '\n') // 换行符
          .replace(/\\t/g, '\t') // 制表符
          .replace(/\\r/g, '\r') // 回车符
          .replace(/\\"/g, '"') // 双引号
          .replace(/\\\\/g, '\\') // 反斜杠（必须放在最后）
        return `"${decoded}"`
      })
      setSourceCode(unescaped)
      setHasTomlError(false)
    } catch (error) {
      toast({
        variant: 'destructive',
        title: '加载失败',
        description: error instanceof Error ? error.message : '加载源代码失败',
      })
    }
  }, [toast])

  // 加载配置
  const loadConfig = useCallback(async () => {
    try {
      setLoading(true)
      // 用 allSettled：主配置为必需，schema 为可选，二者失败互不影响
      const [result, schemaResult] = await Promise.allSettled([
        getBotConfigCached(),
        getBotConfigSchema(),
      ])
      if (result.status !== 'fulfilled') {
        toast({
          title: '加载失败',
          description: result.reason instanceof Error ? result.reason.message : '加载配置失败',
          variant: 'destructive',
        })
        setLoading(false)
        return
      }
      parseAndSetConfig(result.value)
      if (schemaResult.status === 'fulfilled' && schemaResult.value) {
        setConfigSchema(
          (schemaResult.value as unknown as Record<string, unknown>).schema as ConfigSchema
        )
      }
      setHasUnsavedChanges(false)
      initialLoadRef.current = false
    } catch (error) {
      console.error('加载配置失败:', error)
      toast({
        title: '加载失败',
        description: '无法加载配置文件',
        variant: 'destructive',
      })
    } finally {
      setLoading(false)
    }
  }, [toast, parseAndSetConfig])

  useEffect(() => {
    loadConfig()
  }, [loadConfig])

  useEffect(() => {
    const hookEntries = [
      ['bot.platform', BotPlatformAccountsHook, 'replace'],
      ['bot.alias_names', AliasNamesHook],
      ['bot.qq_account', HiddenFieldHook, 'hidden'],
      ['bot.platforms', HiddenFieldHook, 'hidden'],
      ['personality.multiple_reply_style', MultipleReplyStyleHook],
      ['chat.reply_style.chat_prompts', ChatPromptsHook],
      ['chat.reply_timing.talk_value_rules', ChatTalkValueRulesHook],
      ['experimental.focus_chat_whitelist', FocusWhitelistHook],
      ['experimental.focus_groups', BehaviorFocusGroupsHook],
      ['experimental.behavior_groups', BehaviorGroupsHook],
      ['experimental.behavior_learning_list', BehaviorLearningListHook],
      ['expression.expression_groups', ExpressionGroupsHook],
      ['expression.learning_list', ExpressionLearningListHook],
      ['jargon.jargon_groups', JargonGroupsHook],
      ['jargon.learning_list', JargonLearningListHook],
      ['a_memorix.global_memory_sharing_enabled', HiddenFieldHook, 'hidden'],
      ['a_memorix.shared_memory_groups', AMemorixSharedMemoryGroupsHook],
      ['a_memorix.filter.chats', AMemorixRetrievalChatsHook],
      ['a_memorix.filter.retrieval', AMemorixRetrievalFilterGroupHook, 'wrapper'],
      ['a_memorix.filter.retrieval.chat_stream.chats', AMemorixRetrievalChatsHook],
      ['a_memorix.filter.retrieval.chat_summary.chats', AMemorixRetrievalChatsHook],
      ['a_memorix.filter.retrieval.episode.chats', AMemorixRetrievalChatsHook],
      ['keyword_reaction.keyword_rules', KeywordRulesHook],
      ['keyword_reaction.regex_rules', RegexRulesHook],
      ['mcp.client.roots.items', MCPRootItemsHook],
      ['mcp.servers', MCPServersHook],
    ] as const

    for (const [fieldPath, hookComponent, hookType = 'replace'] of hookEntries) {
      fieldHooks.register(fieldPath, hookComponent, hookType)
    }

    return () => {
      for (const [fieldPath] of hookEntries) {
        fieldHooks.unregister(fieldPath)
      }
    }
  })

  const { triggerAutoSave, cancelPendingAutoSave } = useAutoSave(
    initialLoadRef.current,
    setAutoSaving,
    setHasUnsavedChanges
  )

  const dismissFileModeNotice = useCallback(() => {
    localStorage.setItem(FILE_MODE_NOTICE_DISMISSED_KEY, 'true')
    setShowFileModeNotice(false)
  }, [])

  // 保存源代码
  const saveSourceCode = async () => {
    try {
      setSaving(true)
      // 编辑器展示时会把 basic string 内的 \n 展开成真实换行；保存前先转回 TOML 转义序列。
      const escapedSourceCode = sourceCode.replace(/"([^"]*)"/g, (_match, content) => {
        const encoded = content
          .replace(/\\/g, '\\\\') // 反斜杠必须先转义，避免 \s 等序列被 TOML 当作非法转义
          .replace(/"/g, '\\"')
          .replace(/\n/g, '\\n')
          .replace(/\t/g, '\\t')
          .replace(/\r/g, '\\r')
        return `"${encoded}"`
      })

      // 前端验证 TOML 格式
      try {
        parseToml(escapedSourceCode)
      } catch (error) {
        const errorMsg = error instanceof Error ? error.message : 'TOML 格式错误'
        const translatedMsg = translateTomlError(errorMsg)
        setHasTomlError(true)
        setTomlErrorMessage(translatedMsg)
        toast({
          variant: 'destructive',
          title: 'TOML 格式错误',
          description: translatedMsg,
        })
        setSaving(false)
        return
      }

      await updateBotConfigRaw(escapedSourceCode)
      setHasUnsavedChanges(false)
      setHasTomlError(false)
      setTomlErrorMessage('')
      toast({
        title: '保存成功',
        description: '配置已保存',
      })
      // 重新加载可视化配置
      await loadConfig()
    } catch (error) {
      setHasTomlError(true)
      const errorMsg = error instanceof Error ? error.message : '保存配置失败'
      setTomlErrorMessage(errorMsg)
      toast({
        variant: 'destructive',
        title: '保存失败',
        description: errorMsg,
      })
    } finally {
      setSaving(false)
    }
  }

  // 处理模式切换
  const handleModeChange = async (mode: 'visual' | 'source') => {
    if (hasUnsavedChanges) {
      toast({
        variant: 'destructive',
        title: '切换失败',
        description: '请先保存当前更改',
      })
      return
    }

    setEditMode(mode)
    if (mode === 'source') {
      await loadSourceCode()
    } else {
      // 切换回可视化时,直接重新加载配置但不显示全局 loading
      try {
        const result = await getBotConfig()
        parseAndSetConfig(result)
        setHasUnsavedChanges(false)
      } catch (error) {
        console.error('加载配置失败:', error)
        toast({
          title: '加载失败',
          description: '无法加载配置文件',
          variant: 'destructive',
        })
      }
    }
  }

  // 手动保存
  const saveConfig = async () => {
    try {
      setSaving(true)
      // 取消待处理的自动保存
      cancelPendingAutoSave()

      await updateBotConfig(buildFullConfig())
      setHasUnsavedChanges(false)
      toast({
        title: '保存成功',
        description: '麦麦设置已保存',
      })
    } catch (error) {
      console.error('保存配置失败:', error)
      toast({
        title: '保存失败',
        description: (error as Error).message,
        variant: 'destructive',
      })
    } finally {
      setSaving(false)
    }
  }

  const handleReloadFromFile = async () => {
    cancelPendingAutoSave()
    await loadConfig()
    if (editMode === 'source') {
      await loadSourceCode()
    }
    setHasUnsavedChanges(false)
    toast({
      title: '已刷新',
      description: '已从 bot_config.toml 重新读取配置',
    })
  }

  // 重启麦麦
  const handleRestart = async () => {
    await triggerRestart()
  }

  // 保存并重启
  const handleSaveAndRestart = async () => {
    try {
      setSaving(true)
      // 取消待处理的自动保存
      cancelPendingAutoSave()

      await updateBotConfig(buildFullConfig())
      setHasUnsavedChanges(false)
      toast({
        title: '保存成功',
        description: '配置已保存，即将重启麦麦...',
      })
      // 等待一下让用户看到保存成功的提示
      await new Promise((resolve) => setTimeout(resolve, TOAST_DISPLAY_DELAY))
      await handleRestart()
    } catch (error) {
      console.error('保存失败:', error)
      toast({
        title: '保存失败',
        description: (error as Error).message,
        variant: 'destructive',
      })
    } finally {
      setSaving(false)
    }
  }
  // 保留给后续恢复重启入口或快捷操作复用；当前页面不渲染重启按钮。
  void handleSaveAndRestart

  // 根据 schema 构建 tab 分组
  const tabGroups = useMemo(() => {
    if (!configSchema) return []
    return buildTabGroupsFromSchema(configSchema)
  }, [configSchema])

  const setSectionValue = useCallback((sectionName: string, value: ConfigSectionData) => {
    setSectionValues((current) => ({
      ...current,
      [sectionName]: value,
    }))
    triggerAutoSave(sectionName, value)
  }, [triggerAutoSave])

  if (loading) {
    return (
      <ScrollArea className="h-full">
        <div className="space-y-4 p-4 sm:space-y-6 sm:p-6">
          <div className="flex h-64 items-center justify-center">
            <ThinkingIllustration size="lg" />
          </div>
        </div>
      </ScrollArea>
    )
  }

  return (
    <ScrollArea className="h-full min-w-0" scrollbars="vertical">
      <div className="max-w-full space-y-4 overflow-x-hidden p-4 sm:space-y-6 sm:p-6">
        {/* 页面标题 */}
        <div className="flex flex-col gap-3 sm:gap-4">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="min-w-0">
              <h1 className="text-xl font-bold sm:text-2xl md:text-3xl">麦麦设置</h1>
            </div>
            {/* 按钮组 - 桌面端靠右 */}
            <div className="flex w-full min-w-0 flex-wrap gap-2 sm:w-auto sm:flex-shrink-0 sm:justify-end">
              <Tabs
                value={editMode}
                onValueChange={(v) => handleModeChange(v as 'visual' | 'source')}
                className="w-full min-w-0 sm:w-[14rem]"
              >
                <TabsList data-config-bot-mode-tabs="true" className="grid h-9 w-full grid-cols-2">
                  <TabsTrigger value="visual" className="px-2 text-sm">
                    <Layout className="mr-1 h-4 w-4" />
                    可视化
                  </TabsTrigger>
                  <TabsTrigger value="source" className="px-2 text-sm">
                    <Code2 className="mr-1 h-4 w-4" />
                    文件
                  </TabsTrigger>
                </TabsList>
              </Tabs>
              <Button
                onClick={handleReloadFromFile}
                disabled={saving || autoSaving || isRestarting}
                size="sm"
                variant="outline"
                className="h-9 w-9 flex-none px-0"
                aria-label="刷新"
                title="刷新"
              >
                <RefreshCw className="h-4 w-4" />
              </Button>
              <Button
                onClick={editMode === 'visual' ? saveConfig : saveSourceCode}
                disabled={saving || autoSaving || !hasUnsavedChanges || isRestarting}
                size="sm"
                variant="outline"
                className="h-9 w-9 flex-none px-0"
                aria-label={
                  saving
                    ? '保存中'
                    : autoSaving
                      ? '自动保存中'
                      : hasUnsavedChanges
                        ? '保存'
                        : '已保存'
                }
                title={
                  saving
                    ? '保存中'
                    : autoSaving
                      ? '自动保存中'
                      : hasUnsavedChanges
                        ? '保存'
                        : '已保存'
                }
              >
                <span className="relative inline-flex h-4 w-4 items-center justify-center">
                  <Save className="h-4 w-4" strokeWidth={2} fill="none" />
                  {!saving && !autoSaving && !hasUnsavedChanges && (
                    <Check
                      className="pointer-events-none absolute -right-2 -bottom-2 !h-3 !w-3"
                      strokeWidth={3.4}
                    />
                  )}
                </span>
              </Button>
            </div>
          </div>
        </div>

        {/* 源代码模式 */}
        {editMode === 'source' && (
          <div className="space-y-4">
            {(showFileModeNotice || (hasTomlError && tomlErrorMessage)) && (
              <Alert className={showFileModeNotice ? 'pr-10' : undefined}>
                <Info className="h-4 w-4" />
                {showFileModeNotice && (
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="absolute top-2 right-2 h-6 w-6 px-0"
                    aria-label="关闭文件模式提示"
                    title="关闭文件模式提示"
                    onClick={dismissFileModeNotice}
                  >
                    <X className="h-3.5 w-3.5" />
                  </Button>
                )}
                <AlertDescription>
                  {showFileModeNotice && (
                    <>
                      <strong>文件模式：</strong>直接编辑原始配置文件。此功能仅适用于熟悉 TOML
                      语法的用户。只有格式完全正确才能保存。
                    </>
                  )}
                  {hasTomlError && tomlErrorMessage && (
                    <div className="text-destructive bg-destructive/10 mt-3 rounded-md p-3 font-semibold">
                      <div className="mb-2 font-bold">⚠️ TOML 格式错误：</div>
                      <pre className="font-mono text-sm break-words whitespace-pre-wrap">
                        {tomlErrorMessage}
                      </pre>
                    </div>
                  )}
                </AlertDescription>
              </Alert>
            )}

            <CodeEditor
              value={sourceCode}
              onChange={(value) => {
                setSourceCode(value)
                setHasUnsavedChanges(true)
                // 清除之前的错误状态
                if (hasTomlError) {
                  setHasTomlError(false)
                  setTomlErrorMessage('')
                }
              }}
              language="toml"
              height="calc(100vh - 280px)"
              minHeight="500px"
              placeholder="TOML 配置内容"
            />
          </div>
        )}

        {/* 可视化模式 */}
        {editMode === 'visual' && (
          <DynamicConfigTabs
            configSchema={configSchema}
            tabGroups={tabGroups}
            sectionValues={sectionValues}
            setSectionValue={setSectionValue}
            setHasUnsavedChanges={setHasUnsavedChanges}
          />
        )}

        {/* 重启遮罩层 */}
        <RestartOverlay />
      </div>
    </ScrollArea>
  )
}

// ==================== 动态 Tab 渲染组件 ====================

function updateNestedValue(
  target: ConfigSectionData | null | undefined,
  pathSegments: string[],
  value: unknown
): ConfigSectionData {
  const currentTarget = target && typeof target === 'object' && !Array.isArray(target) ? target : {}
  const [currentPath, ...restPath] = pathSegments

  if (!currentPath) {
    return currentTarget
  }

  if (restPath.length === 0) {
    return {
      ...currentTarget,
      [currentPath]: value,
    }
  }

  return {
    ...currentTarget,
    [currentPath]: updateNestedValue(
      currentTarget[currentPath] as ConfigSectionData | undefined,
      restPath,
      value
    ),
  }
}

interface DynamicConfigTabsProps {
  configSchema: ConfigSchema | null
  tabGroups: TabGroup[]
  sectionValues: Record<string, ConfigSectionData | null>
  setSectionValue: (sectionName: string, value: ConfigSectionData) => void
  setHasUnsavedChanges: (v: boolean) => void
}

function DynamicConfigTabs(props: DynamicConfigTabsProps) {
  const { configSchema, sectionValues, setHasUnsavedChanges, setSectionValue, tabGroups } = props
  const [expanded, setExpanded] = useState(false)
  const [activeTab, setActiveTab] = useState(tabGroups[0]?.id ?? '')
  const [expandedSubtabGroups, setExpandedSubtabGroups] = useState<Record<string, boolean>>({})
  const [activeSubtabByGroup, setActiveSubtabByGroup] = useState<Record<string, string>>({})
  const [advancedVisible, setAdvancedVisible] = useState(false)
  const [tabGuideVisible, setTabGuideVisible] = useState(
    () => localStorage.getItem('bot-config-tabs-guide-dismissed') !== 'true'
  )

  useEffect(() => {
    if (!tabGroups.some((tab) => tab.id === activeTab)) {
      setActiveTab(tabGroups[0]?.id ?? '')
    }
  }, [activeTab, tabGroups])

  if (tabGroups.length === 0 || !configSchema?.nested) {
    return null
  }

  const defaultTabGroups = tabGroups.filter((tab) => !tab.advanced)
  const expandedTabGroups = tabGroups.filter((tab) => tab.advanced)
  const visibleTabGroups = expanded ? [...defaultTabGroups, ...expandedTabGroups] : defaultTabGroups
  const hasCollapsibleTabs = tabGroups.some((tab) => tab.advanced)
  const firstExpandedTabId = visibleTabGroups.find((tab) => tab.advanced)?.id

  const toggleExpanded = () => {
    setExpanded((current) => {
      if (current && tabGroups.find((tab) => tab.id === activeTab)?.advanced) {
        const firstDefaultTab = tabGroups.find((tab) => !tab.advanced)
        setActiveTab(firstDefaultTab?.id ?? tabGroups[0]?.id ?? '')
      }
      return !current
    })
  }

  const dismissTabGuide = () => {
    localStorage.setItem('bot-config-tabs-guide-dismissed', 'true')
    setTabGuideVisible(false)
  }

  const updateSectionValueByPath = (sectionName: string, restPath: string[], value: unknown) => {
    const currentSectionValue = sectionValues[sectionName] ?? {}
    const nextSectionValue =
      restPath.length === 0
        ? (value as ConfigSectionData)
        : updateNestedValue(currentSectionValue, restPath, value)

    setSectionValue(sectionName, nextSectionValue)
    setHasUnsavedChanges(true)
  }

  const getSubtabLabel = (schema: ConfigSchema, fallback: string) => {
    return schema.uiSubLabel || schema.uiLabel || schema.classDoc || fallback
  }

  const getObjectSubcategoryEntries = (sectionSchema: ConfigSchema) => {
    const sectionFieldByName = new Map(sectionSchema.fields.map((field) => [field.name, field]))
    return Object.entries(sectionSchema.nested ?? {}).filter(([subcategoryName]) => {
      return sectionFieldByName.get(subcategoryName)?.type === 'object'
    })
  }

  const renderSubtabbedContent = (tabId: string, tabNestedEntries: readonly (readonly [string, ConfigSchema])[]) => {
    const subtabPanes: SubtabPane[] = []
    const chatManagementHintPaneIds = new Set(['chat.reply_timing', 'chat.reply_style'])
    const entryMap = new Map<string, ConfigSchema>(
      tabNestedEntries.map(([sectionName, schema]) => [sectionName, schema]),
    )
    const childSectionsByParent = new Map<string, Array<readonly [string, ConfigSchema]>>()

    for (const [sectionName, sectionSchema] of tabNestedEntries) {
      const parentName = sectionSchema.uiParent
      if (!parentName || !entryMap.has(parentName)) {
        continue
      }

      const childSections = childSectionsByParent.get(parentName) ?? []
      childSections.push([sectionName, sectionSchema])
      childSectionsByParent.set(parentName, childSections)
    }

    const collectDescendantEntries = (parentName: string): Array<readonly [string, ConfigSchema]> => {
      const directChildEntries = childSectionsByParent.get(parentName) ?? []

      return directChildEntries.flatMap(([childName, childSchema]) => {
        return [[childName, childSchema] as const, ...collectDescendantEntries(childName)]
      })
    }

    const renderSectionGroupContent = (sectionEntries: Array<readonly [string, ConfigSchema]>) => {
      const values = Object.fromEntries(
        sectionEntries.map(([sectionName]) => [sectionName, sectionValues[sectionName] ?? {}])
      )
      const groupSchema: ConfigSchema = {
        className: sectionEntries.map(([sectionName]) => sectionName).join('.'),
        classDoc: sectionEntries[0]?.[1].uiLabel || sectionEntries[0]?.[1].classDoc || '',
        fields: [],
        nested: Object.fromEntries(sectionEntries),
      }

      return (
        <DynamicConfigForm
          schema={groupSchema}
          values={values}
          onChange={(fieldPath, value) => {
            const [sectionName, ...restPath] = fieldPath.split('.')
            if (!sectionName) {
              return
            }

            updateSectionValueByPath(sectionName, restPath, value)
          }}
          hooks={fieldHooks}
          advancedVisible={advancedVisible}
          sectionColumns={2}
        />
      )
    }

    for (const [sectionName, sectionSchema] of tabNestedEntries) {
      if (sectionSchema.uiParent && entryMap.has(sectionSchema.uiParent)) {
        continue
      }

      const sectionValue = (sectionValues[sectionName] ?? {}) as ConfigSectionData
      const allSubcategoryEntries = sectionSchema.uiUseSubTabs ? getObjectSubcategoryEntries(sectionSchema) : []
      const subcategoryEntries = allSubcategoryEntries
      const subcategoryNames = new Set(allSubcategoryEntries.map(([subcategoryName]) => subcategoryName))
      const rootFields = sectionSchema.fields.filter((field) => !subcategoryNames.has(field.name))
      const rootSchema: ConfigSchema = {
        ...sectionSchema,
        className: `${sectionSchema.className}Root`,
        fields: rootFields,
        nested: {},
      }

      if (rootFields.length > 0) {
        subtabPanes.push({
          advanced: Boolean(sectionSchema.uiAdvanced),
          id: sectionName,
          label: sectionSchema.uiRootSubLabel || getSubtabLabel(sectionSchema, sectionName),
          content: (
            <DynamicConfigForm
              schema={rootSchema}
              values={sectionValue}
              onChange={(fieldPath, value) => updateSectionValueByPath(sectionName, fieldPath.split('.'), value)}
              basePath={sectionName}
              hooks={fieldHooks}
              advancedVisible={advancedVisible}
              sectionColumns={1}
            />
          ),
        })
      }

      for (const [subcategoryName, subcategorySchema] of subcategoryEntries) {
        subtabPanes.push({
          advanced: Boolean(subcategorySchema.uiAdvanced),
          id: `${sectionName}.${subcategoryName}`,
          label: getSubtabLabel(subcategorySchema, subcategoryName),
          content: (
            <DynamicConfigForm
              schema={subcategorySchema}
              values={(sectionValue[subcategoryName] as Record<string, unknown>) || {}}
              onChange={(fieldPath, value) =>
                updateSectionValueByPath(sectionName, [subcategoryName, ...fieldPath.split('.')], value)
              }
              basePath={`${sectionName}.${subcategoryName}`}
              hooks={fieldHooks}
              advancedVisible={advancedVisible}
              sectionColumns={1}
            />
          ),
        })
      }

      for (const [childName, childSchema] of childSectionsByParent.get(sectionName) ?? []) {
        const sectionGroupEntries = [[childName, childSchema] as const, ...collectDescendantEntries(childName)]
        subtabPanes.push({
          advanced: Boolean(childSchema.uiAdvanced),
          id: childName,
          label: getSubtabLabel(childSchema, childName),
          content: renderSectionGroupContent(sectionGroupEntries),
        })
      }
    }

    if (subtabPanes.length === 0) {
      return null
    }

    const subtabExpanded = Boolean(expandedSubtabGroups[tabId])
    const defaultSubtabPanes = subtabPanes.filter((pane) => !pane.advanced)
    const expandedSubtabPanes = subtabPanes.filter((pane) => pane.advanced)
    const visibleSubtabPanes = subtabExpanded ? [...defaultSubtabPanes, ...expandedSubtabPanes] : defaultSubtabPanes
    const hasCollapsibleSubtabs = subtabPanes.some((pane) => pane.advanced)
    const firstExpandedSubtabId = visibleSubtabPanes.find((pane) => pane.advanced)?.id
    const visibleSubtabIds = new Set(visibleSubtabPanes.map((pane) => pane.id))
    const activeSubtab = activeSubtabByGroup[tabId]
    const resolvedActiveSubtab = visibleSubtabIds.has(activeSubtab)
      ? activeSubtab
      : visibleSubtabPanes[0]?.id ?? subtabPanes[0].id

    const toggleSubtabsExpanded = () => {
      if (subtabExpanded && subtabPanes.find((pane) => pane.id === resolvedActiveSubtab)?.advanced) {
        setActiveSubtabByGroup((current) => ({
          ...current,
          [tabId]: defaultSubtabPanes[0]?.id ?? subtabPanes[0].id,
        }))
      }

      setExpandedSubtabGroups((current) => ({
        ...current,
        [tabId]: !subtabExpanded,
      }))
    }

    return (
      <Tabs
        key={subtabPanes.map((pane) => pane.id).join('|')}
        value={resolvedActiveSubtab}
        onValueChange={(value) =>
          setActiveSubtabByGroup((current) => ({
            ...current,
            [tabId]: value,
          }))
        }
        className="space-y-3"
      >
        <DashboardTabBar data-config-bot-subtab-list="true" variant="scroll" className="bg-background/80 h-11 border">
          {visibleSubtabPanes.map((pane) => (
            <Fragment key={pane.id}>
              {pane.id === firstExpandedSubtabId && (
                <span className="bg-border/90 mx-1 hidden h-7 w-[2px] transition-opacity duration-200 sm:block" />
              )}
              <DashboardTabTrigger
                value={pane.id}
                data-config-bot-extra-tab={pane.advanced ? 'true' : undefined}
                className={cn(
                  'min-h-8 text-base font-semibold',
                  pane.advanced &&
                    'text-muted-foreground/80 decoration-border/80 hover:bg-background/70 data-[state=active]:bg-primary/10 data-[state=active]:text-primary underline decoration-dashed underline-offset-4 data-[state=active]:shadow-none motion-safe:animate-[config-tab-enter_180ms_ease-out_both]'
                )}
              >
                {pane.label}
              </DashboardTabTrigger>
            </Fragment>
          ))}
          {hasCollapsibleSubtabs && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="group h-8 shrink-0 gap-1 self-center px-2 text-sm leading-none transition-all duration-200 ease-out sm:px-2.5"
              onClick={toggleSubtabsExpanded}
            >
              {subtabExpanded ? (
                <ChevronLeft className="h-3.5 w-3.5 transition-transform duration-200 group-hover:-translate-x-0.5" />
              ) : (
                <ChevronRight className="h-3.5 w-3.5 transition-transform duration-200 group-hover:translate-x-0.5" />
              )}
              {subtabExpanded ? '收起' : '更多'}
            </Button>
          )}
        </DashboardTabBar>

        {visibleSubtabPanes.map((pane) => (
          <TabsContent key={pane.id} value={pane.id} className="mt-0">
            {chatManagementHintPaneIds.has(pane.id) && (
              <div className="mb-3 flex flex-col gap-2 rounded-md border bg-muted/20 px-3 py-2 text-sm text-muted-foreground sm:flex-row sm:items-center sm:justify-between">
                <span>需要按具体聊天流调整发言频率或查看聊天 Prompt 时，可以前往聊天管理。</span>
                <Button asChild size="sm" variant="outline" className="h-8 shrink-0 self-start sm:self-center">
                  <Link to="/chat-management">
                    <ExternalLink className="mr-2 h-3.5 w-3.5" />
                    聊天管理
                  </Link>
                </Button>
              </div>
            )}
            {pane.content}
          </TabsContent>
        ))}
      </Tabs>
    )
  }

  const renderTabContent = (tab: TabGroup) => {
    const tabNestedEntries = tab.sections
      .map((sectionName) => [sectionName, configSchema.nested?.[sectionName]] as const)
      .filter((entry): entry is readonly [string, ConfigSchema] => Boolean(entry[1]))

    if (tabNestedEntries.length === 0) {
      return null
    }

    if (tabNestedEntries.some(([, sectionSchema]) => sectionSchema.uiUseSubTabs)) {
      return renderSubtabbedContent(tab.id, tabNestedEntries)
    }

    const values = Object.fromEntries(
      tabNestedEntries.map(([sectionName]) => [sectionName, sectionValues[sectionName] ?? {}])
    )

    const tabSchema: ConfigSchema = {
      className: tab.id,
      classDoc: tab.label,
      fields: [],
      nested: Object.fromEntries(tabNestedEntries),
    }

    return (
      <DynamicConfigForm
        schema={tabSchema}
        values={values}
        onChange={(fieldPath, value) => {
          const [sectionName, ...restPath] = fieldPath.split('.')
          if (!sectionName) {
            return
          }

          updateSectionValueByPath(sectionName, restPath, value)
        }}
        hooks={fieldHooks}
        advancedVisible={advancedVisible}
        sectionColumns={2}
      />
    )
  }

  return (
    <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full">
      <DashboardTabBar
        data-config-bot-tab-list="true"
        className="h-auto min-h-[3.25rem] content-start items-stretch sm:flex-wrap"
      >
        {visibleTabGroups.map((tab) => {
          const isExpandedOnlyTab = tab.advanced
          return (
            <Fragment key={tab.id}>
              {tab.id === firstExpandedTabId && (
                <span className="bg-border/90 mx-1 hidden h-7 w-[2px] transition-opacity duration-200 sm:block" />
              )}
              <DashboardTabTrigger
                value={tab.id}
                data-config-bot-extra-tab={isExpandedOnlyTab ? 'true' : undefined}
                className={cn(
                  'min-h-9 text-lg font-semibold',
                  isExpandedOnlyTab &&
                    'text-muted-foreground/80 decoration-border/80 hover:bg-background/70 data-[state=active]:bg-primary/10 data-[state=active]:text-primary underline decoration-dashed underline-offset-4 data-[state=active]:shadow-none motion-safe:animate-[config-tab-enter_180ms_ease-out_both]'
                )}
              >
                {tab.label}
              </DashboardTabTrigger>
            </Fragment>
          )
        })}
        {hasCollapsibleTabs && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="group h-9 shrink-0 gap-1 self-center px-2 text-sm leading-none transition-all duration-200 ease-out sm:px-2.5"
            onClick={toggleExpanded}
          >
            {expanded ? (
              <ChevronLeft className="h-3.5 w-3.5 transition-transform duration-200 group-hover:-translate-x-0.5" />
            ) : (
              <ChevronRight className="h-3.5 w-3.5 transition-transform duration-200 group-hover:translate-x-0.5" />
            )}
            {expanded ? '收起' : '更多'}
          </Button>
        )}
        <Button
          type="button"
          variant={advancedVisible ? 'default' : 'outline'}
          size="sm"
          className="h-9 shrink-0 self-center px-2.5 text-sm leading-none transition-all duration-200 ease-out sm:ml-auto"
          onClick={() => setAdvancedVisible((current) => !current)}
        >
          高级设置
        </Button>
      </DashboardTabBar>
      {tabGuideVisible && (
        <div className="bg-muted/20 text-muted-foreground mt-2 flex flex-col gap-2 rounded-md border px-3 py-2 text-xs sm:flex-row sm:items-center sm:justify-between">
          <span>点击“更多”展开隐藏配置栏目；点击“高级设置”显示高级配置项。</span>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-6 self-start px-2 text-xs sm:self-center"
            onClick={dismissTabGuide}
          >
            我知道了
          </Button>
        </div>
      )}
      {tabGroups.map((tab) => (
        <TabsContent
          key={tab.id}
          value={tab.id}
          className="space-y-4 motion-safe:animate-[config-tab-content-enter_180ms_ease-out_both]"
        >
          {renderTabContent(tab)}
        </TabsContent>
      ))}
    </Tabs>
  )
}
