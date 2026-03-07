import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { parse as parseToml } from 'smol-toml'

import { AlertDescription, Alert } from '@/components/ui/alert'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { CodeEditor } from '@/components'
import { DynamicConfigForm } from '@/components/dynamic-form'
import { RestartOverlay } from '@/components/restart-overlay'
import { useToast } from '@/hooks/use-toast'
import { getBotConfig, getBotConfigRaw, getBotConfigSchema, updateBotConfig, updateBotConfigRaw } from '@/lib/config-api'
import { fieldHooks } from '@/lib/field-hooks'
import { RestartProvider, useRestart } from '@/lib/restart-context'

import { Code2, Info, Layout, Power, Save } from 'lucide-react'

import type { ConfigSchema } from '@/types/config-schema'
import type {
  BotConfig,
  ChatConfig,
  ChineseTypoConfig,
  DebugConfig,
  DreamConfig,
  EmojiConfig,
  ExperimentalConfig,
  ExpressionConfig,
  KeywordReactionConfig,
  LogConfig,
  LPMMKnowledgeConfig,
  MaimMessageConfig,
  MemoryConfig,
  MessageReceiveConfig,
  PersonalityConfig,
  ResponsePostProcessConfig,
  ResponseSplitterConfig,
  TelemetryConfig,
  ToolConfig,
  VoiceConfig,
  WebUIConfig,
} from './bot/types'
import { useAutoSave, useConfigAutoSave } from './bot/hooks'
import { ChatSectionHook } from './bot/hooks'
import {
  BotInfoSection,
  DebugSection,
  DreamSection,
  ExperimentalSection,
  ExpressionSection,
  FeaturesSection,
  LogSection,
  LPMMSection,
  MaimMessageSection,
  MessageReceiveSection,
  PersonalitySection,
  ProcessingSection,
  TelemetrySection,
  WebUISection,
} from './bot/sections'
// ==================== 常量定义 ====================
/** Toast 显示前的延迟时间 (毫秒) */
const TOAST_DISPLAY_DELAY = 500

/** Tab 标签页的首选排列顺序 (host field name) */
const TAB_ORDER = [
  'bot', 'personality', 'chat', 'expression', 'emoji',
  'response_post_process', 'dream', 'lpmm_knowledge', 'webui', 'debug',
]

// ==================== Tab 分组类型与构建 ====================
interface TabGroup {
  id: string
  label: string
  icon: string
  sections: string[]
}

/**
 * 从 schema 的 nested 字段解析出 tab 分组信息。
 * - 有 uiLabel 且无 uiParent → 独立 tab (host)
 * - 有 uiParent → 归入对应 host tab 的 sections
 */
