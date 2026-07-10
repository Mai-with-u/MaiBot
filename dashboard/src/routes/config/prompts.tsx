import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  CheckCircle2,
  Eye,
  GitCompareArrows,
  RefreshCw,
  Save,
  Search,
  SlidersHorizontal,
} from 'lucide-react'

import { CodeEditor } from '@/components/CodeEditor'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Separator } from '@/components/ui/separator'
import { ThinkingIllustration } from '@/components/ui/thinking-illustration'
import { useToast } from '@/hooks/use-toast'
import {
  activatePromptVersion,
  getDefaultPromptFile,
  getPromptCatalog,
  getPromptFile,
  getPromptVersionFile,
  resetPromptFile,
  updatePromptFile,
  type PromptCatalog,
  type PromptFileInfo,
  type PromptValidationResult,
  type PromptVersionInfo,
} from '@/lib/prompt-api'
import { cn } from '@/lib/utils'

const DEFAULT_VERSION_ID = '__default__'
const DIFF_ADDED_CLASS = 'cm-prompt-diff-added'
const DIFF_REMOVED_CLASS = 'cm-prompt-diff-removed'
const DIFF_ADDED_TEXT_CLASS = 'cm-prompt-diff-added-text'
const DIFF_REMOVED_TEXT_CLASS = 'cm-prompt-diff-removed-text'

interface PromptDiffState {
  defaultLineClasses: Record<number, string>
  currentLineClasses: Record<number, string>
  defaultRangeClasses: Array<{
    fromLine: number
    fromCh: number
    toLine: number
    toCh: number
    className: string
  }>
  currentRangeClasses: Array<{
    fromLine: number
    fromCh: number
    toLine: number
    toCh: number
    className: string
  }>
  added: number
  removed: number
  changed: number
}

