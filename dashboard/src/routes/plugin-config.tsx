import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import { DraftNumberInput } from '@/components/ui/draft-number-input'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Slider } from '@/components/ui/slider'
import { Textarea } from '@/components/ui/textarea'
import { Badge } from '@/components/ui/badge'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { ListFieldEditor } from '@/components/ListFieldEditor'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { CodeEditor } from '@/components/CodeEditor'
import { useTheme } from '@/components/use-theme'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  Settings,
  AlertCircle,
  AlertTriangle,
  Package,
  ArrowUp,
  RefreshCw,
  ChevronRight,
  ChevronDown,
  Save,
  RotateCcw,
  Power,
  Loader2,
  Search,
  ArrowLeft,
  Info,
  Eye,
  EyeOff,
  RotateCw,
  Code2,
  Layout,
  Trash2,
} from 'lucide-react'
import { RestartProvider, useRestart } from '@/lib/restart-context'
import { RestartOverlay } from '@/components/restart-overlay'
import type {
  InstalledPlugin,
  ConfigFieldSchema,
  ConfigSectionSchema,
  ItemFieldDefinition,
} from '@/lib/plugin-api'
import { PluginIcon } from './plugins/PluginIcon'
import { getPluginTypeLabel } from './plugins/types'
import { getNestedRecord } from './plugin-config/utils'
import { usePluginList } from './plugin-config/hooks/usePluginList'
import { usePluginLifecycle } from './plugin-config/hooks/usePluginLifecycle'
import { usePluginConfigEditor } from './plugin-config/hooks/usePluginConfigEditor'

// 字段渲染组件
interface FieldRendererProps {
  field: ConfigFieldSchema
  value: unknown
  onChange: (value: unknown) => void
  sectionName: string
}

function getLocaleCandidates(language: string): string[] {
  const normalized = (language || 'zh').replace('-', '_')
  const base = normalized.split('_')[0]
  const candidates = [language, normalized, base]

  if (base === 'zh') candidates.push('zh_CN', 'zh-CN')
  if (base === 'en') candidates.push('en_US', 'en-US')
  if (base === 'ja') candidates.push('ja_JP', 'ja-JP')
  if (base === 'ko') candidates.push('ko_KR', 'ko-KR')

  candidates.push('zh_CN', 'zh-CN', 'zh')
  return Array.from(new Set(candidates.filter(Boolean)))
}

function resolveLocalizedText(
  value: unknown,
  language: string,
  fallback = '',
  i18n?: Record<string, Record<string, string>>,
  key?: string,
): string {
  const candidates = getLocaleCandidates(language)

  if (i18n && key) {
    for (const locale of candidates) {
      const localized = i18n[locale]?.[key]
      if (typeof localized === 'string' && localized.trim()) {
        return localized
      }
    }
  }

  if (typeof value === 'string') {
    return value || fallback
  }

  if (value && typeof value === 'object' && !Array.isArray(value)) {
    const localizedMap = value as Record<string, unknown>
    for (const locale of candidates) {
      const localized = localizedMap[locale]
      if (typeof localized === 'string' && localized.trim()) {
        return localized
      }
    }
  }

  return fallback
}

function localizeItemFields(
  itemFields: Record<string, ItemFieldDefinition> | undefined,
  language: string,
): Record<string, ItemFieldDefinition> | undefined {
  if (!itemFields) return undefined

  return Object.fromEntries(
    Object.entries(itemFields).map(([fieldName, field]) => [
      fieldName,
      {
        ...field,
        label: resolveLocalizedText(field.label, language, fieldName, field.i18n, 'label'),
        placeholder: resolveLocalizedText(field.placeholder, language, '', field.i18n, 'placeholder') || undefined,
      },
    ])
  )
}