function buildTabGroupsFromSchema(schema: ConfigSchema): TabGroup[] {
  const nested = schema.nested || {}
  const hosts = new Map<string, TabGroup>()
  const children: Array<{ fieldName: string; parentId: string }> = []

  for (const [fieldName, fieldSchema] of Object.entries(nested)) {
    if (fieldSchema.uiLabel && !fieldSchema.uiParent) {
      hosts.set(fieldName, {
        id: fieldName,
        label: fieldSchema.uiLabel,
        icon: fieldSchema.uiIcon || '',
        sections: [fieldName],
      })
    } else if (fieldSchema.uiParent) {
      children.push({ fieldName, parentId: fieldSchema.uiParent })
    }
  }

  for (const { fieldName, parentId } of children) {
    const parent = hosts.get(parentId)
    if (parent) {
      parent.sections.push(fieldName)
    }
  }

  // 按 TAB_ORDER 排序；未列入的 tab 追加到末尾
  return Array.from(hosts.values()).sort((a, b) => {
    const ai = TAB_ORDER.indexOf(a.id)
    const bi = TAB_ORDER.indexOf(b.id)
    return (ai === -1 ? Infinity : ai) - (bi === -1 ? Infinity : bi)
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
  const { toast } = useToast()
  const { triggerRestart, isRestarting } = useRestart()

  // 配置状态
  const [botConfig, setBotConfig] = useState<BotConfig | null>(null)
  const [personalityConfig, setPersonalityConfig] = useState<PersonalityConfig | null>(null)
  const [chatConfig, setChatConfig] = useState<ChatConfig | null>(null)
  const [expressionConfig, setExpressionConfig] = useState<ExpressionConfig | null>(null)
  const [emojiConfig, setEmojiConfig] = useState<EmojiConfig | null>(null)
  const [memoryConfig, setMemoryConfig] = useState<MemoryConfig | null>(null)
  const [toolConfig, setToolConfig] = useState<ToolConfig | null>(null)
  const [voiceConfig, setVoiceConfig] = useState<VoiceConfig | null>(null)
  const [messageReceiveConfig, setMessageReceiveConfig] = useState<MessageReceiveConfig | null>(null)
  const [dreamConfig, setDreamConfig] = useState<DreamConfig | null>(null)
  const [lpmmConfig, setLpmmConfig] = useState<LPMMKnowledgeConfig | null>(null)
  const [keywordReactionConfig, setKeywordReactionConfig] = useState<KeywordReactionConfig | null>(null)
  const [responsePostProcessConfig, setResponsePostProcessConfig] = useState<ResponsePostProcessConfig | null>(null)
  const [chineseTypoConfig, setChineseTypoConfig] = useState<ChineseTypoConfig | null>(null)
  const [responseSplitterConfig, setResponseSplitterConfig] = useState<ResponseSplitterConfig | null>(null)
  const [logConfig, setLogConfig] = useState<LogConfig | null>(null)
  const [debugConfig, setDebugConfig] = useState<DebugConfig | null>(null)
  const [experimentalConfig, setExperimentalConfig] = useState<ExperimentalConfig | null>(null)
  const [maimMessageConfig, setMaimMessageConfig] = useState<MaimMessageConfig | null>(null)
  const [telemetryConfig, setTelemetryConfig] = useState<TelemetryConfig | null>(null)
  const [webuiConfig, setWebuiConfig] = useState<WebUIConfig | null>(null)

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
      [/Invalid TOML document: unrecognized escape sequence/, 'TOML 文档错误：无法识别的转义序列（提示：在双引号字符串中使用 \\\\ 转义反斜杠，或使用单引号字符串）'],
      [/Invalid TOML document: only letter, numbers, dashes and underscores are allowed in keys/, 'TOML 文档错误：键名只能包含字母、数字、短横线和下划线'],
      [/Invalid TOML document: (.+)/, 'TOML 文档错误：$1'],
      
      // 位置错误系列
      [/Unexpected character.*at line (\d+), column (\d+)/, '第 $1 行第 $2 列：意外的字符'],
      [/Expected.*at line (\d+), column (\d+)/, '第 $1 行第 $2 列：缺少必要的字符'],
      [/Invalid.*at line (\d+), column (\d+)/, '第 $1 行第 $2 列：无效的语法'],
      [/Unterminated string at line (\d+)/, '第 $1 行：字符串未正常结束（缺少引号）'],
      [/Duplicate key.*at line (\d+)/, '第 $1 行：重复的键名'],
      [/Invalid escape sequence at line (\d+)/, '第 $1 行：无效的转义序列（提示：在双引号字符串中使用 \\\\ 转义反斜杠）'],
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
    configRef.current = config

    setBotConfig(config.bot as BotConfig)
    setPersonalityConfig(config.personality as PersonalityConfig)
    
    // 确保 chat 配置和 talk_value_rules 有默认值
    const chatConfigData = (config.chat ?? {}) as ChatConfig
    if (!chatConfigData.talk_value_rules) {
      chatConfigData.talk_value_rules = []
    }
    setChatConfig(chatConfigData)
    
    setExpressionConfig(config.expression as ExpressionConfig)
    setEmojiConfig(config.emoji as EmojiConfig)
    setMemoryConfig(config.memory as MemoryConfig)
    setToolConfig(config.tool as ToolConfig)
    setVoiceConfig(config.voice as VoiceConfig)
    setMessageReceiveConfig(config.message_receive as MessageReceiveConfig)
    setDreamConfig(config.dream as DreamConfig)
    setLpmmConfig(config.lpmm_knowledge as LPMMKnowledgeConfig)
    setKeywordReactionConfig(config.keyword_reaction as KeywordReactionConfig)
    setResponsePostProcessConfig(config.response_post_process as ResponsePostProcessConfig)
    setChineseTypoConfig(config.chinese_typo as ChineseTypoConfig)
    setResponseSplitterConfig(config.response_splitter as ResponseSplitterConfig)
    setLogConfig(config.log as LogConfig)
    setDebugConfig(config.debug as DebugConfig)
    setExperimentalConfig(config.experimental as ExperimentalConfig)
    setMaimMessageConfig(config.maim_message as MaimMessageConfig)
    setTelemetryConfig(config.telemetry as TelemetryConfig)
    setWebuiConfig(config.webui as WebUIConfig)
  }, [])

  /**
   * 构建完整的配置对象用于保存
   * 抽取自 saveConfig 和 handleSaveAndRestart 中的重复逻辑
   */
  const buildFullConfig = useCallback(() => {
    return {
      ...configRef.current,
      bot: botConfig,
      personality: personalityConfig,
      chat: chatConfig,
      expression: expressionConfig,
      emoji: emojiConfig,
      memory: memoryConfig,
      tool: toolConfig,
      voice: voiceConfig,
      message_receive: messageReceiveConfig,
      dream: dreamConfig,
      lpmm_knowledge: lpmmConfig,
      keyword_reaction: keywordReactionConfig,
      response_post_process: responsePostProcessConfig,
      chinese_typo: chineseTypoConfig,
      response_splitter: responseSplitterConfig,
      log: logConfig,
      debug: debugConfig,
      experimental: experimentalConfig,
      maim_message: maimMessageConfig,
      telemetry: telemetryConfig,
      webui: webuiConfig,
    }
  }, [
    botConfig, personalityConfig, chatConfig, expressionConfig,
    emojiConfig, memoryConfig, toolConfig,
    voiceConfig, messageReceiveConfig, dreamConfig, lpmmConfig, keywordReactionConfig, responsePostProcessConfig,
    chineseTypoConfig, responseSplitterConfig, logConfig, debugConfig, experimentalConfig,
    maimMessageConfig, telemetryConfig, webuiConfig
  ])

  // 加载源代码
  const loadSourceCode = useCallback(async () => {
    try {
      const result = await getBotConfigRaw()
      if (!result.success) {
        toast({
          variant: 'destructive',
          title: '加载失败',
          description: result.error,
        })
        return
      }
      const raw = (result.data as unknown as Record<string, unknown>).content as string
      // 将 TOML 基本字符串中的转义序列转换为实际字符以便在编辑器中正确显示
      // 使用正则表达式只处理双引号字符串内的转义序列，不影响单引号字符串
      const unescaped = raw.replace(/"([^"]*)"/g, (_match, content) => {
        const decoded = content
          .replace(/\\n/g, '\n')  // 换行符
          .replace(/\\t/g, '\t')  // 制表符
          .replace(/\\r/g, '\r')  // 回车符
          .replace(/\\"/g, '"')   // 双引号
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
      const [result, schemaResult] = await Promise.all([getBotConfig(), getBotConfigSchema()])
      if (!result.success) {
        toast({
          title: '加载失败',
          description: result.error,
          variant: 'destructive',
        })
        setLoading(false)
        return
      }
      parseAndSetConfig((result.data as Record<string, unknown>).config as Record<string, unknown>)
      if (schemaResult.success && schemaResult.data) {
        setConfigSchema((schemaResult.data as unknown as Record<string, unknown>).schema as ConfigSchema)
      }
      setHasUnsavedChanges(false)
      initialLoadRef.current = false
      
      // 同时加载源代码
      await loadSourceCode()
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
  }, [toast, loadSourceCode, parseAndSetConfig])

  useEffect(() => {
    loadConfig()
  }, [loadConfig])

  useEffect(() => {
    fieldHooks.register('chat', ChatSectionHook, 'replace')
    return () => {
      fieldHooks.unregister('chat')
    }
  }, [])

  // 使用模块化的 useAutoSave hook
  const { triggerAutoSave, cancelPendingAutoSave } = useAutoSave(
    initialLoadRef.current,
    setAutoSaving,
    setHasUnsavedChanges
  )

  // 使用 useConfigAutoSave hook 简化配置变化监听
  // 注意: useConfigAutoSave 是一个 hook，不能在条件语句或循环中调用
  // 因此我们仍然需要逐个调用，但代码更简洁
  useConfigAutoSave(botConfig, 'bot', initialLoadRef.current, triggerAutoSave)
  useConfigAutoSave(personalityConfig, 'personality', initialLoadRef.current, triggerAutoSave)
  useConfigAutoSave(chatConfig, 'chat', initialLoadRef.current, triggerAutoSave)
  useConfigAutoSave(expressionConfig, 'expression', initialLoadRef.current, triggerAutoSave)
  useConfigAutoSave(emojiConfig, 'emoji', initialLoadRef.current, triggerAutoSave)
  useConfigAutoSave(memoryConfig, 'memory', initialLoadRef.current, triggerAutoSave)
  useConfigAutoSave(toolConfig, 'tool', initialLoadRef.current, triggerAutoSave)
  useConfigAutoSave(voiceConfig, 'voice', initialLoadRef.current, triggerAutoSave)
  useConfigAutoSave(dreamConfig, 'dream', initialLoadRef.current, triggerAutoSave)
  useConfigAutoSave(lpmmConfig, 'lpmm_knowledge', initialLoadRef.current, triggerAutoSave)
  useConfigAutoSave(keywordReactionConfig, 'keyword_reaction', initialLoadRef.current, triggerAutoSave)
  useConfigAutoSave(responsePostProcessConfig, 'response_post_process', initialLoadRef.current, triggerAutoSave)
  useConfigAutoSave(chineseTypoConfig, 'chinese_typo', initialLoadRef.current, triggerAutoSave)
  useConfigAutoSave(responseSplitterConfig, 'response_splitter', initialLoadRef.current, triggerAutoSave)
  useConfigAutoSave(logConfig, 'log', initialLoadRef.current, triggerAutoSave)
  useConfigAutoSave(debugConfig, 'debug', initialLoadRef.current, triggerAutoSave)
  useConfigAutoSave(maimMessageConfig, 'maim_message', initialLoadRef.current, triggerAutoSave)
  useConfigAutoSave(telemetryConfig, 'telemetry', initialLoadRef.current, triggerAutoSave)
  useConfigAutoSave(webuiConfig, 'webui', initialLoadRef.current, triggerAutoSave)

  // 保存源代码
  const saveSourceCode = async () => {
    try {
      setSaving(true)
      
      // 前端验证 TOML 格式
      try {
        parseToml(sourceCode)
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
      
      // 将双引号字符串中的实际字符转换回 TOML 转义序列
      // 使用正则表达式只处理双引号字符串内的内容，不影响单引号字符串
      const escaped = sourceCode.replace(/"([^"]*)"/g, (_match, content) => {
        const encoded = content
          .replace(/\\/g, '\\\\') // 反斜杠（必须放在最前）
          .replace(/"/g, '\\"')   // 双引号
          .replace(/\n/g, '\\n')  // 换行符
          .replace(/\t/g, '\\t')  // 制表符
          .replace(/\r/g, '\\r')  // 回车符
        return `"${encoded}"`
      })
      const result = await updateBotConfigRaw(escaped)
      if (!result.success) {
        setHasTomlError(true)
        const errorMsg = result.error
        setTomlErrorMessage(errorMsg)
        toast({
          variant: 'destructive',
          title: '保存失败',
          description: errorMsg,
        })
        return
      }
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
        if (!result.success) {
          toast({
            title: '加载失败',
            description: result.error,
            variant: 'destructive',
          })
          return
        }
        parseAndSetConfig((result.data as Record<string, unknown>).config as Record<string, unknown>)
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
      
      const result = await updateBotConfig(buildFullConfig())
      if (!result.success) {
        toast({
          title: '保存失败',
          description: result.error,
          variant: 'destructive',
        })
        setSaving(false)
        return
      }
      setHasUnsavedChanges(false)
      toast({
        title: '保存成功',
        description: '麦麦主程序配置已保存',
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
      
      const result = await updateBotConfig(buildFullConfig())
      if (!result.success) {
        toast({
          title: '保存失败',
          description: result.error,
          variant: 'destructive',
        })
        setSaving(false)
        return
      }
      setHasUnsavedChanges(false)
      toast({
        title: '保存成功',
        description: '配置已保存，即将重启麦麦...',
      })
      // 等待一下让用户看到保存成功的提示
      await new Promise(resolve => setTimeout(resolve, TOAST_DISPLAY_DELAY))
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

  // 根据 schema 构建 tab 分组
  const tabGroups = useMemo(() => {
    if (!configSchema) return []
    return buildTabGroupsFromSchema(configSchema)
  }, [configSchema])

  if (loading) {
    return (
      <ScrollArea className="h-full">
        <div className="space-y-4 sm:space-y-6 p-4 sm:p-6">
          <div className="flex items-center justify-center h-64">
            <p className="text-muted-foreground">加载中...</p>
          </div>
        </div>
      </ScrollArea>
    )
  }

  return (
    <ScrollArea className="h-full">
      <div className="space-y-4 sm:space-y-6 p-4 sm:p-6">
        {/* 页面标题 */}
        <div className="flex flex-col gap-3 sm:gap-4">
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
            <div className="min-w-0">
              <h1 className="text-xl sm:text-2xl md:text-3xl font-bold">麦麦主程序配置</h1>
              <p className="text-muted-foreground mt-1 text-xs sm:text-sm">管理麦麦的核心功能和行为设置</p>
            </div>
            {/* 按钮组 - 桌面端靠右 */}
            <div className="flex gap-2 flex-shrink-0">
              <Button
                onClick={editMode === 'visual' ? saveConfig : saveSourceCode}
                disabled={saving || autoSaving || !hasUnsavedChanges || isRestarting}
                size="sm"
                variant="outline"
                className="w-20 sm:w-24"
              >
                <Save className="h-4 w-4 flex-shrink-0" strokeWidth={2} fill="none" />
                <span className="ml-1 truncate text-xs sm:text-sm">
                  {saving ? '保存中' : autoSaving ? '自动' : hasUnsavedChanges ? '保存' : '已保存'}
                </span>
              </Button>
              <AlertDialog>
                <AlertDialogTrigger asChild>
                  <Button
                    disabled={saving || autoSaving || isRestarting}
                    size="sm"
                    className="w-20 sm:w-28"
                  >
                    <Power className="h-4 w-4 flex-shrink-0" />
                    <span className="ml-1 truncate text-xs sm:text-sm">
                      {isRestarting ? '重启中' : hasUnsavedChanges ? '保存重启' : '重启'}
                    </span>
                  </Button>
                </AlertDialogTrigger>
                <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>确认重启麦麦？</AlertDialogTitle>
                  <AlertDialogDescription asChild>
                    <div>
                      <p>
                        {hasUnsavedChanges 
                          ? '当前有未保存的配置更改。点击确认将先保存配置,然后重启麦麦使新配置生效。重启过程中麦麦将暂时离线。'
                          : '即将重启麦麦主程序。重启过程中麦麦将暂时离线,配置将在重启后生效。'
                        }
                      </p>
                    </div>
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>取消</AlertDialogCancel>
                  <AlertDialogAction onClick={hasUnsavedChanges ? handleSaveAndRestart : handleRestart}>
                    {hasUnsavedChanges ? '保存并重启' : '确认重启'}
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
            </div>
          </div>
          
          {/* 模式切换 - 单独一行 */}
          <div className="flex">
            <Tabs value={editMode} onValueChange={(v) => handleModeChange(v as 'visual' | 'source')} className="w-full">
              <TabsList className="h-8 sm:h-9 w-full grid grid-cols-2">
                <TabsTrigger value="visual" className="text-xs sm:text-sm">
                  <Layout className="h-3 w-3 sm:h-4 sm:w-4 mr-1" />
                  可视化编辑
                </TabsTrigger>
                <TabsTrigger value="source" className="text-xs sm:text-sm">
                  <Code2 className="h-3 w-3 sm:h-4 sm:w-4 mr-1" />
                  源代码编辑
                </TabsTrigger>
              </TabsList>
            </Tabs>
          </div>
        </div>

        {/* 重启提示 */}
        <Alert>
          <Info className="h-4 w-4" />
          <AlertDescription>
            配置更新后需要<strong>重启麦麦</strong>才能生效。你可以点击右上角的"保存并重启"按钮一键完成保存和重启。
          </AlertDescription>
        </Alert>

        {/* 源代码模式 */}
        {editMode === 'source' && (
          <div className="space-y-4">
            <Alert>
              <Info className="h-4 w-4" />
              <AlertDescription>
                <strong>源代码模式（高级功能）：</strong>直接编辑 TOML 配置文件。此功能仅适用于熟悉 TOML 语法的高级用户。保存时会在前端验证格式，只有格式完全正确才能保存。
                {hasTomlError && tomlErrorMessage && (
                  <div className="text-destructive font-semibold mt-3 p-3 bg-destructive/10 rounded-md">
                    <div className="font-bold mb-2">⚠️ TOML 格式错误：</div>
                    <pre className="text-sm font-mono whitespace-pre-wrap break-words">
                      {tomlErrorMessage}
                    </pre>
                  </div>
                )}
              </AlertDescription>
            </Alert>
            
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
            tabGroups={tabGroups}
            botConfig={botConfig} setBotConfig={setBotConfig}
            personalityConfig={personalityConfig} setPersonalityConfig={setPersonalityConfig}
            chatConfig={chatConfig} setChatConfig={setChatConfig}
            expressionConfig={expressionConfig} setExpressionConfig={setExpressionConfig}
            emojiConfig={emojiConfig} setEmojiConfig={setEmojiConfig}
            memoryConfig={memoryConfig} setMemoryConfig={setMemoryConfig}
            toolConfig={toolConfig} setToolConfig={setToolConfig}
            voiceConfig={voiceConfig} setVoiceConfig={setVoiceConfig}
            messageReceiveConfig={messageReceiveConfig} setMessageReceiveConfig={setMessageReceiveConfig}
            dreamConfig={dreamConfig} setDreamConfig={setDreamConfig}
            lpmmConfig={lpmmConfig} setLpmmConfig={setLpmmConfig}
            keywordReactionConfig={keywordReactionConfig} setKeywordReactionConfig={setKeywordReactionConfig}
            responsePostProcessConfig={responsePostProcessConfig} setResponsePostProcessConfig={setResponsePostProcessConfig}
            chineseTypoConfig={chineseTypoConfig} setChineseTypoConfig={setChineseTypoConfig}
            responseSplitterConfig={responseSplitterConfig} setResponseSplitterConfig={setResponseSplitterConfig}
            logConfig={logConfig} setLogConfig={setLogConfig}
            debugConfig={debugConfig} setDebugConfig={setDebugConfig}
            experimentalConfig={experimentalConfig} setExperimentalConfig={setExperimentalConfig}
            maimMessageConfig={maimMessageConfig} setMaimMessageConfig={setMaimMessageConfig}
            telemetryConfig={telemetryConfig} setTelemetryConfig={setTelemetryConfig}
            webuiConfig={webuiConfig} setWebuiConfig={setWebuiConfig}
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

interface DynamicConfigTabsProps {
  tabGroups: TabGroup[]
  botConfig: BotConfig | null
  setBotConfig: (c: BotConfig) => void
  personalityConfig: PersonalityConfig | null
  setPersonalityConfig: (c: PersonalityConfig) => void
  chatConfig: ChatConfig | null
  setChatConfig: (c: ChatConfig) => void
  expressionConfig: ExpressionConfig | null
  setExpressionConfig: (c: ExpressionConfig) => void
  emojiConfig: EmojiConfig | null
  setEmojiConfig: (c: EmojiConfig) => void
  memoryConfig: MemoryConfig | null
  setMemoryConfig: (c: MemoryConfig) => void
  toolConfig: ToolConfig | null
  setToolConfig: (c: ToolConfig) => void
  voiceConfig: VoiceConfig | null
  setVoiceConfig: (c: VoiceConfig) => void
  messageReceiveConfig: MessageReceiveConfig | null
  setMessageReceiveConfig: (c: MessageReceiveConfig) => void
  dreamConfig: DreamConfig | null
  setDreamConfig: (c: DreamConfig) => void
  lpmmConfig: LPMMKnowledgeConfig | null
  setLpmmConfig: (c: LPMMKnowledgeConfig) => void
  keywordReactionConfig: KeywordReactionConfig | null
  setKeywordReactionConfig: (c: KeywordReactionConfig) => void
  responsePostProcessConfig: ResponsePostProcessConfig | null
  setResponsePostProcessConfig: (c: ResponsePostProcessConfig) => void
  chineseTypoConfig: ChineseTypoConfig | null
  setChineseTypoConfig: (c: ChineseTypoConfig) => void
  responseSplitterConfig: ResponseSplitterConfig | null
  setResponseSplitterConfig: (c: ResponseSplitterConfig) => void
  logConfig: LogConfig | null
  setLogConfig: (c: LogConfig) => void
  debugConfig: DebugConfig | null
  setDebugConfig: (c: DebugConfig) => void
  experimentalConfig: ExperimentalConfig | null
  setExperimentalConfig: (c: ExperimentalConfig) => void
  maimMessageConfig: MaimMessageConfig | null
  setMaimMessageConfig: (c: MaimMessageConfig) => void
  telemetryConfig: TelemetryConfig | null
  setTelemetryConfig: (c: TelemetryConfig) => void
  webuiConfig: WebUIConfig | null
  setWebuiConfig: (c: WebUIConfig) => void
  setHasUnsavedChanges: (v: boolean) => void
}

function DynamicConfigTabs(props: DynamicConfigTabsProps) {
  const { tabGroups } = props

  // 每个 tab host field name → 对应的 ReactNode 内容
  const tabContentMap: Record<string, React.ReactNode> = {
    bot: props.botConfig && (
      <BotInfoSection config={props.botConfig} onChange={props.setBotConfig} />
    ),
    personality: props.personalityConfig && (
      <PersonalitySection config={props.personalityConfig} onChange={props.setPersonalityConfig} />
    ),
    chat: props.chatConfig && (
      <DynamicConfigForm
        schema={{ className: 'ChatConfig', classDoc: '聊天配置', fields: [], nested: {} }}
        values={{ chat: props.chatConfig }}
        onChange={(field, value) => {
          if (field === 'chat') {
            props.setChatConfig(value as ChatConfig)
            props.setHasUnsavedChanges(true)
          }
        }}
        hooks={fieldHooks}
      />
    ),
    expression: props.expressionConfig && (
      <ExpressionSection config={props.expressionConfig} onChange={props.setExpressionConfig} />
    ),
    emoji: props.emojiConfig && props.memoryConfig && props.toolConfig && props.voiceConfig && (
      <FeaturesSection
        emojiConfig={props.emojiConfig}
        memoryConfig={props.memoryConfig}
        toolConfig={props.toolConfig}
        voiceConfig={props.voiceConfig}
        onEmojiChange={props.setEmojiConfig}
        onMemoryChange={props.setMemoryConfig}
        onToolChange={props.setToolConfig}
        onVoiceChange={props.setVoiceConfig}
      />
    ),
    response_post_process: (
      <>
        {props.keywordReactionConfig && props.responsePostProcessConfig && props.chineseTypoConfig && props.responseSplitterConfig && (
          <ProcessingSection
            keywordReactionConfig={props.keywordReactionConfig}
            responsePostProcessConfig={props.responsePostProcessConfig}
            chineseTypoConfig={props.chineseTypoConfig}
            responseSplitterConfig={props.responseSplitterConfig}
            onKeywordReactionChange={props.setKeywordReactionConfig}
            onResponsePostProcessChange={props.setResponsePostProcessConfig}
            onChineseTypoChange={props.setChineseTypoConfig}
            onResponseSplitterChange={props.setResponseSplitterConfig}
          />
        )}
        {props.messageReceiveConfig && (
          <MessageReceiveSection config={props.messageReceiveConfig} onChange={props.setMessageReceiveConfig} />
        )}
      </>
    ),
    dream: props.dreamConfig && (
      <DreamSection config={props.dreamConfig} onChange={props.setDreamConfig} />
    ),
    lpmm_knowledge: props.lpmmConfig && (
      <LPMMSection config={props.lpmmConfig} onChange={props.setLpmmConfig} />
    ),
    webui: props.webuiConfig && (
      <WebUISection config={props.webuiConfig} onChange={props.setWebuiConfig} />
    ),
    debug: (
      <>
        {props.logConfig && <LogSection config={props.logConfig} onChange={props.setLogConfig} />}
        {props.debugConfig && <DebugSection config={props.debugConfig} onChange={props.setDebugConfig} />}
        {props.experimentalConfig && <ExperimentalSection config={props.experimentalConfig} onChange={props.setExperimentalConfig} />}
        {props.maimMessageConfig && <MaimMessageSection config={props.maimMessageConfig} onChange={props.setMaimMessageConfig} />}
        {props.telemetryConfig && <TelemetrySection config={props.telemetryConfig} onChange={props.setTelemetryConfig} />}
      </>
    ),
  }

  if (tabGroups.length === 0) return null

  return (
    <Tabs defaultValue={tabGroups[0].id} className="w-full">
      <TabsList className="flex flex-wrap h-auto gap-1 p-1">
        {tabGroups.map((tab) => (
          <TabsTrigger
            key={tab.id}
            value={tab.id}
            className="text-xs px-2 py-1.5 sm:px-3 sm:py-2 data-[state=active]:shadow-sm"
          >
            {tab.label}
          </TabsTrigger>
        ))}
      </TabsList>
      {tabGroups.map((tab) => (
        <TabsContent key={tab.id} value={tab.id} className="space-y-4">
          {tabContentMap[tab.id]}
        </TabsContent>
      ))}
    </Tabs>
  )
}
