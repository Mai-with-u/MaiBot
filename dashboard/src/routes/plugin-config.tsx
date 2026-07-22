import { type CSSProperties, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
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
import { MultiSelect } from '@/components/ui/multi-select'
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
  Loader2,
  Search,
  ArrowLeft,
  Info,
  Eye,
  EyeOff,
  RotateCw,
  Code2,
  Layout,
  BookOpen,
  FileText,
  GripHorizontal,
  Trash2,
  Wrench,
  Terminal,
  X,
} from 'lucide-react'
import { RestartProvider, useRestart } from '@/lib/restart-context'
import { RestartOverlay } from '@/components/restart-overlay'
import { getLocalPluginChangelog, getLocalPluginReadme, getPluginRuntimeComponents } from '@/lib/plugin-api'
import { MarkdownRenderer } from '@/components/markdown-renderer'
import { PluginStats } from '@/components/plugin-stats'
import type {
  InstalledPlugin,
  ConfigFieldSchema,
  ConfigSectionSchema,
  ItemFieldDefinition,
  PluginRuntimeComponent,
  PluginRuntimeComponentType,
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
  key?: string
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
  language: string
): Record<string, ItemFieldDefinition> | undefined {
  if (!itemFields) return undefined

  return Object.fromEntries(
    Object.entries(itemFields).map(([fieldName, field]) => [
      fieldName,
      {
        ...field,
        label: resolveLocalizedText(field.label, language, fieldName, field.i18n, 'label'),
        placeholder:
          resolveLocalizedText(field.placeholder, language, '', field.i18n, 'placeholder') ||
          undefined,
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
  const placeholder = resolveLocalizedText(
    field.placeholder,
    language,
    '',
    field.i18n,
    'placeholder'
  )
  const localizedItemFields = localizeItemFields(field.item_fields, language)

  // 根据 ui_type 渲染不同的控件
  switch (field.ui_type) {
    case 'switch':
      return (
        <div className="flex items-center justify-between">
          <div className="space-y-0.5">
            <Label>{label}</Label>
            {hint && <p className="text-muted-foreground text-xs">{hint}</p>}
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
          {hint && <p className="text-muted-foreground text-xs">{hint}</p>}
        </div>
      )

    case 'slider':
      return (
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <Label>{label}</Label>
            <span className="text-muted-foreground text-sm">
              {(value as number) ?? field.default}
            </span>
          </div>
          <Slider
            value={[(value as number) ?? (field.default as number)]}
            onValueChange={(v) => onChange(v[0])}
            min={field.min ?? 0}
            max={field.max ?? 100}
            step={field.step ?? 1}
            disabled={field.disabled}
          />
          {hint && <p className="text-muted-foreground text-xs">{hint}</p>}
        </div>
      )

    case 'select':
      if (field.multiple) {
        const selectedValues = Array.isArray(value)
          ? value.map(v => String(v))
          : Array.isArray(field.default)
            ? field.default.map(v => String(v))
            : []

        return (
          <div className="space-y-2">
            <Label>{label}</Label>
            <MultiSelect
              options={(field.choices ?? []).map((choice) => ({
                label: String(choice),
                value: String(choice),
              }))}
              selected={selectedValues}
              onChange={onChange}
              placeholder={placeholder || '请选择'}
              disabled={field.disabled}
            />
            {hint && (
              <p className="text-xs text-muted-foreground">{hint}</p>
            )}
          </div>
        )
      }

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
          {hint && <p className="text-muted-foreground text-xs">{hint}</p>}
        </div>
      )

    case 'textarea':
      return (
        <div className="space-y-2">
          <Label>{label}</Label>
          <Textarea
            value={(value as string) ?? field.default}
            onChange={(e) => onChange(e.target.value)}
            placeholder={placeholder}
            rows={field.rows ?? 3}
            disabled={field.disabled}
          />
          {hint && <p className="text-muted-foreground text-xs">{hint}</p>}
        </div>
      )

    case 'password':
      return (
        <div className="space-y-2">
          <Label>{label}</Label>
          <div className="relative">
            <Input
              type={showPassword ? 'text' : 'password'}
              value={(value as string) ?? ''}
              onChange={(e) => onChange(e.target.value)}
              placeholder={placeholder}
              disabled={field.disabled}
              className="pr-10"
            />
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="absolute top-0 right-0 h-full px-3"
              onClick={() => setShowPassword(!showPassword)}
            >
              {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
            </Button>
          </div>
          {hint && <p className="text-muted-foreground text-xs">{hint}</p>}
        </div>
      )

    case 'list':
      return (
        <div className="space-y-2">
          <Label>{label}</Label>
          <ListFieldEditor
            value={Array.isArray(value) ? value : Array.isArray(field.default) ? field.default : []}
            onChange={(newValue) => onChange(newValue)}
            itemType={field.item_type ?? 'string'}
            itemFields={localizedItemFields}
            minItems={field.min_items}
            maxItems={field.max_items}
            disabled={field.disabled}
            placeholder={placeholder}
          />
          {hint && <p className="text-muted-foreground text-xs">{hint}</p>}
        </div>
      )

    case 'text':
    default:
      return (
        <div className="space-y-2">
          <Label>{label}</Label>
          <Input
            type="text"
            value={(value as string) ?? field.default ?? ''}
            onChange={(e) => onChange(e.target.value)}
            placeholder={placeholder}
            maxLength={field.max_length}
            disabled={field.disabled}
          />
          {hint && <p className="text-muted-foreground text-xs">{hint}</p>}
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

function getFieldGridClassName(field: ConfigFieldSchema): string {
  if (field.ui_type === 'textarea' || field.ui_type === 'list' || field.ui_type === 'slider') {
    return 'lg:col-span-2'
  }

  return 'min-w-0'
}

function SectionRenderer({ sectionName, section, config, onChange }: SectionRendererProps) {
  const [isOpen, setIsOpen] = useState(!section.collapsed)
  const { i18n } = useTranslation()
  const language = i18n.resolvedLanguage || i18n.language || 'zh'
  const resolvedSectionName = section.name || sectionName
  const sectionConfig = getNestedRecord(config, resolvedSectionName)
  const title = resolveLocalizedText(section.title, language, sectionName, section.i18n, 'title')
  const description = resolveLocalizedText(
    section.description,
    language,
    '',
    section.i18n,
    'description'
  )

  // 按 order 排序字段
  const sortedFields = Object.entries(section.fields)
    .filter(([, field]) => !field.hidden)
    .sort(([, a], [, b]) => (a.order ?? 0) - (b.order ?? 0))

  return (
    <Collapsible open={isOpen} onOpenChange={setIsOpen}>
      <Card>
        <CollapsibleTrigger asChild>
          <CardHeader className="hover:bg-muted/50 cursor-pointer gap-0.5 px-4! py-2! transition-colors sm:px-4! sm:py-2!">
            <div className="flex items-center">
              <div className="flex min-w-0 items-center gap-2">
                {isOpen ? (
                  <ChevronDown className="text-muted-foreground h-4 w-4" />
                ) : (
                  <ChevronRight className="text-muted-foreground h-4 w-4" />
                )}
                <CardTitle className="min-w-0 truncate text-base">{title}</CardTitle>
              </div>
            </div>
            {description && <CardDescription className="ml-6 text-xs leading-tight">{description}</CardDescription>}
          </CardHeader>
        </CollapsibleTrigger>
        <CollapsibleContent>
          <CardContent className="grid grid-cols-1 gap-4 pt-0 lg:grid-cols-2">
            {sortedFields.map(([fieldName, field]) => (
              <div key={fieldName} className={getFieldGridClassName(field)}>
                <FieldRenderer
                  field={field}
                  value={sectionConfig?.[fieldName]}
                  onChange={(value) => onChange(resolvedSectionName, fieldName, value)}
                  sectionName={resolvedSectionName}
                />
              </div>
            ))}
          </CardContent>
        </CollapsibleContent>
      </Card>
    </Collapsible>
  )
}

interface PluginDetailItem {
  label: string
  value: string
}

interface PluginDetailsPanelProps {
  plugin: InstalledPlugin
  description: string
  detailItems: PluginDetailItem[]
  homepageUrl?: string
  repositoryUrl?: string
  documentationUrl?: string
  issuesUrl?: string
  changelog?: string | null
}

type ComponentDisplayGroup = 'tool' | 'command'

const COMPONENT_GROUP_LABELS: Record<ComponentDisplayGroup, string> = {
  tool: '工具',
  command: '命令',
}

const COMPONENT_GROUP_DESCRIPTIONS: Record<ComponentDisplayGroup, string> = {
  tool: '包含 Tool 与旧版本兼容的 Action',
  command: '通过命令文本触发的组件',
}

const COMPONENT_TYPE_LABELS: Record<PluginRuntimeComponentType, string> = {
  action: '旧版动作',
  command: '命令',
  tool: '工具',
}

const COMPONENT_GROUP_ICONS = {
  tool: Wrench,
  command: Terminal,
}

function getSchemaPropertyNames(schema: Record<string, unknown> | undefined): string[] {
  const properties = schema?.properties
  if (!properties || typeof properties !== 'object' || Array.isArray(properties)) {
    return []
  }
  return Object.keys(properties)
}

function resolveComponentDisplayGroup(component: PluginRuntimeComponent): ComponentDisplayGroup {
  return component.component_type === 'command' ? 'command' : 'tool'
}

function groupComponentsByDisplayGroup(components: PluginRuntimeComponent[]) {
  return components.reduce<Record<ComponentDisplayGroup, PluginRuntimeComponent[]>>(
    (grouped, component) => {
      grouped[resolveComponentDisplayGroup(component)].push(component)
      return grouped
    },
    { tool: [], command: [] }
  )
}

function PluginDetailsPanel({
  plugin,
  description,
  detailItems,
  homepageUrl,
  repositoryUrl,
  documentationUrl,
  issuesUrl,
  changelog,
}: PluginDetailsPanelProps) {
  const [components, setComponents] = useState<PluginRuntimeComponent[]>([])
  const [componentsLoading, setComponentsLoading] = useState(true)
  const [componentsError, setComponentsError] = useState('')
  const [readme, setReadme] = useState('')
  const [readmeLoading, setReadmeLoading] = useState(true)
  const [readmeError, setReadmeError] = useState('')

  useEffect(() => {
    let cancelled = false

    setComponentsLoading(true)
    setComponentsError('')
    getPluginRuntimeComponents(plugin.id)
      .then((data) => {
        if (!cancelled) {
          setComponents(data)
        }
      })
      .catch((error) => {
        if (!cancelled) {
          setComponentsError(error instanceof Error ? error.message : '组件加载失败')
        }
      })
      .finally(() => {
        if (!cancelled) {
          setComponentsLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [plugin.id])

  useEffect(() => {
    let cancelled = false

    setReadmeLoading(true)
    setReadmeError('')
    setReadme('')
    getLocalPluginReadme(plugin.id)
      .then((content) => {
        if (!cancelled) {
          setReadme(content)
        }
      })
      .catch((error) => {
        if (!cancelled) {
          setReadmeError(error instanceof Error ? error.message : 'README 加载失败')
        }
      })
      .finally(() => {
        if (!cancelled) {
          setReadmeLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [plugin.id])

  const groupedComponents = groupComponentsByDisplayGroup(components)
  const componentCount = components.length
  const statsPluginId = plugin.manifest.id || plugin.id

  return (
    <div className="space-y-4">
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.4fr)_minmax(320px,0.8fr)]">
        <Card>
          <CardHeader>
            <CardTitle>插件详情</CardTitle>
            <CardDescription>{description || '暂无描述'}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid gap-3 sm:grid-cols-2">
              {detailItems.map((item) => (
                <div key={item.label} className="bg-muted/20 min-w-0 rounded-md border px-3 py-2">
                  <div className="text-muted-foreground text-xs font-medium">{item.label}</div>
                  <div className="mt-1 text-sm break-words">{item.value}</div>
                </div>
              ))}
            </div>
            {(homepageUrl || repositoryUrl || documentationUrl || issuesUrl) && (
              <div className="flex flex-wrap gap-2">
                {homepageUrl && (
                  <Button variant="outline" size="sm" asChild>
                    <a href={homepageUrl} target="_blank" rel="noreferrer">
                      主页
                    </a>
                  </Button>
                )}
                {repositoryUrl && (
                  <Button variant="outline" size="sm" asChild>
                    <a href={repositoryUrl} target="_blank" rel="noreferrer">
                      仓库
                    </a>
                  </Button>
                )}
                {documentationUrl && (
                  <Button variant="outline" size="sm" asChild>
                    <a href={documentationUrl} target="_blank" rel="noreferrer">
                      文档
                    </a>
                  </Button>
                )}
                {issuesUrl && (
                  <Button variant="outline" size="sm" asChild>
                    <a href={issuesUrl} target="_blank" rel="noreferrer">
                      问题反馈
                    </a>
                  </Button>
                )}
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>市场反馈</CardTitle>
            <CardDescription>点赞、评分和评论会提交到插件市场统计服务。</CardDescription>
          </CardHeader>
          <CardContent>
            <PluginStats pluginId={statsPluginId} />
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>README</CardTitle>
          <CardDescription>插件根目录中的说明文档。</CardDescription>
        </CardHeader>
        <CardContent>
          {readmeLoading ? (
            <div className="text-muted-foreground flex items-center justify-center gap-2 py-8 text-sm">
              <Loader2 className="h-4 w-4 animate-spin" />
              正在加载 README
            </div>
          ) : readmeError ? (
            <Alert variant="destructive">
              <AlertCircle className="h-4 w-4" />
              <AlertDescription>{readmeError}</AlertDescription>
            </Alert>
          ) : readme ? (
            <ScrollArea className="h-[min(48vh,540px)] pr-4">
              <MarkdownRenderer content={readme} />
            </ScrollArea>
          ) : (
            <div className="text-muted-foreground rounded-md border border-dashed px-4 py-8 text-center text-sm">
              暂无 README
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>更新日志</CardTitle>
          <CardDescription>插件作者提供的版本变更记录。</CardDescription>
        </CardHeader>
        <CardContent>
          {changelog ? (
            <ScrollArea className="h-[min(36vh,420px)] pr-4">
              <MarkdownRenderer content={changelog} />
            </ScrollArea>
          ) : (
            <div className="text-muted-foreground rounded-md border border-dashed px-4 py-8 text-center text-sm">
              暂无更新日志
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <CardTitle>注册组件</CardTitle>
              <CardDescription>当前插件运行时已注册的 Tool、旧版 Action 和 Command。</CardDescription>
            </div>
            <Badge variant="secondary">{componentCount} 个组件</Badge>
          </div>
        </CardHeader>
        <CardContent>
          {componentsLoading ? (
            <div className="text-muted-foreground flex items-center justify-center gap-2 py-8 text-sm">
              <Loader2 className="h-4 w-4 animate-spin" />
              正在加载组件
            </div>
          ) : componentsError ? (
            <Alert variant="destructive">
              <AlertCircle className="h-4 w-4" />
              <AlertDescription>{componentsError}</AlertDescription>
            </Alert>
          ) : componentCount === 0 ? (
            <div className="text-muted-foreground rounded-md border border-dashed px-4 py-8 text-center text-sm">
              当前插件未注册运行时组件
            </div>
          ) : (
            <div className="grid gap-4 lg:grid-cols-2">
              {(Object.keys(COMPONENT_GROUP_LABELS) as ComponentDisplayGroup[]).map((componentGroup) => {
                const Icon = COMPONENT_GROUP_ICONS[componentGroup]
                const typedComponents = groupedComponents[componentGroup]
                return (
                  <section key={componentGroup} className="min-w-0 space-y-3">
                    <div className="flex items-center justify-between gap-2">
                      <div className="min-w-0">
                        <h3 className="flex items-center gap-2 text-sm font-semibold">
                          <Icon className="h-4 w-4 text-muted-foreground" />
                          {COMPONENT_GROUP_LABELS[componentGroup]}
                        </h3>
                        <p className="text-muted-foreground mt-1 text-xs">
                          {COMPONENT_GROUP_DESCRIPTIONS[componentGroup]}
                        </p>
                      </div>
                      <Badge variant="outline">{typedComponents.length}</Badge>
                    </div>
                    {typedComponents.length === 0 ? (
                      <div className="text-muted-foreground rounded-md border border-dashed px-3 py-4 text-center text-xs">
                        暂无{COMPONENT_GROUP_LABELS[componentGroup]}组件
                      </div>
                    ) : (
                      <div className="space-y-2">
                        {typedComponents.map((component) => {
                          const schemaProperties = getSchemaPropertyNames(component.parameters_schema)
                          return (
                            <div key={`${component.component_type}-${component.name}`} className="rounded-md border p-3">
                              <div className="mb-2 flex items-start justify-between gap-2">
                                <div className="min-w-0">
                                  <div className="break-words text-sm font-medium">{component.name}</div>
                                  {component.description && (
                                    <p className="text-muted-foreground mt-1 line-clamp-3 text-xs">
                                      {component.description}
                                    </p>
                                  )}
                                </div>
                                <Badge variant={component.enabled ? 'default' : 'secondary'} className="shrink-0">
                                  {component.enabled ? '启用' : '禁用'}
                                </Badge>
                              </div>
                              <Badge variant="outline" className="mb-2 text-[0.68rem]">
                                {COMPONENT_TYPE_LABELS[component.component_type]}
                              </Badge>

                              {component.component_type === 'action' && (
                                <div className="text-muted-foreground space-y-1 text-xs">
                                  {component.activation_type && <div>触发方式：{component.activation_type}</div>}
                                  {component.activation_keywords && component.activation_keywords.length > 0 && (
                                    <div className="flex flex-wrap gap-1">
                                      {component.activation_keywords.map((keyword) => (
                                        <Badge key={keyword} variant="outline" className="text-[0.68rem]">
                                          {keyword}
                                        </Badge>
                                      ))}
                                    </div>
                                  )}
                                </div>
                              )}

                              {component.component_type === 'tool' && schemaProperties.length > 0 && (
                                <div className="mt-2 flex flex-wrap gap-1">
                                  {schemaProperties.map((propertyName) => (
                                    <Badge key={propertyName} variant="outline" className="text-[0.68rem]">
                                      {propertyName}
                                    </Badge>
                                  ))}
                                </div>
                              )}
                            </div>
                          )
                        })}
                      </div>
                    )}
                  </section>
                )
              })}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

type PluginDocumentMode = 'readme' | 'changelog'

interface PluginDocumentPanelPosition {
  left: number
  top: number
}

const DOCUMENT_PANEL_WIDTH = 560
const DOCUMENT_PANEL_HEIGHT = 620
const DOCUMENT_PANEL_MARGIN = 16

function clampPanelValue(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max)
}

function getInitialDocumentPanelPosition(): PluginDocumentPanelPosition {
  if (typeof window === 'undefined') {
    return { left: 320, top: 120 }
  }

  return {
    left: Math.max(DOCUMENT_PANEL_MARGIN, window.innerWidth - DOCUMENT_PANEL_WIDTH - 32),
    top: 112,
  }
}

interface PluginDocumentFloatingPanelProps {
  plugin: InstalledPlugin
  onClose: () => void
}

function PluginDocumentFloatingPanel({ plugin, onClose }: PluginDocumentFloatingPanelProps) {
  const [mode, setMode] = useState<PluginDocumentMode>('readme')
  const [readme, setReadme] = useState('')
  const [changelog, setChangelog] = useState(plugin.changelog ?? '')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [dragging, setDragging] = useState(false)
  const [position, setPosition] = useState<PluginDocumentPanelPosition>(getInitialDocumentPanelPosition)
  const panelRef = useRef<HTMLDivElement | null>(null)
  const dragRef = useRef<{
    pointerId?: number
    offsetX: number
    offsetY: number
  } | null>(null)

  useEffect(() => {
    let cancelled = false

    async function loadDocument() {
      setLoading(true)
      setError('')
      try {
        if (mode === 'readme') {
          const content = await getLocalPluginReadme(plugin.id)
          if (!cancelled) {
            setReadme(content)
          }
          return
        }

        const localChangelog = plugin.changelog?.trim()
          ? plugin.changelog
          : await getLocalPluginChangelog(plugin.id)
        if (!cancelled) {
          setChangelog(localChangelog ?? '')
        }
      } catch (loadError) {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : '文档加载失败')
        }
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    }

    void loadDocument()

    return () => {
      cancelled = true
    }
  }, [mode, plugin.changelog, plugin.id])

  const movePanel = (clientX: number, clientY: number) => {
    const dragState = dragRef.current
    if (!dragState) {
      return
    }

    const panelRect = panelRef.current?.getBoundingClientRect()
    const panelWidth = panelRect?.width ?? DOCUMENT_PANEL_WIDTH
    const panelHeight = panelRect?.height ?? DOCUMENT_PANEL_HEIGHT
    const maxLeft = Math.max(
      DOCUMENT_PANEL_MARGIN,
      window.innerWidth - DOCUMENT_PANEL_MARGIN - panelWidth
    )
    const maxTop = Math.max(
      DOCUMENT_PANEL_MARGIN,
      window.innerHeight - DOCUMENT_PANEL_MARGIN - panelHeight
    )
    setPosition({
      left: clampPanelValue(clientX - dragState.offsetX, DOCUMENT_PANEL_MARGIN, maxLeft),
      top: clampPanelValue(clientY - dragState.offsetY, DOCUMENT_PANEL_MARGIN, maxTop),
    })
  }

  const startDrag = (clientX: number, clientY: number, pointerId?: number) => {
    const rect = panelRef.current?.getBoundingClientRect()
    if (!rect) {
      return
    }

    dragRef.current = {
      pointerId,
      offsetX: clientX - rect.left,
      offsetY: clientY - rect.top,
    }
    setDragging(true)
  }

  useEffect(() => {
    if (!dragging) {
      return
    }

    const handleMouseMove = (event: MouseEvent) => {
      if (dragRef.current?.pointerId !== undefined) {
        return
      }
      movePanel(event.clientX, event.clientY)
    }
    const handleMouseUp = () => {
      if (dragRef.current?.pointerId !== undefined) {
        return
      }
      dragRef.current = null
      setDragging(false)
    }

    window.addEventListener('mousemove', handleMouseMove)
    window.addEventListener('mouseup', handleMouseUp)
    return () => {
      window.removeEventListener('mousemove', handleMouseMove)
      window.removeEventListener('mouseup', handleMouseUp)
    }
  }, [dragging])

  const handlePointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0 || dragRef.current) {
      return
    }

    startDrag(event.clientX, event.clientY, event.pointerId)
    event.currentTarget.setPointerCapture(event.pointerId)
  }

  const handlePointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    const dragState = dragRef.current
    if (!dragState || dragState.pointerId !== event.pointerId) {
      return
    }

    movePanel(event.clientX, event.clientY)
  }

  const endDrag = (event: React.PointerEvent<HTMLDivElement>) => {
    if (dragRef.current?.pointerId === event.pointerId) {
      dragRef.current = null
      setDragging(false)
      if (event.currentTarget.hasPointerCapture(event.pointerId)) {
        event.currentTarget.releasePointerCapture(event.pointerId)
      }
    }
  }

  const handleMouseDown = (event: React.MouseEvent<HTMLDivElement>) => {
    if (event.button !== 0 || dragRef.current) {
      return
    }

    startDrag(event.clientX, event.clientY)
  }

  const content = mode === 'readme' ? readme : changelog
  const panelStyle = {
    left: position.left,
    top: position.top,
  } satisfies CSSProperties

  const panel = (
    <div
      ref={panelRef}
      data-dashboard-floating-content="true"
      className="fixed z-50 w-[min(calc(100vw-2rem),35rem)] overflow-hidden rounded-md border bg-background shadow-2xl"
      style={panelStyle}
    >
      <div
        className={`flex touch-none select-none items-center gap-2 border-b bg-muted/70 px-3 py-2 ${
          dragging ? 'cursor-grabbing' : 'cursor-grab'
        }`}
        onPointerCancel={endDrag}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={endDrag}
        onMouseDown={handleMouseDown}
      >
        <GripHorizontal className="text-muted-foreground h-4 w-4 shrink-0" />
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-semibold">插件文档</div>
          <div className="text-muted-foreground truncate text-xs">{plugin.manifest.name}</div>
        </div>
        <div
          className="flex shrink-0 items-center gap-1"
          onPointerDown={(event) => event.stopPropagation()}
        >
          <Button
            type="button"
            variant={mode === 'readme' ? 'default' : 'outline'}
            size="sm"
            className="h-8"
            onClick={() => setMode('readme')}
          >
            <BookOpen className="mr-1.5 h-3.5 w-3.5" />
            README
          </Button>
          <Button
            type="button"
            variant={mode === 'changelog' ? 'default' : 'outline'}
            size="sm"
            className="h-8"
            onClick={() => setMode('changelog')}
          >
            <FileText className="mr-1.5 h-3.5 w-3.5" />
            更新日志
          </Button>
          <Button type="button" variant="ghost" size="icon" className="h-8 w-8" onClick={onClose}>
            <X className="h-4 w-4" />
          </Button>
        </div>
      </div>

      <div className="p-3">
        {loading ? (
          <div className="text-muted-foreground flex h-64 items-center justify-center gap-2 text-sm">
            <Loader2 className="h-4 w-4 animate-spin" />
            正在加载文档
          </div>
        ) : error ? (
          <Alert variant="destructive">
            <AlertCircle className="h-4 w-4" />
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : content ? (
          <ScrollArea className="h-[min(62vh,31rem)] pr-4">
            <MarkdownRenderer content={content} />
          </ScrollArea>
        ) : (
          <div className="text-muted-foreground flex h-64 items-center justify-center rounded-md border border-dashed text-sm">
            {mode === 'readme' ? '暂无 README' : '暂无更新日志'}
          </div>
        )}
      </div>
    </div>
  )

  if (typeof document === 'undefined') {
    return panel
  }

  return createPortal(panel, document.body)
}

// 插件配置编辑器
interface PluginConfigEditorProps {
  plugin: InstalledPlugin
  onBack: () => void
  initialTab?: string
}

function PluginConfigEditor({ plugin, onBack, initialTab }: PluginConfigEditorProps) {
  const { i18n } = useTranslation()
  const language = i18n.resolvedLanguage || i18n.language || 'zh'
  const [documentPanelOpen, setDocumentPanelOpen] = useState(false)

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
      <div className="flex h-64 items-center justify-center">
        <Loader2 className="text-muted-foreground h-8 w-8 animate-spin" />
      </div>
    )
  }

  if (!schema) {
    return (
      <div className="flex h-64 flex-col items-center justify-center space-y-4">
        <AlertCircle className="text-muted-foreground h-12 w-12" />
        <p className="text-muted-foreground">无法加载配置</p>
        <Button onClick={onBack} variant="outline">
          <ArrowLeft className="mr-2 h-4 w-4" />
          返回
        </Button>
      </div>
    )
  }

  // 按 order 排序 sections
  const sortedSections = Object.entries(schema.sections).sort(
    ([, a], [, b]) => (a.order ?? 0) - (b.order ?? 0)
  )
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
    'name'
  )
  const manifestUrls = plugin.manifest.urls as
    | {
        repository?: string
        homepage?: string
        documentation?: string
        issues?: string
      }
    | undefined
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
  ].filter(
    (item): item is { label: string; value: string } =>
      typeof item.value === 'string' && item.value.trim().length > 0
  )

  return (
    <div className="space-y-4 sm:space-y-6">
      {/* 头部 */}
      <div
        className="sticky top-0 z-40 -mx-5 flex items-center justify-between gap-3 overflow-x-auto border-b px-5 py-2.5 shadow-sm backdrop-blur sm:-mx-7 sm:px-7 lg:-mx-8 lg:px-8"
        style={{ backgroundColor: 'hsl(var(--background) / 0.96)' }}
      >
        <div className="flex min-w-0 items-center gap-2">
          <Button variant="ghost" size="icon" className="h-8 w-8 shrink-0" onClick={handleBack}>
            <ArrowLeft className="h-4 w-4" />
          </Button>
          <div className="flex min-w-0 items-center gap-2">
            <h1 className="min-w-0 truncate text-lg font-semibold sm:text-xl" data-plugin-config-title>
              {pluginName}
            </h1>
            <Badge variant={isEnabled ? 'default' : 'secondary'} className="shrink-0">
              {isEnabled ? '已启用' : '已禁用'}
            </Badge>
            <span className="text-muted-foreground shrink-0 text-sm">
              v{schema.plugin_info.version || plugin.manifest.version}
            </span>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2 whitespace-nowrap sm:gap-3">
          <Button
            variant="outline"
            size="sm"
            className="h-8"
            onClick={() => setDocumentPanelOpen(true)}
          >
            <BookOpen className="mr-2 h-4 w-4" />
            打开文档
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="h-8"
            onClick={() => setEditMode(editMode === 'visual' ? 'source' : 'visual')}
          >
            {editMode === 'visual' ? (
              <>
                <Code2 className="mr-2 h-4 w-4" />
                源代码
              </>
            ) : (
              <>
                <Layout className="mr-2 h-4 w-4" />
                可视化
              </>
            )}
          </Button>
          <div
            data-dashboard-input="true"
            className="border-input flex h-8 items-center gap-2 rounded-md border bg-transparent px-2 text-sm font-medium shadow-sm"
          >
            <Switch
              checked={isEnabled}
              onCheckedChange={() => void handleToggle()}
              aria-label={isEnabled ? '禁用插件' : '启用插件'}
            />
            <span className="text-xs">启用</span>
          </div>
          <Button
            variant="outline"
            size="sm"
            className="h-8"
            onClick={() => setResetDialogOpen(true)}
          >
            <RotateCcw className="mr-2 h-4 w-4" />
            重置
          </Button>
          <Button size="sm" className="h-8" onClick={handleSave} disabled={!hasChanges || saving}>
            {saving ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <Save className="mr-2 h-4 w-4" />
            )}
            保存
          </Button>
        </div>
      </div>

      {/* 未保存提示 */}
      {hasChanges && (
        <Card className="border-orange-200 bg-orange-50 dark:border-orange-900 dark:bg-orange-950/20">
          <CardContent className="py-3!">
            <div className="flex items-center gap-2">
              <Info className="h-4 w-4 text-orange-600" />
              <p className="text-sm text-orange-800 dark:text-orange-200">有未保存的更改</p>
            </div>
          </CardContent>
        </Card>
      )}

      <Tabs
        value={pluginPageTab}
        onValueChange={(value) => setPluginPageTab(value as 'settings' | 'details')}
      >
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
                  <strong>文件模式：</strong>直接编辑原始配置文件。此功能仅适用于熟悉
                  TOML语法的用户。只有格式完全正确才能保存。
                  {hasTomlError && (
                    <span className="text-destructive ml-2 font-semibold">
                      ⚠️ 上次保存失败，请检查 TOML 格式
                    </span>
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
                    {schemaTabs.map((tab) => (
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
                  {schemaTabs.map((tab) => (
                    <TabsContent key={tab.id} value={tab.id} className="mt-4 space-y-4">
                      {tab.sections.map((sectionName) => {
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
          <PluginDetailsPanel
            plugin={plugin}
            description={plugin.manifest.description || ''}
            detailItems={pluginDetailItems}
            homepageUrl={pluginHomepageUrl}
            repositoryUrl={pluginRepositoryUrl}
            documentationUrl={manifestUrls?.documentation}
            issuesUrl={manifestUrls?.issues}
            changelog={plugin.changelog}
          />
        </TabsContent>
      </Tabs>

      {documentPanelOpen && (
        <PluginDocumentFloatingPanel
          plugin={plugin}
          onClose={() => setDocumentPanelOpen(false)}
        />
      )}

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
            <DialogDescription>当前插件配置文件有修改，离开页面前是否保存？</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={closeLeavePrompt} disabled={saving}>
              取消
            </Button>
            <Button variant="outline" onClick={leaveWithoutSaving} disabled={saving}>
              不保存
            </Button>
            <Button onClick={saveAndLeave} disabled={saving}>
              {saving ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <Save className="mr-2 h-4 w-4" />
              )}
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
  const { triggerRestart, isRestarting } = useRestart()

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
    isPluginLoadFailed,
    isPluginVersionIncompatible,
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
  const [loadFailureDetailPlugin, setLoadFailureDetailPlugin] = useState<InstalledPlugin | null>(null)

  // 如果选中了插件，显示配置编辑器
  if (selectedPlugin) {
    return (
      <>
        <ScrollArea className="h-full">
          <div className="px-5 pt-0 pb-4 sm:px-7 sm:pb-6 lg:px-8">
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
    <>
      <ScrollArea className="h-full">
      <div className="space-y-4 p-4 sm:space-y-6 sm:p-6">
        <div className="flex flex-nowrap items-center gap-2 sm:gap-3">
          <div className="relative min-w-0 flex-1 basis-0 sm:basis-72">
            <Search className="text-muted-foreground absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2" />
            <Input
              placeholder="搜索插件..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="pl-9"
            />
          </div>
          <div
            data-dashboard-input="true"
            className="border-input flex h-9 shrink-0 items-center gap-1.5 rounded-md border bg-transparent px-2 py-1 text-sm font-medium whitespace-nowrap shadow-sm transition-colors sm:gap-2 sm:px-3"
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
          <Button
            variant="outline"
            size="sm"
            className="h-9 shrink-0 px-2 sm:px-3"
            onClick={() => triggerRestart()}
            disabled={isRestarting}
            title="重启麦麦"
          >
            <RotateCw className={`h-4 w-4 ${isRestarting ? 'animate-spin' : ''} sm:mr-2`} />
            <span className="hidden sm:inline">重启麦麦</span>
          </Button>
        </div>

        {/* 统计信息 */}
        {isModernDashboardStyle ? (
          <Card>
            <CardContent className="space-y-3 p-4!">
              <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
                <span className="flex items-center gap-2">
                  <Package className="text-muted-foreground h-4 w-4" />
                  已安装 <strong>{installedCount}</strong> 个插件
                </span>
                <span>
                  已启用 <strong className="text-emerald-600">{enabledCount}</strong> 个
                </span>
                <span>
                  已禁用 <strong className="text-muted-foreground">{disabledCount}</strong> 个
                </span>
                <span>
                  加载中 <strong className="text-sky-600">{loadingCount}</strong> 个
                </span>
                {showsCircuitSummary && (
                  <span>
                    熔断中 <strong className="text-orange-600">{circuitOpenCount}</strong> 个
                  </span>
                )}
              </div>
              <div
                className="flex items-center gap-3 border-t pt-3 text-sm"
                aria-label={modernLoadSummaryLabel}
              >
                <span className="sr-only">{modernLoadSummaryLabel}</span>
                <strong className="w-8 text-right text-emerald-600">{loadSuccessCount}</strong>
                <div
                  className="bg-muted flex h-3 min-w-28 flex-1 overflow-hidden"
                  aria-hidden="true"
                >
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
            <div className="space-y-2" aria-label={futureRetroPluginSummaryLabel}>
              <span className="sr-only">{futureRetroPluginSummaryLabel}</span>
              <div className="bg-muted flex h-3 w-full overflow-hidden" aria-hidden="true">
                {plugins.length > 0 ? (
                  plugins.map((plugin, index) => (
                    <div
                      key={`${plugin.id}-${index}`}
                      className={`min-w-0 flex-1 ${getPluginStatusBarClassName(plugin)} ${
                        index < plugins.length - 1 ? 'border-background border-r' : ''
                      }`}
                      title={`${plugin.manifest.name}：${getPluginStatusLabel(plugin)}`}
                    />
                  ))
                ) : (
                  <div className="bg-muted-foreground/20 h-full flex-1" />
                )}
              </div>
              <div className="text-muted-foreground flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
                <span className="flex items-center gap-1.5">
                  插件 <strong className="text-foreground">{installedCount}</strong>个
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="h-2 w-2 rounded-full bg-emerald-500" />
                  启用 <strong className="text-emerald-600">{enabledCount}</strong> 个
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="bg-muted-foreground/45 h-2 w-2 rounded-full" />
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
            <Loader2 className="text-muted-foreground h-8 w-8 animate-spin" />
          </div>
        ) : visiblePlugins.length === 0 ? (
          <div className="flex flex-col items-center justify-center space-y-4 py-12">
            <Package className="text-muted-foreground/50 h-16 w-16" />
            <div className="space-y-2 text-center">
              <p className="text-muted-foreground text-lg font-medium">
                {showUpdateOnly
                  ? '暂无可更新插件'
                  : searchQuery
                    ? '没有找到匹配的插件'
                    : '暂无已安装的插件'}
              </p>
              <p className="text-muted-foreground text-sm">
                {showUpdateOnly
                  ? '当前已安装插件没有发现新版本'
                  : searchQuery
                    ? '尝试其他搜索关键词'
                    : '前往插件市场安装插件'}
              </p>
            </div>
          </div>
        ) : (
          <div className="divide-border/80 divide-y">
            {visiblePlugins.map((plugin) => {
              const pluginActing = actingPluginId === plugin.id
              const pluginDisabled = isPluginDisabled(plugin)
              const updateState = getPluginUpdateState(plugin)
              const pluginLoadFailed = isPluginLoadFailed(plugin)
              const pluginVersionIncompatible = isPluginVersionIncompatible(plugin)
              const pluginNeedsUpdate = pluginVersionIncompatible && updateState.hasUpdate
              const baseStatusMeta = getPluginStatusMeta(plugin)
              const statusMeta = pluginVersionIncompatible
                ? {
                    ...baseStatusMeta,
                    label: pluginNeedsUpdate ? '需要更新' : '版本不兼容',
                  }
                : baseStatusMeta
              const loadFailureReason = plugin.load_error?.trim() || '运行时未返回具体失败原因'
              const loadFailureTitle = pluginNeedsUpdate
                ? '当前插件版本需要更新'
                : pluginVersionIncompatible
                  ? '当前插件版本已不兼容'
                  : '插件加载失败'
              const loadFailureDescription = pluginVersionIncompatible
                ? checkingUpdates
                  ? `已安装 v${plugin.manifest.version} 与当前麦麦版本不兼容，正在检查插件市场更新…`
                  : pluginNeedsUpdate
                    ? `已安装 v${plugin.manifest.version}，插件市场已有 v${updateState.latestVersion}，请更新后重试。`
                    : `已安装 v${plugin.manifest.version} 与当前麦麦版本不兼容，请前往插件市场查看兼容版本。`
                : `失败原因：${loadFailureReason}`
              return (
                <div
                  key={plugin.id}
                  data-plugin-list-item="true"
                  className={`hover:bg-muted/55 focus-visible:bg-muted/55 relative flex cursor-pointer flex-col justify-between gap-2 py-2.5 transition-all duration-150 ease-out hover:-translate-y-0.5 hover:shadow-md focus-visible:-translate-y-0.5 focus-visible:shadow-md focus-visible:outline-none sm:min-h-0 sm:flex-row sm:items-center sm:gap-3 sm:px-2 sm:py-3 ${
                    isPluginDisabled(plugin) ? 'opacity-70' : ''
                  }`}
                  role="button"
                  tabIndex={0}
                  onClick={() => openPluginConfig(plugin)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault()
                      openPluginConfig(plugin)
                    }
                  }}
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
                      <PluginIcon
                        pluginId={plugin.id}
                        manifest={plugin.manifest}
                        installed
                        className="h-12 w-12 sm:h-10 sm:w-10"
                      />
                      <span className="text-muted-foreground text-[0.65rem] leading-none">
                        v{plugin.manifest.version}
                      </span>
                    </div>
                    <div className="min-w-0 flex-1 space-y-2 sm:space-y-1">
                      <div className="flex min-w-0 flex-wrap items-center gap-2">
                        <h3 className="min-w-0 text-sm leading-snug font-medium break-words sm:truncate sm:text-base">
                          {plugin.manifest.name}
                        </h3>
                        <Badge variant="outline" className="flex-shrink-0 text-xs">
                          {getPluginTypeLabel(plugin)}
                        </Badge>
                        {statusMeta.showsBadge !== false && (
                          <Badge
                            variant="outline"
                            className={`flex-shrink-0 gap-1 text-xs ${statusMeta.badgeClassName ?? ''}`}
                          >
                            {statusMeta.icon === 'loading' && (
                              <Loader2 className="h-3 w-3 animate-spin" />
                            )}
                            {statusMeta.icon === 'warning' && <AlertCircle className="h-3 w-3" />}
                            {statusMeta.icon === 'circuit' && <AlertTriangle className="h-3 w-3" />}
                            {statusMeta.label}
                          </Badge>
                        )}
                      </div>
                      <p className="text-muted-foreground line-clamp-2 text-sm leading-relaxed sm:truncate sm:leading-normal">
                        {plugin.manifest.description || '暂无描述'}
                      </p>
                      {pluginLoadFailed && (
                        <div className="flex min-w-0 flex-col gap-2 rounded-md border border-red-200 bg-red-50/80 px-3 py-2 text-xs text-red-700 dark:border-red-900 dark:bg-red-950/25 dark:text-red-300 sm:flex-row sm:items-center">
                          <div className="flex min-w-0 flex-1 items-start gap-1.5">
                            <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                            <div className="min-w-0 space-y-0.5">
                              <div className="text-sm font-medium">{loadFailureTitle}</div>
                              <div className="line-clamp-2 break-words">{loadFailureDescription}</div>
                            </div>
                          </div>
                          <div className="flex shrink-0 items-center gap-2">
                            {pluginNeedsUpdate && (
                              <Button
                                type="button"
                                size="sm"
                                className="h-7 px-2 text-xs"
                                onClick={(event) => openUpdatePluginDialog(plugin, event)}
                              >
                                立即更新
                              </Button>
                            )}
                            {pluginVersionIncompatible && !pluginNeedsUpdate && !checkingUpdates && (
                              <Button
                                asChild
                                variant="outline"
                                size="sm"
                                className="h-7 border-red-300 px-2 text-xs text-red-700 hover:bg-red-100 dark:border-red-800 dark:text-red-300 dark:hover:bg-red-950"
                              >
                                <a href="/plugins" onClick={(event) => event.stopPropagation()}>
                                  前往插件市场
                                </a>
                              </Button>
                            )}
                            <Button
                              type="button"
                              variant="outline"
                              size="sm"
                              className="h-7 border-red-300 px-2 text-xs text-red-700 hover:bg-red-100 dark:border-red-800 dark:text-red-300 dark:hover:bg-red-950"
                              onClick={(event) => {
                                event.stopPropagation()
                                setLoadFailureDetailPlugin(plugin)
                              }}
                            >
                              查看详情
                            </Button>
                          </div>
                        </div>
                      )}
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
                          className="ring-background absolute -top-1 -right-1 h-3 w-3 rounded-sm bg-yellow-400 ring-2"
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
                    <ChevronRight className="text-muted-foreground h-4 w-4" />
                  </div>
                </div>
              )
            })}
          </div>
        )}

        <Dialog
          open={loadFailureDetailPlugin !== null}
          onOpenChange={(open) => {
            if (!open) {
              setLoadFailureDetailPlugin(null)
            }
          }}
        >
          <DialogContent className="max-w-[min(92vw,44rem)]">
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2 text-red-600">
                <AlertCircle className="h-5 w-5" />
                插件加载失败详情
              </DialogTitle>
              <DialogDescription>
                {loadFailureDetailPlugin?.manifest.name || '插件'} 未能完成加载。
              </DialogDescription>
            </DialogHeader>

            {loadFailureDetailPlugin && (
              <div className="space-y-4">
                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="rounded-md border px-3 py-2">
                    <div className="text-muted-foreground text-xs font-medium">插件 ID</div>
                    <div className="mt-1 break-words text-sm">{loadFailureDetailPlugin.id}</div>
                  </div>
                  <div className="rounded-md border px-3 py-2">
                    <div className="text-muted-foreground text-xs font-medium">版本</div>
                    <div className="mt-1 text-sm">v{loadFailureDetailPlugin.manifest.version}</div>
                  </div>
                  <div className="rounded-md border px-3 py-2">
                    <div className="text-muted-foreground text-xs font-medium">加载状态</div>
                    <div className="mt-1 text-sm">{getPluginStatusLabel(loadFailureDetailPlugin)}</div>
                  </div>
                  <div className="rounded-md border px-3 py-2">
                    <div className="text-muted-foreground text-xs font-medium">安装路径</div>
                    <div className="mt-1 break-words text-sm">{loadFailureDetailPlugin.path}</div>
                  </div>
                </div>

                <div className="space-y-2">
                  <div className="text-sm font-medium">失败原因</div>
                  <ScrollArea className="max-h-[min(42vh,20rem)] rounded-md border bg-muted/30 p-3">
                    <pre className="whitespace-pre-wrap break-words text-sm leading-relaxed">
                      {loadFailureDetailPlugin.load_error?.trim() || '运行时未返回具体失败原因'}
                    </pre>
                  </ScrollArea>
                </div>
              </div>
            )}
          </DialogContent>
        </Dialog>

        <Dialog
          open={updateDialogOpen}
          onOpenChange={(open) => {
            if (!open) {
              closeUpdatePluginDialog()
              return
            }
            setUpdateDialogOpen(true)
          }}
        >
          <DialogContent
            preventOutsideClose={updateProgress?.stage === 'loading'}
            hideCloseButton={updateProgress?.stage === 'loading'}
          >
            <DialogHeader>
              <DialogTitle>确认更新插件</DialogTitle>
              <DialogDescription>
                {updatingPlugin
                  ? `即将更新 ${updatingPlugin.manifest.name}。更新过程中请保持麦麦运行。`
                  : '即将更新插件。更新过程中请保持麦麦运行。'}
              </DialogDescription>
            </DialogHeader>

            {updateProgress && (
              <div
                className={`space-y-3 border p-3 ${
                  updateProgress.stage === 'success'
                    ? 'border-green-200 bg-green-50 dark:border-green-900 dark:bg-green-950/20'
                    : updateProgress.stage === 'error'
                      ? 'border-red-200 bg-red-50 dark:border-red-900 dark:bg-red-950/20'
                      : 'bg-muted/50'
                }`}
              >
                <div className="flex items-center justify-between gap-3">
                  <div className="flex min-w-0 items-center gap-2">
                    {updateProgress.stage === 'loading' ? (
                      <Loader2 className="h-4 w-4 shrink-0 animate-spin" />
                    ) : updateProgress.stage === 'success' ? (
                      <Info className="h-4 w-4 shrink-0 text-green-600" />
                    ) : (
                      <Info className="h-4 w-4 shrink-0 text-red-600" />
                    )}
                    <span
                      className={`text-sm font-medium ${
                        updateProgress.stage === 'success'
                          ? 'text-green-700 dark:text-green-300'
                          : updateProgress.stage === 'error'
                            ? 'text-red-700 dark:text-red-300'
                            : ''
                      }`}
                    >
                      {updateProgress.stage === 'loading' && '正在更新'}
                      {updateProgress.stage === 'success' && '更新完成'}
                      {updateProgress.stage === 'error' && '更新失败'}
                    </span>
                  </div>
                  {updateProgress.stage !== 'error' && (
                    <span
                      className={`shrink-0 text-sm font-medium ${
                        updateProgress.stage === 'success'
                          ? 'text-green-700 dark:text-green-300'
                          : ''
                      }`}
                    >
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
                <p
                  className={`text-sm break-words ${
                    updateProgress.stage === 'success'
                      ? 'text-green-600 dark:text-green-400'
                      : updateProgress.stage === 'error'
                        ? 'text-red-600 dark:text-red-400'
                        : 'text-muted-foreground'
                  }`}
                >
                  {updateProgress.stage === 'error'
                    ? updateProgress.error || updateProgress.message || '更新失败'
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
                {updateProgress?.stage === 'success' || updateProgress?.stage === 'error'
                  ? '关闭'
                  : '取消'}
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

        <Dialog
          open={deleteDialogOpen}
          onOpenChange={(open) => {
            if (!open) {
              closeDeletePluginDialog()
              return
            }
            setDeleteDialogOpen(true)
          }}
        >
          <DialogContent
            preventOutsideClose={deleteProgress?.stage === 'loading'}
            hideCloseButton={deleteProgress?.stage === 'loading'}
          >
            <DialogHeader>
              <DialogTitle>确认删除插件</DialogTitle>
              <DialogDescription>
                {deletingPlugin
                  ? `即将删除 ${deletingPlugin.manifest.name}。删除后可从插件市场重新安装。`
                  : '即将删除插件。删除后可从插件市场重新安装。'}
              </DialogDescription>
            </DialogHeader>

            {deleteProgress && (
              <div
                className={`space-y-3 border p-3 ${
                  deleteProgress.stage === 'success'
                    ? 'border-green-200 bg-green-50 dark:border-green-900 dark:bg-green-950/20'
                    : deleteProgress.stage === 'error'
                      ? 'border-red-200 bg-red-50 dark:border-red-900 dark:bg-red-950/20'
                      : 'bg-muted/50'
                }`}
              >
                <div className="flex items-center justify-between gap-3">
                  <div className="flex min-w-0 items-center gap-2">
                    {deleteProgress.stage === 'loading' ? (
                      <Loader2 className="h-4 w-4 shrink-0 animate-spin" />
                    ) : deleteProgress.stage === 'success' ? (
                      <Info className="h-4 w-4 shrink-0 text-green-600" />
                    ) : (
                      <Info className="h-4 w-4 shrink-0 text-red-600" />
                    )}
                    <span
                      className={`text-sm font-medium ${
                        deleteProgress.stage === 'success'
                          ? 'text-green-700 dark:text-green-300'
                          : deleteProgress.stage === 'error'
                            ? 'text-red-700 dark:text-red-300'
                            : ''
                      }`}
                    >
                      {deleteProgress.stage === 'loading' && '正在删除'}
                      {deleteProgress.stage === 'success' && '删除完成'}
                      {deleteProgress.stage === 'error' && '删除失败'}
                    </span>
                  </div>
                  {deleteProgress.stage !== 'error' && (
                    <span
                      className={`shrink-0 text-sm font-medium ${
                        deleteProgress.stage === 'success'
                          ? 'text-green-700 dark:text-green-300'
                          : ''
                      }`}
                    >
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
                <p
                  className={`text-sm break-words ${
                    deleteProgress.stage === 'success'
                      ? 'text-green-600 dark:text-green-400'
                      : deleteProgress.stage === 'error'
                        ? 'text-red-600 dark:text-red-400'
                        : 'text-muted-foreground'
                  }`}
                >
                  {deleteProgress.stage === 'error'
                    ? deleteProgress.error || deleteProgress.message || '删除失败'
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
                {deleteProgress?.stage === 'success' || deleteProgress?.stage === 'error'
                  ? '关闭'
                  : '取消'}
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
      <RestartOverlay />
    </>
  )
}