function FieldRenderer({ field, value, onChange }: FieldRendererProps) {
  const [showPassword, setShowPassword] = useState(false)
  const { i18n } = useTranslation()
  const language = i18n.resolvedLanguage || i18n.language || 'zh'
  const label = resolveLocalizedText(field.label, language, field.name, field.i18n, 'label')
  const hint = resolveLocalizedText(field.hint, language, '', field.i18n, 'hint')
  const placeholder = resolveLocalizedText(field.placeholder, language, '', field.i18n, 'placeholder')
  const localizedItemFields = localizeItemFields(field.item_fields, language)

  // 根据 ui_type 渲染不同的控件
  switch (field.ui_type) {
    case 'switch':
      return (
        <div className="flex items-center justify-between">
          <div className="space-y-0.5">
            <Label>{label}</Label>
            {hint && (
              <p className="text-xs text-muted-foreground">{hint}</p>
            )}
          </div>
          <Switch
            checked={Boolean(value ?? field.default)}
            onCheckedChange={onChange}
            disabled={field.disabled}
          />
        </div>
      )

    case 'number':
      return (
        <div className="space-y-2">
          <Label>{label}</Label>
          <DraftNumberInput
            value={value}
            defaultValue={field.default}
            onValueChange={onChange}
            min={field.min}
            max={field.max}
            step={field.step ?? 1}
            placeholder={placeholder}
            disabled={field.disabled}
          />
          {hint && (
            <p className="text-xs text-muted-foreground">{hint}</p>
          )}
        </div>
      )

    case 'slider':
      return (
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <Label>{label}</Label>
            <span className="text-sm text-muted-foreground">
              {value as number ?? field.default}
            </span>
          </div>
          <Slider
            value={[value as number ?? field.default as number]}
            onValueChange={(v) => onChange(v[0])}
            min={field.min ?? 0}
            max={field.max ?? 100}
            step={field.step ?? 1}
            disabled={field.disabled}
          />
          {hint && (
            <p className="text-xs text-muted-foreground">{hint}</p>
          )}
        </div>
      )

    case 'select':
      return (
        <div className="space-y-2">
          <Label>{label}</Label>
          <Select
            value={String(value ?? field.default)}
            onValueChange={onChange}
            disabled={field.disabled}
          >
            <SelectTrigger>
              <SelectValue placeholder={placeholder || '请选择'} />
            </SelectTrigger>
            <SelectContent>
              {field.choices?.map((choice) => (
                <SelectItem key={String(choice)} value={String(choice)}>
                  {String(choice)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {hint && (
            <p className="text-xs text-muted-foreground">{hint}</p>
          )}
        </div>
      )

    case 'textarea':
      return (
        <div className="space-y-2">
          <Label>{label}</Label>
          <Textarea
            value={value as string ?? field.default}
            onChange={(e) => onChange(e.target.value)}
            placeholder={placeholder}
            rows={field.rows ?? 3}
            disabled={field.disabled}
          />
          {hint && (
            <p className="text-xs text-muted-foreground">{hint}</p>
          )}
        </div>
      )

    case 'password':
      return (
        <div className="space-y-2">
          <Label>{label}</Label>
          <div className="relative">
            <Input
              type={showPassword ? 'text' : 'password'}
              value={value as string ?? ''}
              onChange={(e) => onChange(e.target.value)}
              placeholder={placeholder}
              disabled={field.disabled}
              className="pr-10"
            />
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="absolute right-0 top-0 h-full px-3"
              onClick={() => setShowPassword(!showPassword)}
            >
              {showPassword ? (
                <EyeOff className="h-4 w-4" />
              ) : (
                <Eye className="h-4 w-4" />
              )}
            </Button>
          </div>
          {hint && (
            <p className="text-xs text-muted-foreground">{hint}</p>
          )}
        </div>
      )

    case 'list':
      return (
        <div className="space-y-2">
          <Label>{label}</Label>
          <ListFieldEditor
            value={Array.isArray(value) ? value : (Array.isArray(field.default) ? field.default : [])}
            onChange={(newValue) => onChange(newValue)}
            itemType={field.item_type ?? 'string'}
            itemFields={localizedItemFields}
            minItems={field.min_items}
            maxItems={field.max_items}
            disabled={field.disabled}
            placeholder={placeholder}
          />
          {hint && (
            <p className="text-xs text-muted-foreground">{hint}</p>
          )}
        </div>
      )

    case 'text':
    default:
      return (
        <div className="space-y-2">
          <Label>{label}</Label>
          <Input
            type="text"
            value={value as string ?? field.default ?? ''}
            onChange={(e) => onChange(e.target.value)}
            placeholder={placeholder}
            maxLength={field.max_length}
            disabled={field.disabled}
          />
          {hint && (
            <p className="text-xs text-muted-foreground">{hint}</p>
          )}
        </div>
      )
  }
}

// Section 渲染组件
interface SectionRendererProps {
  sectionName: string
  section: ConfigSectionSchema
  config: Record<string, unknown>
  onChange: (sectionName: string, fieldName: string, value: unknown) => void
}

function SectionRenderer({ sectionName, section, config, onChange }: SectionRendererProps) {
  const [isOpen, setIsOpen] = useState(!section.collapsed)
  const { i18n } = useTranslation()
  const language = i18n.resolvedLanguage || i18n.language || 'zh'
  const resolvedSectionName = section.name || sectionName
  const sectionConfig = getNestedRecord(config, resolvedSectionName)
  const title = resolveLocalizedText(section.title, language, sectionName, section.i18n, 'title')
  const description = resolveLocalizedText(section.description, language, '', section.i18n, 'description')

  // 按 order 排序字段
  const sortedFields = Object.entries(section.fields)
    .filter(([, field]) => !field.hidden)
    .sort(([, a], [, b]) => (a.order ?? 0) - (b.order ?? 0))

  return (
    <Collapsible open={isOpen} onOpenChange={setIsOpen}>
      <Card>
        <CollapsibleTrigger asChild>
          <CardHeader className="cursor-pointer hover:bg-muted/50 transition-colors">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                {isOpen ? (
                  <ChevronDown className="h-4 w-4 text-muted-foreground" />
                ) : (
                  <ChevronRight className="h-4 w-4 text-muted-foreground" />
                )}
                <CardTitle className="text-lg">{title}</CardTitle>
              </div>
              <Badge variant="secondary" className="text-xs">
                {sortedFields.length} 项
              </Badge>
            </div>
            {description && (
              <CardDescription className="ml-6">
                {description}
              </CardDescription>
            )}
          </CardHeader>
        </CollapsibleTrigger>
        <CollapsibleContent>
          <CardContent className="space-y-4 pt-0">
            {sortedFields.map(([fieldName, field]) => (
              <FieldRenderer
                key={fieldName}
                field={field}
                value={sectionConfig?.[fieldName]}
                onChange={(value) => onChange(resolvedSectionName, fieldName, value)}
                sectionName={resolvedSectionName}
              />
            ))}
          </CardContent>
        </CollapsibleContent>
      </Card>
    </Collapsible>
  )
}

// 插件配置编辑器
interface PluginConfigEditorProps {
  plugin: InstalledPlugin
  onBack: () => void
  initialTab?: string
}

function PluginConfigEditor({ plugin, onBack, initialTab }: PluginConfigEditorProps) {
  const { triggerRestart, isRestarting } = useRestart()
  const { i18n } = useTranslation()
  const language = i18n.resolvedLanguage || i18n.language || 'zh'

  const {
    editMode,
    setEditMode,
    pluginPageTab,
    setPluginPageTab,
    activeConfigTab,
    handleConfigTabChange,
    schema,
    config,
    sourceCode,
    handleSourceCodeChange,
    handleFieldChange,
    loading,
    saving,
    hasChanges,
    hasTomlError,
    handleSave,
    handleReset,
    handleToggle,
    resetDialogOpen,
    setResetDialogOpen,
    navigationBlocker,
    internalLeavePromptOpen,
    handleBack,
    closeLeavePrompt,
    leaveWithoutSaving,
    saveAndLeave,
  } = usePluginConfigEditor({ plugin, onBack, initialTab })

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    )
  }

  if (!schema) {
    return (
      <div className="flex flex-col items-center justify-center h-64 space-y-4">
        <AlertCircle className="h-12 w-12 text-muted-foreground" />
        <p className="text-muted-foreground">无法加载配置</p>
        <Button onClick={onBack} variant="outline">
          <ArrowLeft className="h-4 w-4 mr-2" />
          返回
        </Button>
      </div>
    )
  }

  // 按 order 排序 sections
  const sortedSections = Object.entries(schema.sections)
    .sort(([, a], [, b]) => (a.order ?? 0) - (b.order ?? 0))
  const schemaTabs = schema.layout.type === 'tabs' ? schema.layout.tabs : []
  const selectedConfigTab = schemaTabs.some((tab) => tab.id === activeConfigTab)
    ? activeConfigTab
    : schemaTabs[0]?.id

  // 获取当前启用状态
  const isEnabled = (config.plugin as Record<string, unknown>)?.enabled !== false
  const pluginName = resolveLocalizedText(
    schema.plugin_info.name,
    language,
    plugin.manifest.name,
    schema.plugin_info.i18n,
    'name',
  )
  const manifestUrls = plugin.manifest.urls as {
    repository?: string
    homepage?: string
    documentation?: string
    issues?: string
  } | undefined
  const pluginHomepageUrl = plugin.manifest.homepage_url || manifestUrls?.homepage
  const pluginRepositoryUrl = plugin.manifest.repository_url || manifestUrls?.repository
  const pluginDetailItems = [
    { label: '插件 ID', value: plugin.manifest.id || plugin.id },
    { label: '版本', value: schema.plugin_info.version || plugin.manifest.version },
    { label: '类型', value: getPluginTypeLabel(plugin) },
    { label: '作者', value: plugin.manifest.author?.name },
    { label: '许可证', value: plugin.manifest.license },
    { label: '最低麦麦版本', value: plugin.manifest.host_application?.min_version },
    { label: '安装路径', value: plugin.path },
  ].filter((item): item is { label: string; value: string } => typeof item.value === 'string' && item.value.trim().length > 0)

  return (
    <div className="space-y-4 sm:space-y-6">
      {/* 头部 */}
      <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-4">
        <div className="flex items-start gap-3">
          <Button variant="ghost" size="icon" onClick={handleBack}>
            <ArrowLeft className="h-5 w-5" />
          </Button>
          <div>
            <h1 className="text-xl font-bold sm:text-2xl" data-plugin-config-title>
              {pluginName}
            </h1>
            <div className="flex items-center gap-2 mt-1">
              <Badge variant={isEnabled ? 'default' : 'secondary'}>
                {isEnabled ? '已启用' : '已禁用'}
              </Badge>
              <span className="text-sm text-muted-foreground">
                v{schema.plugin_info.version || plugin.manifest.version}
              </span>
            </div>
          </div>
        </div>
        <div className="ml-10 flex flex-wrap gap-3 sm:ml-0">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setEditMode(editMode === 'visual' ? 'source' : 'visual')}
          >
            {editMode === 'visual' ? (
              <>
                <Code2 className="h-4 w-4 mr-2" />
                源代码
              </>
            ) : (
              <>
                <Layout className="h-4 w-4 mr-2" />
                可视化
              </>
            )}
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => triggerRestart()}
            disabled={isRestarting}
          >
            <RotateCw className={`h-4 w-4 mr-2 ${isRestarting ? 'animate-spin' : ''}`} />
            重启麦麦
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={handleToggle}
          >
            <Power className="h-4 w-4 mr-2" />
            {isEnabled ? '禁用' : '启用'}
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setResetDialogOpen(true)}
          >
            <RotateCcw className="h-4 w-4 mr-2" />
            重置
          </Button>
          <Button
            size="sm"
            onClick={handleSave}
            disabled={!hasChanges || saving}
          >
            {saving ? (
              <Loader2 className="h-4 w-4 mr-2 animate-spin" />
            ) : (
              <Save className="h-4 w-4 mr-2" />
            )}
            保存
          </Button>
        </div>
      </div>

      {/* 未保存提示 */}
      {hasChanges && (
        <Card className="border-orange-200 bg-orange-50 dark:bg-orange-950/20 dark:border-orange-900">
          <CardContent className="py-3">
            <div className="flex items-center gap-2">
              <Info className="h-4 w-4 text-orange-600" />
              <p className="text-sm text-orange-800 dark:text-orange-200">
                有未保存的更改
              </p>
            </div>
          </CardContent>
        </Card>
      )}

      <Tabs value={pluginPageTab} onValueChange={(value) => setPluginPageTab(value as 'settings' | 'details')}>
        <TabsList>
          <TabsTrigger value="settings">设置</TabsTrigger>
          <TabsTrigger value="details">详情</TabsTrigger>
        </TabsList>
        <TabsContent value="settings" className="mt-4">
          {/* 源代码模式 */}
          {editMode === 'source' && (
            <div className="space-y-4">
              <Alert>
                <AlertCircle className="h-4 w-4" />
                <AlertDescription>
                  <strong>源代码模式（高级功能）：</strong>直接编辑 TOML 配置文件。保存时会验证格式，只有格式正确才能保存。
                  {hasTomlError && (
                    <span className="text-destructive font-semibold ml-2">⚠️ 上次保存失败，请检查 TOML 格式</span>
                  )}
                </AlertDescription>
              </Alert>

              <CodeEditor
                value={sourceCode}
                onChange={handleSourceCodeChange}
                language="toml"
                height="calc(100vh - 350px)"
                minHeight="500px"
                placeholder="TOML 配置内容"
              />
            </div>
          )}

          {/* 可视化模式 */}
          {editMode === 'visual' && (
          <>
          {/* 配置区域 */}
          {schema.layout.type === 'tabs' && schemaTabs.length > 0 ? (
            // 标签页布局
            <Tabs value={selectedConfigTab} onValueChange={handleConfigTabChange}>
              <TabsList>
                {schemaTabs.map(tab => (
                  <TabsTrigger key={tab.id} value={tab.id}>
                    {resolveLocalizedText(tab.title, language, tab.id, tab.i18n, 'title')}
                    {tab.badge && (
                      <Badge variant="secondary" className="ml-2 text-xs">
                        {tab.badge}
                      </Badge>
                    )}
                  </TabsTrigger>
                ))}
              </TabsList>
              {schemaTabs.map(tab => (
                <TabsContent key={tab.id} value={tab.id} className="space-y-4 mt-4">
                  {tab.sections.map(sectionName => {
                    const section = schema.sections[sectionName]
                    if (!section) return null
                    return (
                      <SectionRenderer
                        key={sectionName}
                        sectionName={sectionName}
                        section={section}
                        config={config}
                        onChange={handleFieldChange}
                      />
                    )
                  })}
                </TabsContent>
              ))}
            </Tabs>
          ) : (
            // 自动布局
            <div className="space-y-4">
              {sortedSections.map(([sectionName, section]) => (
                <SectionRenderer
                  key={sectionName}
                  sectionName={sectionName}
                  section={section}
                  config={config}
                  onChange={handleFieldChange}
                />
              ))}
            </div>
          )}
          </>
          )}
        </TabsContent>
        <TabsContent value="details" className="mt-4">
          <Card>
            <CardHeader>
              <CardTitle>插件详情</CardTitle>
              <CardDescription>{plugin.manifest.description || '暂无描述'}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid gap-3 sm:grid-cols-2">
                {pluginDetailItems.map((item) => (
                  <div key={item.label} className="min-w-0 rounded-md border bg-muted/20 px-3 py-2">
                    <div className="text-xs font-medium text-muted-foreground">{item.label}</div>
                    <div className="mt-1 break-words text-sm">{item.value}</div>
                  </div>
                ))}
              </div>
              {(pluginHomepageUrl || pluginRepositoryUrl || manifestUrls?.documentation || manifestUrls?.issues) && (
                <div className="flex flex-wrap gap-2">
                  {pluginHomepageUrl && (
                    <Button variant="outline" size="sm" asChild>
                      <a href={pluginHomepageUrl} target="_blank" rel="noreferrer">主页</a>
                    </Button>
                  )}
                  {pluginRepositoryUrl && (
                    <Button variant="outline" size="sm" asChild>
                      <a href={pluginRepositoryUrl} target="_blank" rel="noreferrer">仓库</a>
                    </Button>
                  )}
                  {manifestUrls?.documentation && (
                    <Button variant="outline" size="sm" asChild>
                      <a href={manifestUrls.documentation} target="_blank" rel="noreferrer">文档</a>
                    </Button>
                  )}
                  {manifestUrls?.issues && (
                    <Button variant="outline" size="sm" asChild>
                      <a href={manifestUrls.issues} target="_blank" rel="noreferrer">问题反馈</a>
                    </Button>
                  )}
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>

      <Dialog
        open={internalLeavePromptOpen || navigationBlocker.status === 'blocked'}
        onOpenChange={(open) => {
          if (!open) {
            closeLeavePrompt()
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>有未保存的更改</DialogTitle>
            <DialogDescription>
              当前插件配置文件有修改，离开页面前是否保存？
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={closeLeavePrompt} disabled={saving}>
              取消
            </Button>
            <Button variant="outline" onClick={leaveWithoutSaving} disabled={saving}>
              不保存
            </Button>
            <Button onClick={saveAndLeave} disabled={saving}>
              {saving ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <Save className="h-4 w-4 mr-2" />}
              保存并离开
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 重置确认对话框 */}
      <Dialog open={resetDialogOpen} onOpenChange={setResetDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>确认重置配置</DialogTitle>
            <DialogDescription>
              这将删除当前配置文件，下次加载插件时将使用默认配置。此操作不可撤销。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setResetDialogOpen(false)}>
              取消
            </Button>
            <Button variant="destructive" onClick={handleReset}>
              确认重置
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

// 主页面组件 - 包装 RestartProvider
export function PluginConfigPage() {
  return (
    <RestartProvider>
      <PluginConfigPageContent />
    </RestartProvider>
  )
}

// 内部组件：实际内容
function PluginConfigPageContent() {
  const { themeConfig } = useTheme()

  const {
    plugins,
    loading,
    selectedPlugin,
    selectedPluginTab,
    openPluginConfig,
    closePluginConfig,
    loadPlugins,
    searchQuery,
    setSearchQuery,
    showUpdateOnly,
    setShowUpdateOnly,
    visiblePlugins,
    actingPluginId,
    setActingPluginId,
    performTogglePlugin,
    checkingUpdates,
    getPluginUpdateState,
    getPluginRepositoryUrl,
    isPluginDisabled,
    getPluginStatusBarClassName,
    getPluginStatusLabel,
    getPluginStatusMeta,
    installedCount,
    disabledCount,
    loadingCount,
    circuitOpenCount,
    loadFailedCount,
    enabledCount,
    loadSuccessCount,
    loadSuccessPercent,
    loadFailedPercent,
    loadingPercent,
    circuitPercent,
    showsCircuitSummary,
    modernLoadSummaryLabel,
    futureRetroPluginSummaryLabel,
  } = usePluginList()

  const {
    deleteDialogOpen,
    setDeleteDialogOpen,
    deletingPlugin,
    deleteProgress,
    openDeletePluginDialog,
    closeDeletePluginDialog,
    handleConfirmDeletePlugin,
    updateDialogOpen,
    setUpdateDialogOpen,
    updatingPlugin,
    updateProgress,
    openUpdatePluginDialog,
    closeUpdatePluginDialog,
    handleConfirmUpdatePlugin,
  } = usePluginLifecycle({
    getPluginRepositoryUrl,
    onChanged: loadPlugins,
    setActingPluginId,
  })

  const isModernDashboardStyle = themeConfig.dashboardStyle === 'modern'
  const isFutureRetroDashboardStyle = themeConfig.dashboardStyle === 'future-retro'

  // 如果选中了插件，显示配置编辑器
  if (selectedPlugin) {
    return (
      <>
        <ScrollArea className="h-full">
          <div className="p-4 sm:p-6">
            <PluginConfigEditor
              plugin={selectedPlugin}
              initialTab={selectedPluginTab}
              onBack={closePluginConfig}
            />
          </div>
        </ScrollArea>
        <RestartOverlay />
      </>
    )
  }

  return (
    <ScrollArea className="h-full">
      <div className="space-y-4 sm:space-y-6 p-4 sm:p-6">
        <div className="flex flex-nowrap items-center gap-2 sm:gap-3">
          <div className="relative min-w-0 flex-1 basis-0 sm:basis-72">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              placeholder="搜索插件..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="pl-9"
            />
          </div>
          <div
            data-dashboard-input="true"
            className="border-input flex h-9 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md border bg-transparent px-2 py-1 text-sm font-medium shadow-sm transition-colors sm:gap-2 sm:px-3"
          >
            <Label htmlFor="show-update-only" className="cursor-pointer text-sm font-medium">
              有更新
            </Label>
            <Switch
              id="show-update-only"
              checked={showUpdateOnly}
              disabled={checkingUpdates}
              onCheckedChange={setShowUpdateOnly}
            />
          </div>
          <Button
            variant="outline"
            size="icon"
            className="shrink-0"
            onClick={loadPlugins}
            aria-label="刷新"
            title="刷新"
          >
            <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
          </Button>
        </div>

        {/* 统计信息 */}
        {isModernDashboardStyle ? (
          <Card>
            <CardContent className="space-y-3 p-4">
              <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
                <span className="flex items-center gap-2">
                  <Package className="h-4 w-4 text-muted-foreground" />
                  已安装 <strong>{installedCount}</strong> 个插件
                </span>
                <span>已启用 <strong className="text-emerald-600">{enabledCount}</strong> 个</span>
                <span>已禁用 <strong className="text-muted-foreground">{disabledCount}</strong> 个</span>
                <span>加载中 <strong className="text-sky-600">{loadingCount}</strong> 个</span>
                {showsCircuitSummary && (
                  <span>熔断中 <strong className="text-orange-600">{circuitOpenCount}</strong> 个</span>
                )}
              </div>
              <div
                className="flex items-center gap-3 border-t pt-3 text-sm"
                aria-label={modernLoadSummaryLabel}
              >
                <span className="sr-only">
                  {modernLoadSummaryLabel}
                </span>
                <strong className="w-8 text-right text-emerald-600">{loadSuccessCount}</strong>
                <div className="flex h-3 min-w-28 flex-1 overflow-hidden bg-muted" aria-hidden="true">
                  <div className="bg-emerald-500" style={{ width: `${loadSuccessPercent}%` }} />
                  <div className="bg-sky-500" style={{ width: `${loadingPercent}%` }} />
                  <div className="bg-orange-500" style={{ width: `${circuitPercent}%` }} />
                  <div className="bg-red-500" style={{ width: `${loadFailedPercent}%` }} />
                </div>
                <strong className="w-8 text-red-600">{loadFailedCount}</strong>
              </div>
            </CardContent>
          </Card>
        ) : (
          <div className="space-y-3">
            <div
              className="space-y-2"
              aria-label={futureRetroPluginSummaryLabel}
            >
              <span className="sr-only">
                {futureRetroPluginSummaryLabel}
              </span>
              <div className="flex h-3 w-full overflow-hidden bg-muted" aria-hidden="true">
                {plugins.length > 0 ? (
                  plugins.map((plugin, index) => (
                    <div
                      key={`${plugin.id}-${index}`}
                      className={`min-w-0 flex-1 ${getPluginStatusBarClassName(plugin)} ${
                        index < plugins.length - 1 ? 'border-r border-background' : ''
                      }`}
                      title={`${plugin.manifest.name}：${getPluginStatusLabel(plugin)}`}
                    />
                  ))
                ) : (
                  <div className="h-full flex-1 bg-muted-foreground/20" />
                )}
              </div>
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
                <span className="flex items-center gap-1.5">
                  插件 <strong className="text-foreground">{installedCount}</strong>个
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="h-2 w-2 rounded-full bg-emerald-500" />
                  启用 <strong className="text-emerald-600">{enabledCount}</strong> 个
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="h-2 w-2 rounded-full bg-muted-foreground/45" />
                  禁用 <strong className="text-muted-foreground">{disabledCount}</strong> 个
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="h-2 w-2 rounded-full bg-sky-500" />
                  加载中 <strong className="text-sky-600">{loadingCount}</strong> 个
                </span>
                {showsCircuitSummary && (
                  <span className="flex items-center gap-1.5">
                    <span className="h-2 w-2 rounded-full bg-orange-500" />
                    熔断中 <strong className="text-orange-600">{circuitOpenCount}</strong> 个
                  </span>
                )}
                <span className="flex items-center gap-1.5">
                  <span className="h-2 w-2 rounded-full bg-red-500" />
                  启动失败 <strong className="text-red-600">{loadFailedCount}</strong> 个
                </span>
              </div>
            </div>
          </div>
        )}

        {/* 插件列表 */}
        {loading ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
          </div>
        ) : visiblePlugins.length === 0 ? (
          <div className="flex flex-col items-center justify-center space-y-4 py-12">
            <Package className="h-16 w-16 text-muted-foreground/50" />
            <div className="space-y-2 text-center">
              <p className="text-lg font-medium text-muted-foreground">
                {showUpdateOnly ? '暂无可更新插件' : searchQuery ? '没有找到匹配的插件' : '暂无已安装的插件'}
              </p>
              <p className="text-sm text-muted-foreground">
                {showUpdateOnly ? '当前已安装插件没有发现新版本' : searchQuery ? '尝试其他搜索关键词' : '前往插件市场安装插件'}
              </p>
            </div>
          </div>
        ) : (
          <div className="divide-y divide-border/80">
            {visiblePlugins.map(plugin => {
              const statusMeta = getPluginStatusMeta(plugin)
              const pluginActing = actingPluginId === plugin.id
              const pluginDisabled = isPluginDisabled(plugin)
              const updateState = getPluginUpdateState(plugin)
              return (
              <div
                key={plugin.id}
                data-plugin-list-item="true"
                className={`relative flex cursor-pointer flex-col justify-between gap-2 py-2.5 transition-all duration-150 ease-out hover:-translate-y-0.5 hover:bg-muted/55 hover:shadow-md focus-visible:-translate-y-0.5 focus-visible:bg-muted/55 focus-visible:outline-none focus-visible:shadow-md sm:min-h-0 sm:flex-row sm:items-center sm:gap-3 sm:px-2 sm:py-3 ${
                  isPluginDisabled(plugin) ? 'opacity-70' : ''
                }`}
                role="button"
                tabIndex={0}
                onClick={() => openPluginConfig(plugin)}
                onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openPluginConfig(plugin) } }}
              >
                <div className="flex min-w-0 items-start gap-3 sm:items-center">
                  <span
                    className={`mt-4 flex-shrink-0 sm:mt-0 ${
                      isFutureRetroDashboardStyle ? 'h-12 w-2' : 'h-2.5 w-2.5 rounded-full'
                    } ${statusMeta.dotClassName}`}
                    title={statusMeta.label}
                    aria-label={statusMeta.label}
                  />
                  <div className="flex w-12 flex-shrink-0 flex-col items-center gap-1 sm:w-10">
                    <PluginIcon pluginId={plugin.id} manifest={plugin.manifest} installed className="h-12 w-12 sm:h-10 sm:w-10" />
                    <span className="text-muted-foreground text-[0.65rem] leading-none">
                      v{plugin.manifest.version}
                    </span>
                  </div>
                  <div className="min-w-0 flex-1 space-y-2 sm:space-y-1">
                    <div className="flex min-w-0 flex-wrap items-center gap-2">
                      <h3 className="min-w-0 break-words text-sm font-medium leading-snug sm:truncate sm:text-base">
                        {plugin.manifest.name}
                      </h3>
                      <Badge variant="outline" className="text-xs flex-shrink-0">
                        {getPluginTypeLabel(plugin)}
                      </Badge>
                      {statusMeta.showsBadge !== false && (
                        <Badge
                          variant="outline"
                          className={`text-xs flex-shrink-0 gap-1 ${statusMeta.badgeClassName ?? ''}`}
                        >
                          {statusMeta.icon === 'loading' && <Loader2 className="h-3 w-3 animate-spin" />}
                          {statusMeta.icon === 'warning' && <AlertCircle className="h-3 w-3" />}
                          {statusMeta.icon === 'circuit' && <AlertTriangle className="h-3 w-3" />}
                          {statusMeta.label}
                        </Badge>
                      )}
                    </div>
                    <p className="line-clamp-2 text-sm leading-relaxed text-muted-foreground sm:truncate sm:leading-normal">
                      {plugin.manifest.description || '暂无描述'}
                    </p>
                  </div>
                </div>
                <div className="flex items-center justify-end gap-2 border-t pt-2 sm:flex-shrink-0 sm:border-t-0 sm:pt-0">
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-9 w-9 p-0"
                    title="配置"
                    aria-label="配置"
                    onClick={() => openPluginConfig(plugin)}
                  >
                    <Settings className="h-4 w-4" />
                  </Button>
                  <div
                    className="flex h-9 w-9 items-center justify-center"
                    title={pluginDisabled ? '启动插件' : '关闭插件'}
                  >
                    {pluginActing && <Loader2 className="h-4 w-4 animate-spin" />}
                    <Switch
                      data-plugin-list-switch="true"
                      checked={!pluginDisabled}
                      disabled={pluginActing}
                      aria-label={pluginDisabled ? '启动插件' : '关闭插件'}
                      onClick={(event) => event.stopPropagation()}
                      onCheckedChange={() => void performTogglePlugin(plugin)}
                    />
                  </div>
                  <Button
                    variant="outline"
                    size="sm"
                    className="relative h-9 w-9 p-0"
                    disabled={pluginActing || !updateState.canUpdate}
                    title={updateState.title}
                    aria-label={updateState.title || '更新/升级'}
                    onClick={(event) => openUpdatePluginDialog(plugin, event)}
                  >
                    {updateState.hasUpdate && (
                      <span
                        className="absolute -right-1 -top-1 h-3 w-3 rounded-sm bg-yellow-400 ring-2 ring-background"
                        aria-hidden="true"
                      />
                    )}
                    {pluginActing ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : checkingUpdates ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <ArrowUp className="h-4 w-4" />
                    )}
                  </Button>
                  <Button
                    variant="destructive"
                    size="sm"
                    className="h-9 w-9 p-0"
                    disabled={pluginActing}
                    title="删除"
                    aria-label="删除"
                    onClick={(event) => openDeletePluginDialog(plugin, event)}
                  >
                    {pluginActing ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <Trash2 className="h-4 w-4" />
                    )}
                  </Button>
                  <ChevronRight className="h-4 w-4 text-muted-foreground" />
                </div>
              </div>
              )
            })}
          </div>
        )}

        <Dialog open={updateDialogOpen} onOpenChange={(open) => {
          if (!open) {
            closeUpdatePluginDialog()
            return
          }
          setUpdateDialogOpen(true)
        }}>
          <DialogContent preventOutsideClose={updateProgress?.stage === 'loading'} hideCloseButton={updateProgress?.stage === 'loading'}>
            <DialogHeader>
              <DialogTitle>确认更新插件</DialogTitle>
              <DialogDescription>
                {updatingPlugin
                  ? `即将更新 ${updatingPlugin.manifest.name}。更新过程中请保持麦麦运行。`
                  : '即将更新插件。更新过程中请保持麦麦运行。'}
              </DialogDescription>
            </DialogHeader>

            {updateProgress && (
              <div className={`space-y-3 border p-3 ${
                updateProgress.stage === 'success'
                  ? 'border-green-200 bg-green-50 dark:border-green-900 dark:bg-green-950/20'
                  : updateProgress.stage === 'error'
                    ? 'border-red-200 bg-red-50 dark:border-red-900 dark:bg-red-950/20'
                    : 'bg-muted/50'
              }`}>
                <div className="flex items-center justify-between gap-3">
                  <div className="flex min-w-0 items-center gap-2">
                    {updateProgress.stage === 'loading' ? (
                      <Loader2 className="h-4 w-4 shrink-0 animate-spin" />
                    ) : updateProgress.stage === 'success' ? (
                      <Info className="h-4 w-4 shrink-0 text-green-600" />
                    ) : (
                      <Info className="h-4 w-4 shrink-0 text-red-600" />
                    )}
                    <span className={`text-sm font-medium ${
                      updateProgress.stage === 'success'
                        ? 'text-green-700 dark:text-green-300'
                        : updateProgress.stage === 'error'
                          ? 'text-red-700 dark:text-red-300'
                          : ''
                    }`}>
                      {updateProgress.stage === 'loading' && '正在更新'}
                      {updateProgress.stage === 'success' && '更新完成'}
                      {updateProgress.stage === 'error' && '更新失败'}
                    </span>
                  </div>
                  {updateProgress.stage !== 'error' && (
                    <span className={`shrink-0 text-sm font-medium ${
                      updateProgress.stage === 'success' ? 'text-green-700 dark:text-green-300' : ''
                    }`}>
                      {updateProgress.progress}%
                    </span>
                  )}
                </div>
                {updateProgress.stage !== 'error' && (
                  <Progress
                    value={updateProgress.progress}
                    className={`h-2 ${updateProgress.stage === 'success' ? '[&>div]:bg-green-500' : ''}`}
                  />
                )}
                <p className={`break-words text-sm ${
                  updateProgress.stage === 'success'
                    ? 'text-green-600 dark:text-green-400'
                    : updateProgress.stage === 'error'
                      ? 'text-red-600 dark:text-red-400'
                      : 'text-muted-foreground'
                }`}>
                  {updateProgress.stage === 'error'
                    ? (updateProgress.error || updateProgress.message || '更新失败')
                    : updateProgress.message}
                </p>
              </div>
            )}

            <DialogFooter>
              <Button
                variant="outline"
                onClick={closeUpdatePluginDialog}
                disabled={updateProgress?.stage === 'loading'}
              >
                {updateProgress?.stage === 'success' || updateProgress?.stage === 'error' ? '关闭' : '取消'}
              </Button>
              {updateProgress?.stage !== 'success' && updateProgress?.stage !== 'error' && (
                <Button
                  onClick={handleConfirmUpdatePlugin}
                  disabled={!updatingPlugin || updateProgress?.stage === 'loading'}
                >
                  {updateProgress?.stage === 'loading' ? (
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  ) : (
                    <ArrowUp className="mr-2 h-4 w-4" />
                  )}
                  {updateProgress?.stage === 'loading' ? '更新中' : '确认更新'}
                </Button>
              )}
            </DialogFooter>
          </DialogContent>
        </Dialog>

        <Dialog open={deleteDialogOpen} onOpenChange={(open) => {
          if (!open) {
            closeDeletePluginDialog()
            return
          }
          setDeleteDialogOpen(true)
        }}>
          <DialogContent preventOutsideClose={deleteProgress?.stage === 'loading'} hideCloseButton={deleteProgress?.stage === 'loading'}>
            <DialogHeader>
              <DialogTitle>确认删除插件</DialogTitle>
              <DialogDescription>
                {deletingPlugin
                  ? `即将删除 ${deletingPlugin.manifest.name}。删除后可从插件市场重新安装。`
                  : '即将删除插件。删除后可从插件市场重新安装。'}
              </DialogDescription>
            </DialogHeader>

            {deleteProgress && (
              <div className={`space-y-3 border p-3 ${
                deleteProgress.stage === 'success'
                  ? 'border-green-200 bg-green-50 dark:border-green-900 dark:bg-green-950/20'
                  : deleteProgress.stage === 'error'
                    ? 'border-red-200 bg-red-50 dark:border-red-900 dark:bg-red-950/20'
                    : 'bg-muted/50'
              }`}>
                <div className="flex items-center justify-between gap-3">
                  <div className="flex min-w-0 items-center gap-2">
                    {deleteProgress.stage === 'loading' ? (
                      <Loader2 className="h-4 w-4 shrink-0 animate-spin" />
                    ) : deleteProgress.stage === 'success' ? (
                      <Info className="h-4 w-4 shrink-0 text-green-600" />
                    ) : (
                      <Info className="h-4 w-4 shrink-0 text-red-600" />
                    )}
                    <span className={`text-sm font-medium ${
                      deleteProgress.stage === 'success'
                        ? 'text-green-700 dark:text-green-300'
                        : deleteProgress.stage === 'error'
                          ? 'text-red-700 dark:text-red-300'
                          : ''
                    }`}>
                      {deleteProgress.stage === 'loading' && '正在删除'}
                      {deleteProgress.stage === 'success' && '删除完成'}
                      {deleteProgress.stage === 'error' && '删除失败'}
                    </span>
                  </div>
                  {deleteProgress.stage !== 'error' && (
                    <span className={`shrink-0 text-sm font-medium ${
                      deleteProgress.stage === 'success' ? 'text-green-700 dark:text-green-300' : ''
                    }`}>
                      {deleteProgress.progress}%
                    </span>
                  )}
                </div>
                {deleteProgress.stage !== 'error' && (
                  <Progress
                    value={deleteProgress.progress}
                    className={`h-2 ${deleteProgress.stage === 'success' ? '[&>div]:bg-green-500' : ''}`}
                  />
                )}
                <p className={`break-words text-sm ${
                  deleteProgress.stage === 'success'
                    ? 'text-green-600 dark:text-green-400'
                    : deleteProgress.stage === 'error'
                      ? 'text-red-600 dark:text-red-400'
                      : 'text-muted-foreground'
                }`}>
                  {deleteProgress.stage === 'error'
                    ? (deleteProgress.error || deleteProgress.message || '删除失败')
                    : deleteProgress.message}
                </p>
              </div>
            )}

            <DialogFooter>
              <Button
                variant="outline"
                onClick={closeDeletePluginDialog}
                disabled={deleteProgress?.stage === 'loading'}
              >
                {deleteProgress?.stage === 'success' || deleteProgress?.stage === 'error' ? '关闭' : '取消'}
              </Button>
              {deleteProgress?.stage !== 'success' && deleteProgress?.stage !== 'error' && (
                <Button
                  variant="destructive"
                  onClick={handleConfirmDeletePlugin}
                  disabled={!deletingPlugin || deleteProgress?.stage === 'loading'}
                >
                  {deleteProgress?.stage === 'loading' ? (
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  ) : (
                    <Trash2 className="mr-2 h-4 w-4" />
                  )}
                  {deleteProgress?.stage === 'loading' ? '删除中' : '确认删除'}
                </Button>
              )}
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>
    </ScrollArea>
  )
}