function formatFileSize(size: number) {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

function splitPromptLines(value: string) {
  return value.length === 0 ? [''] : value.split('\n')
}

function addPromptDiffRange(
  ranges: PromptDiffState['defaultRangeClasses'],
  line: number,
  fromCh: number,
  toCh: number,
  className: string
) {
  if (toCh <= fromCh) return
  const previousRange = ranges[ranges.length - 1]
  if (
    previousRange &&
    previousRange.fromLine === line &&
    previousRange.toLine === line &&
    previousRange.toCh === fromCh &&
    previousRange.className === className
  ) {
    previousRange.toCh = toCh
    return
  }
  ranges.push({ fromLine: line, fromCh, toLine: line, toCh, className })
}

function addInlineDiffRanges(
  defaultLine: string,
  currentLine: string,
  defaultLineNumber: number,
  currentLineNumber: number,
  defaultRanges: PromptDiffState['defaultRangeClasses'],
  currentRanges: PromptDiffState['currentRangeClasses']
) {
  const defaultChars = defaultLine.split('')
  const currentChars = currentLine.split('')
  const dp = Array.from({ length: defaultChars.length + 1 }, () =>
    Array<number>(currentChars.length + 1).fill(0)
  )

  for (let i = defaultChars.length - 1; i >= 0; i -= 1) {
    for (let j = currentChars.length - 1; j >= 0; j -= 1) {
      dp[i][j] =
        defaultChars[i] === currentChars[j]
          ? dp[i + 1][j + 1] + 1
          : Math.max(dp[i + 1][j], dp[i][j + 1])
    }
  }

  let i = 0
  let j = 0
  while (i < defaultChars.length || j < currentChars.length) {
    if (i < defaultChars.length && j < currentChars.length && defaultChars[i] === currentChars[j]) {
      i += 1
      j += 1
      continue
    }

    const canRemove = i < defaultChars.length
    const canAdd = j < currentChars.length
    const removeScore = canRemove ? dp[i + 1][j] : -1
    const addScore = canAdd ? dp[i][j + 1] : -1

    if (canRemove && canAdd && removeScore === addScore) {
      addPromptDiffRange(defaultRanges, defaultLineNumber, i, i + 1, DIFF_REMOVED_TEXT_CLASS)
      addPromptDiffRange(currentRanges, currentLineNumber, j, j + 1, DIFF_ADDED_TEXT_CLASS)
      i += 1
      j += 1
    } else if (canRemove && removeScore >= addScore) {
      addPromptDiffRange(defaultRanges, defaultLineNumber, i, i + 1, DIFF_REMOVED_TEXT_CLASS)
      i += 1
    } else if (canAdd) {
      addPromptDiffRange(currentRanges, currentLineNumber, j, j + 1, DIFF_ADDED_TEXT_CLASS)
      j += 1
    }
  }
}

function buildPromptDiff(defaultContent: string, currentContent: string): PromptDiffState {
  const defaultLines = splitPromptLines(defaultContent)
  const currentLines = splitPromptLines(currentContent)
  const dp = Array.from({ length: defaultLines.length + 1 }, () =>
    Array<number>(currentLines.length + 1).fill(0)
  )

  for (let i = defaultLines.length - 1; i >= 0; i -= 1) {
    for (let j = currentLines.length - 1; j >= 0; j -= 1) {
      dp[i][j] =
        defaultLines[i] === currentLines[j]
          ? dp[i + 1][j + 1] + 1
          : Math.max(dp[i + 1][j], dp[i][j + 1])
    }
  }

  const defaultLineClasses: Record<number, string> = {}
  const currentLineClasses: Record<number, string> = {}
  const defaultRangeClasses: PromptDiffState['defaultRangeClasses'] = []
  const currentRangeClasses: PromptDiffState['currentRangeClasses'] = []
  let added = 0
  let removed = 0
  let changed = 0
  let i = 0
  let j = 0

  while (i < defaultLines.length || j < currentLines.length) {
    if (i < defaultLines.length && j < currentLines.length && defaultLines[i] === currentLines[j]) {
      i += 1
      j += 1
      continue
    }

    const canRemove = i < defaultLines.length
    const canAdd = j < currentLines.length
    const removeScore = canRemove ? dp[i + 1][j] : -1
    const addScore = canAdd ? dp[i][j + 1] : -1

    if (canRemove && canAdd && removeScore === addScore) {
      defaultLineClasses[i + 1] = DIFF_REMOVED_CLASS
      currentLineClasses[j + 1] = DIFF_ADDED_CLASS
      addInlineDiffRanges(
        defaultLines[i],
        currentLines[j],
        i + 1,
        j + 1,
        defaultRangeClasses,
        currentRangeClasses
      )
      changed += 1
      i += 1
      j += 1
    } else if (canRemove && removeScore >= addScore) {
      defaultLineClasses[i + 1] = DIFF_REMOVED_CLASS
      addPromptDiffRange(defaultRangeClasses, i + 1, 0, defaultLines[i].length, DIFF_REMOVED_TEXT_CLASS)
      removed += 1
      i += 1
    } else if (canAdd) {
      currentLineClasses[j + 1] = DIFF_ADDED_CLASS
      addPromptDiffRange(currentRangeClasses, j + 1, 0, currentLines[j].length, DIFF_ADDED_TEXT_CLASS)
      added += 1
      j += 1
    }
  }

  return {
    defaultLineClasses,
    currentLineClasses,
    defaultRangeClasses,
    currentRangeClasses,
    added,
    removed,
    changed,
  }
}

export function PromptManagementPage() {
  const { toast } = useToast()
  const [catalog, setCatalog] = useState<PromptCatalog | null>(null)
  const [language, setLanguage] = useState('zh-CN')
  const [filename, setFilename] = useState('')
  const [content, setContent] = useState('')
  const [savedContent, setSavedContent] = useState('')
  const [loadingCatalog, setLoadingCatalog] = useState(true)
  const [loadingFile, setLoadingFile] = useState(false)
  const [saving, setSaving] = useState(false)
  const [applyingVersion, setApplyingVersion] = useState(false)
  const [loadingDefaultPrompt, setLoadingDefaultPrompt] = useState(false)
  const [defaultPromptOpen, setDefaultPromptOpen] = useState(false)
  const [defaultPromptContent, setDefaultPromptContent] = useState('')
  const [diffMode, setDiffMode] = useState(false)
  const [loadingDiffDefault, setLoadingDiffDefault] = useState(false)
  const [diffDefaultContent, setDiffDefaultContent] = useState('')
  const [query, setQuery] = useState('')
  const [showAdvancedPrompts, setShowAdvancedPrompts] = useState(false)
  const [versions, setVersions] = useState<PromptVersionInfo[]>([])
  const [activeVersionId, setActiveVersionId] = useState<string | null>(null)
  const [selectedVersionId, setSelectedVersionId] = useState(DEFAULT_VERSION_ID)
  const [validation, setValidation] = useState<PromptValidationResult | null>(null)
  const diffModeRef = useRef(diffMode)

  const hasUnsavedChanges = content !== savedContent

  const promptFiles = useMemo<PromptFileInfo[]>(() => {
    if (!catalog || !language) return []
    return catalog.files[language] ?? []
  }, [catalog, language])

  const visiblePromptFiles = useMemo<PromptFileInfo[]>(() => {
    return showAdvancedPrompts ? promptFiles : promptFiles.filter((file) => !file.advanced)
  }, [promptFiles, showAdvancedPrompts])

  const filteredFiles = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase()
    if (!normalizedQuery) return visiblePromptFiles
    return visiblePromptFiles.filter((file) => {
      const searchableText = [file.name, file.display_name, file.description]
        .join(' ')
        .toLowerCase()
      return searchableText.includes(normalizedQuery)
    })
  }, [visiblePromptFiles, query])

  const selectedFile = promptFiles.find((file) => file.name === filename)
  const isCustomized = selectedFile?.customized ?? false
  const selectedCustomVersion = selectedVersionId !== DEFAULT_VERSION_ID
  const selectedVersionIsApplied = selectedCustomVersion
    ? selectedVersionId === activeVersionId
    : !isCustomized
  const canApplySelectedVersion = !selectedVersionIsApplied && !hasUnsavedChanges
  const selectedVersionStorageKey =
    language && filename ? `maibot.promptManagement.selectedVersion.${language}/${filename}` : ''
  const diffState = useMemo(
    () => buildPromptDiff(diffDefaultContent, content),
    [content, diffDefaultContent]
  )

  const applyPromptContent = useCallback(
    (
      result: {
        content: string
        versions: PromptVersionInfo[]
        active_version_id: string | null
        validation: PromptValidationResult
      },
      nextSelectedVersionId?: string
    ) => {
      setContent(result.content)
      setSavedContent(result.content)
      setVersions(result.versions ?? [])
      setActiveVersionId(result.active_version_id)
      setSelectedVersionId(
        nextSelectedVersionId ?? result.active_version_id ?? DEFAULT_VERSION_ID
      )
      setValidation(result.validation ?? null)
    },
    []
  )

  useEffect(() => {
    diffModeRef.current = diffMode
  }, [diffMode])

  useEffect(() => {
    if (!filename || showAdvancedPrompts) return
    const currentFile = promptFiles.find((file) => file.name === filename)
    if (!currentFile?.advanced) return
    setFilename(visiblePromptFiles[0]?.name ?? '')
  }, [filename, promptFiles, showAdvancedPrompts, visiblePromptFiles])

  const loadCatalog = useCallback(async () => {
    try {
      setLoadingCatalog(true)
      const result = await getPromptCatalog()
      setCatalog(result)
      const nextLanguage =
        language && result.languages.includes(language)
          ? language
          : result.languages.includes('zh-CN')
            ? 'zh-CN'
            : (result.languages[0] ?? '')
      setLanguage(nextLanguage)

      const nextFiles = nextLanguage ? (result.files[nextLanguage] ?? []) : []
      const nextBasicFiles = nextFiles.filter((file) => !file.advanced)
      setFilename((current) =>
        nextFiles.some((file) => file.name === current)
          ? current
          : (nextBasicFiles[0]?.name ?? nextFiles[0]?.name ?? '')
      )
    } catch (error) {
      toast({
        title: '加载 Prompt 目录失败',
        description: (error as Error).message,
        variant: 'destructive',
      })
    } finally {
      setLoadingCatalog(false)
    }
  }, [language, toast])

  useEffect(() => {
    void loadCatalog()
  }, [loadCatalog])

  useEffect(() => {
    if (!language || !filename) {
      setContent('')
      setSavedContent('')
      setVersions([])
      setActiveVersionId(null)
      setSelectedVersionId(DEFAULT_VERSION_ID)
      setValidation(null)
      setDiffDefaultContent('')
      return
    }

    let cancelled = false
    const loadFile = async () => {
      try {
        setLoadingFile(true)
        const result = await getPromptFile(language, filename)
        if (cancelled) return
        const persistedVersionId = selectedVersionStorageKey
          ? localStorage.getItem(selectedVersionStorageKey)
          : null
        const nextVersionId =
          persistedVersionId === DEFAULT_VERSION_ID ||
          result.versions.some((version) => version.id === persistedVersionId)
            ? persistedVersionId
            : null

        let promptContentResult = result
        if (nextVersionId && nextVersionId !== result.active_version_id) {
          promptContentResult =
            nextVersionId === DEFAULT_VERSION_ID
              ? await getDefaultPromptFile(language, filename)
              : await getPromptVersionFile(language, filename, nextVersionId)
          if (cancelled) return
        }

        if (diffModeRef.current) {
          const defaultResult =
            nextVersionId === DEFAULT_VERSION_ID
              ? promptContentResult
              : await getDefaultPromptFile(language, filename)
          if (cancelled) return
          setDiffDefaultContent(defaultResult.content)
        } else {
          setDiffDefaultContent('')
        }

        applyPromptContent(promptContentResult, nextVersionId ?? undefined)
      } catch (error) {
        if (!cancelled) {
          toast({
            title: '读取 Prompt 失败',
            description: (error as Error).message,
            variant: 'destructive',
          })
        }
      } finally {
        if (!cancelled) {
          setLoadingFile(false)
        }
      }
    }

    void loadFile()
    return () => {
      cancelled = true
    }
  }, [applyPromptContent, filename, language, selectedVersionStorageKey, toast])

  const handleLanguageChange = (nextLanguage: string) => {
    setLanguage(nextLanguage)
    setQuery('')
    const nextFiles = catalog?.files[nextLanguage] ?? []
    const nextVisibleFiles = showAdvancedPrompts
      ? nextFiles
      : nextFiles.filter((file) => !file.advanced)
    setFilename(nextVisibleFiles[0]?.name ?? '')
  }

  const handleSave = async () => {
    if (!language || !filename) return

    try {
      setSaving(true)
      const result = await updatePromptFile(language, filename, content, {
        versionId: selectedCustomVersion ? selectedVersionId : null,
        createVersion: !selectedCustomVersion,
      })
      applyPromptContent(result, result.active_version_id ?? DEFAULT_VERSION_ID)
      if (selectedVersionStorageKey) {
        localStorage.setItem(selectedVersionStorageKey, result.active_version_id ?? DEFAULT_VERSION_ID)
      }
      toast({ title: 'Prompt 已保存', description: `${language}/${filename}` })
      void loadCatalog()
    } catch (error) {
      toast({
        title: '保存 Prompt 失败',
        description: (error as Error).message,
        variant: 'destructive',
      })
    } finally {
      setSaving(false)
    }
  }

  const handleVersionChange = async (nextVersionId: string) => {
    if (!language || !filename) return
    if (hasUnsavedChanges) {
      toast({
        title: '当前 Prompt 有未保存修改',
        description: '请先保存或放弃当前修改后再切换版本。',
        variant: 'destructive',
      })
      return
    }

    try {
      setLoadingFile(true)
      const result =
        nextVersionId === DEFAULT_VERSION_ID
          ? await getDefaultPromptFile(language, filename)
          : await getPromptVersionFile(language, filename, nextVersionId)
      applyPromptContent(result, nextVersionId)
      if (selectedVersionStorageKey) {
        localStorage.setItem(selectedVersionStorageKey, nextVersionId)
      }
    } catch (error) {
      toast({
        title: '切换 Prompt 版本失败',
        description: (error as Error).message,
        variant: 'destructive',
      })
    } finally {
      setLoadingFile(false)
    }
  }

  const handleApplySelectedVersion = async () => {
    if (!language || !filename) return

    try {
      setApplyingVersion(true)
      const result =
        selectedVersionId === DEFAULT_VERSION_ID
          ? await resetPromptFile(language, filename)
          : await activatePromptVersion(language, filename, selectedVersionId)
      applyPromptContent(result, result.active_version_id ?? selectedVersionId)
      if (selectedVersionStorageKey) {
        localStorage.setItem(selectedVersionStorageKey, result.active_version_id ?? selectedVersionId)
      }
      toast({ title: '已应用 Prompt 版本', description: `${language}/${filename}` })
      void loadCatalog()
    } catch (error) {
      toast({
        title: '应用 Prompt 版本失败',
        description: (error as Error).message,
        variant: 'destructive',
      })
    } finally {
      setApplyingVersion(false)
    }
  }

  const handleShowDefault = async () => {
    if (!language || !filename) return

    try {
      setLoadingDefaultPrompt(true)
      setDefaultPromptOpen(true)
      const result = await getDefaultPromptFile(language, filename)
      setDefaultPromptContent(result.content)
    } catch (error) {
      toast({
        title: '读取默认 Prompt 失败',
        description: (error as Error).message,
        variant: 'destructive',
      })
      setDefaultPromptOpen(false)
    } finally {
      setLoadingDefaultPrompt(false)
    }
  }

  const handleToggleDiffMode = async () => {
    if (diffMode) {
      setDiffMode(false)
      return
    }
    if (!language || !filename) return

    try {
      setLoadingDiffDefault(true)
      const result = await getDefaultPromptFile(language, filename)
      setDiffDefaultContent(result.content)
      setDiffMode(true)
    } catch (error) {
      toast({
        title: '读取默认 Prompt 失败',
        description: (error as Error).message,
        variant: 'destructive',
      })
    } finally {
      setLoadingDiffDefault(false)
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 p-3 sm:gap-4 sm:p-6">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex min-w-0 items-center justify-between gap-2">
          <h1 className="text-xl font-bold sm:text-2xl md:text-3xl">Prompt管理</h1>
        </div>
        <div className="flex min-w-0 items-center gap-1.5 sm:flex-wrap sm:gap-2">
          <Select value={language} onValueChange={handleLanguageChange} disabled={loadingCatalog}>
            <SelectTrigger className="h-8 w-[6.25rem] text-xs sm:h-9 sm:w-[160px] sm:text-sm">
              <SelectValue placeholder="选择语言" />
            </SelectTrigger>
            <SelectContent>
              {(catalog?.languages ?? []).map((item) => (
                <SelectItem key={item} value={item}>
                  {item}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button
            variant="outline"
            size="icon"
            onClick={() => void loadCatalog()}
            disabled={loadingCatalog}
            className="h-8 w-8 shrink-0 sm:h-9 sm:w-9"
            title="刷新"
            aria-label="刷新"
          >
            <RefreshCw className={cn('h-4 w-4', loadingCatalog && 'animate-spin')} />
          </Button>
          <Button
            variant={showAdvancedPrompts ? 'default' : 'outline'}
            size="sm"
            onClick={() => setShowAdvancedPrompts((current) => !current)}
            className="h-8 shrink-0 px-2 text-xs sm:h-9 sm:px-3 sm:text-sm"
          >
            <SlidersHorizontal className="h-3.5 w-3.5 sm:mr-2 sm:h-4 sm:w-4" />
            <span className="hidden sm:inline">
              {showAdvancedPrompts ? '隐藏高级' : '显示高级'}
            </span>
          </Button>
          <Button
            size="sm"
            className="h-8 shrink-0 px-2 text-xs sm:h-9 sm:px-3 sm:text-sm"
            onClick={handleSave}
            disabled={!hasUnsavedChanges || saving || loadingFile || !filename}
          >
            <Save className="h-3.5 w-3.5 sm:mr-2 sm:h-4 sm:w-4" />
            <span className="ml-1 sm:ml-0">
              {saving
                ? '保存中'
                : hasUnsavedChanges
                  ? selectedCustomVersion
                    ? '保存修改'
                    : '保存为新版本'
                  : '已保存'}
            </span>
          </Button>
        </div>
      </div>

      <div className="grid min-h-0 flex-1 grid-rows-[auto_minmax(0,1fr)] gap-3 lg:grid-cols-[18rem_minmax(0,1fr)] lg:grid-rows-1 lg:gap-4">
        <Card className="flex max-h-52 min-h-0 flex-col overflow-hidden sm:max-h-none">
          <CardHeader className="shrink-0 p-3 pb-2 sm:p-6 sm:pb-3">
            <div className="flex items-center gap-2">
              <div className="text-foreground flex h-10 shrink-0 items-center px-1 text-4xl leading-none font-bold sm:h-9">
                {filteredFiles.length}
              </div>
              <div className="relative min-w-0 flex-1">
                <Search className="text-muted-foreground pointer-events-none absolute top-2.5 left-2 h-4 w-4" />
                <Input
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="搜索"
                  className="pl-8"
                />
              </div>
            </div>
          </CardHeader>
          <Separator />
          <ScrollArea className="min-h-0 flex-1" scrollbars="vertical">
            <div className="space-y-1 p-2">
              {loadingCatalog ? (
                <div className="text-muted-foreground flex items-center justify-center gap-2 p-6 text-sm">
                  <ThinkingIllustration size="sm" />
                </div>
              ) : filteredFiles.length > 0 ? (
                filteredFiles.map((file) => (
                  <button
                    key={file.name}
                    type="button"
                    onClick={() => setFilename(file.name)}
                    className={cn(
                      'w-full rounded-md px-3 py-2 text-left text-sm transition-colors',
                      'hover:bg-accent hover:text-accent-foreground',
                      filename === file.name
                        ? 'bg-accent text-accent-foreground'
                        : 'text-muted-foreground'
                    )}
                  >
                    <div className="flex items-center gap-2">
                      <div className="truncate font-medium" title={file.display_name || file.name}>
                        {file.display_name || file.name}
                      </div>
                      {file.advanced && (
                        <Badge variant="outline" className="shrink-0 text-[10px]">
                          高级
                        </Badge>
                      )}
                      {file.customized && (
                        <Badge variant="secondary" className="shrink-0 text-[10px]">
                          自定义
                        </Badge>
                      )}
                      {file.custom_version_count > 0 && (
                        <Badge variant="outline" className="shrink-0 text-[10px]">
                          {file.custom_version_count} 版
                        </Badge>
                      )}
                    </div>
                    <div className="text-muted-foreground mt-0.5 truncate text-xs">{file.name}</div>
                    {file.description && (
                      <div className="text-muted-foreground mt-1 line-clamp-2 text-xs">
                        {file.description}
                      </div>
                    )}
                  </button>
                ))
              ) : (
                <div className="text-muted-foreground p-6 text-center text-sm">
                  没有可编辑的 Prompt 文件
                </div>
              )}
            </div>
          </ScrollArea>
        </Card>

        <Card className="flex min-h-0 flex-col overflow-hidden">
          <CardHeader className="flex flex-col gap-2 space-y-0 p-3 pb-2 sm:gap-3 sm:p-6 sm:pb-3">
            <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between sm:gap-3">
              <div className="min-w-0">
                <CardTitle className="flex items-center gap-2 truncate text-sm">
                  <span className="truncate">
                    {selectedFile?.display_name || filename || '未选择文件'}
                  </span>
                  {selectedFile?.advanced && (
                    <Badge variant="outline" className="shrink-0">
                      高级
                    </Badge>
                  )}
                  {isCustomized && (
                    <Badge variant="secondary" className="shrink-0">
                      自定义
                    </Badge>
                  )}
                </CardTitle>
                <p className="text-muted-foreground mt-1 text-xs">
                  {language}
                  {selectedFile ? ` · ${formatFileSize(selectedFile.size)}` : ''}
                  {versions.length > 0 ? ` · ${versions.length} 个自定义版本` : ''}
                  {hasUnsavedChanges ? ' · 有未保存修改' : ''}
                </p>
                {selectedFile?.description && (
                  <p className="text-muted-foreground mt-1 line-clamp-2 text-xs">
                    {selectedFile.description}
                  </p>
                )}
              </div>
              <div className="flex shrink-0 flex-wrap items-center gap-2">
                <Select
                  value={selectedVersionId}
                  onValueChange={handleVersionChange}
                  disabled={loadingFile || saving || !filename}
                >
                  <SelectTrigger className="h-8 w-full text-xs sm:h-9 sm:w-[220px]">
                    <SelectValue placeholder="选择版本" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={DEFAULT_VERSION_ID}>默认版本</SelectItem>
                    {versions.map((version) => (
                      <SelectItem key={version.id} value={version.id}>
                        {version.label}
                        {version.active ? '（启用）' : ''}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Button
                  variant="default"
                  size="sm"
                  onClick={() => void handleApplySelectedVersion()}
                  disabled={!canApplySelectedVersion || applyingVersion || loadingFile || !filename}
                  className="h-8 shrink-0 px-2 text-xs sm:h-9 sm:px-3 sm:text-sm"
                >
                  <CheckCircle2
                    className={cn(
                      'mr-1 h-3.5 w-3.5 sm:mr-2 sm:h-4 sm:w-4',
                      applyingVersion && 'animate-pulse'
                    )}
                  />
                  {applyingVersion ? '应用中' : selectedVersionIsApplied ? '已应用' : '应用此版本'}
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleShowDefault}
                  disabled={loadingDefaultPrompt || loadingFile || !filename}
                  className="h-8 flex-1 px-2 text-xs sm:h-9 sm:flex-none sm:px-3 sm:text-sm"
                >
                  <Eye
                    className={cn(
                      'mr-1 h-3.5 w-3.5 sm:mr-2 sm:h-4 sm:w-4',
                      loadingDefaultPrompt && 'animate-pulse'
                    )}
                  />
                  查看默认
                </Button>
                <Button
                  variant={diffMode ? 'default' : 'outline'}
                  size="sm"
                  onClick={() => void handleToggleDiffMode()}
                  disabled={loadingDiffDefault || loadingFile || !filename}
                  className="h-8 flex-1 px-2 text-xs sm:h-9 sm:flex-none sm:px-3 sm:text-sm"
                >
                  <GitCompareArrows
                    className={cn(
                      'mr-1 h-3.5 w-3.5 sm:mr-2 sm:h-4 sm:w-4',
                      loadingDiffDefault && 'animate-pulse'
                    )}
                  />
                  {diffMode ? '退出对比' : '对比默认'}
                </Button>
              </div>
            </div>

            {diffMode && (
              <div className="text-muted-foreground flex flex-wrap items-center gap-2 text-xs">
                <Badge variant="outline" className="border-green-500/50 bg-green-500/10 text-green-700">
                  新增 {diffState.added}
                </Badge>
                <Badge variant="outline" className="border-red-500/50 bg-red-500/10 text-red-700">
                  删除 {diffState.removed}
                </Badge>
                <Badge variant="outline" className="border-amber-500/50 bg-amber-500/10 text-amber-700">
                  修改 {diffState.changed}
                </Badge>
              </div>
            )}

            {validation && !validation.valid && (
              <Alert variant="destructive">
                <AlertTitle>Prompt 参数不匹配</AlertTitle>
                <AlertDescription>{validation.message}</AlertDescription>
              </Alert>
            )}
          </CardHeader>
          <CardContent className="min-h-0 flex-1 p-0">
            {loadingFile ? (
              <div className="text-muted-foreground flex h-full min-h-[320px] items-center justify-center gap-2 text-sm">
                <ThinkingIllustration />
              </div>
            ) : diffMode ? (
              <div className="grid h-full min-h-[320px] grid-rows-[minmax(0,1fr)_minmax(0,1fr)] gap-2 p-2 lg:grid-cols-2 lg:grid-rows-1">
                <div className="flex min-h-0 flex-col gap-1">
                  <div className="text-muted-foreground px-1 text-xs">默认版本</div>
                  <CodeEditor
                    value={diffDefaultContent}
                    readOnly
                    language="text"
                    height="100%"
                    minHeight="0"
                    className="h-full"
                    lineClassNames={diffState.defaultLineClasses}
                    rangeClassNames={diffState.defaultRangeClasses}
                  />
                </div>
                <div className="flex min-h-0 flex-col gap-1">
                  <div className="text-muted-foreground px-1 text-xs">当前编辑</div>
                  <CodeEditor
                    value={content}
                    onChange={setContent}
                    language="text"
                    height="100%"
                    minHeight="0"
                    placeholder="选择一个 Prompt 文件后开始编辑"
                    className="h-full"
                    lineClassNames={diffState.currentLineClasses}
                    rangeClassNames={diffState.currentRangeClasses}
                  />
                </div>
              </div>
            ) : (
              <CodeEditor
                value={content}
                onChange={setContent}
                language="text"
                height="100%"
                minHeight="320px"
                placeholder="选择一个 Prompt 文件后开始编辑"
                className="h-full"
              />
            )}
          </CardContent>
        </Card>
      </div>

      <Dialog open={defaultPromptOpen} onOpenChange={setDefaultPromptOpen}>
        <DialogContent className="h-[calc(100dvh-2rem)] max-w-[min(96vw,1100px)]">
          <DialogHeader>
            <DialogTitle>默认 Prompt</DialogTitle>
            <DialogDescription>
              {language}/{filename} 的内置模板，只读显示，不会修改或删除自定义内容。
            </DialogDescription>
          </DialogHeader>
          {loadingDefaultPrompt ? (
            <div className="text-muted-foreground flex min-h-0 flex-1 items-center justify-center gap-2 text-sm">
              <ThinkingIllustration />
            </div>
          ) : (
            <div className="min-h-0 flex-1">
              <CodeEditor
                value={defaultPromptContent}
                readOnly
                language="text"
                height="100%"
                minHeight="0"
                className="h-full"
              />
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  )
}
