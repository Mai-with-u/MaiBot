import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Check,
  CheckCircle2,
  Eye,
  FileText,
  GitCompareArrows,
  List,
  Maximize2,
  MoreHorizontal,
  PanelLeftClose,
  PanelLeftOpen,
  RefreshCw,
  Save,
  Search,
  SlidersHorizontal,
  Trash2,
} from 'lucide-react'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import {
  DashboardFolderPanel,
  DashboardFolderTab,
  DashboardFolderTabBar,
} from '@/components/ui/dashboard-tabs'
import { DataPanel } from '@/components/ui/surface'
import { SelectionListItem } from '@/components/ui/selection-list-item'
import { Tabs, TabsContent } from '@/components/ui/tabs'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { PromptWarningDot } from '@/components/ui/prompt-warning-dot'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { StatefulButton } from '@/components/ui/stateful-button'
import { ThinkingIllustration } from '@/components/ui/thinking-illustration'
import { MOBILE_GLASS_CLASS } from '@/components/layout/mobile-glass'
import { MobileHeaderActions } from '@/components/layout/MobileHeaderActions'
import { useMobileTitleOverride } from '@/components/layout/mobile-nav-context'
import { useMediaQuery } from '@/hooks/use-media-query'
import { useToast } from '@/hooks/use-toast'
import {
  activatePromptVersion,
  deletePromptVersion,
  getDefaultPromptFile,
  getPromptFile,
  getPromptVersionFile,
  resetPromptFile,
  updatePromptFile,
  updateDefaultPromptFile,
  type PromptCatalog,
  type PromptFileInfo,
  type PromptValidationResult,
  type PromptVersionInfo,
} from '@/lib/prompt-api'
import { getSetting } from '@/lib/settings-manager'
import { cn } from '@/lib/utils'
import { promptCatalogQueryOptions } from '@/lib/prompt-query'
import { queryClient } from '@/lib/query'
import { promptFolder } from './prompt-folders'
import { PromptDirectory } from './PromptDirectory'
import { PromptBlockEditor } from './PromptBlockEditor'
import { PromptSideBySide } from './PromptSideBySide'
import '@/styles/prompt-management.css'

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
      addPromptDiffRange(
        defaultRangeClasses,
        i + 1,
        0,
        defaultLines[i].length,
        DIFF_REMOVED_TEXT_CLASS
      )
      removed += 1
      i += 1
    } else if (canAdd) {
      currentLineClasses[j + 1] = DIFF_ADDED_CLASS
      addPromptDiffRange(
        currentRangeClasses,
        j + 1,
        0,
        currentLines[j].length,
        DIFF_ADDED_TEXT_CLASS
      )
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
  const { t } = useTranslation()
  const [editDefaultPrompt, setEditDefaultPrompt] = useState(() => getSetting('editDefaultPrompt'))
  const [sideBySideOpen, setSideBySideOpen] = useState(false)
  const [rightFilename, setRightFilename] = useState('')
  const [rightEditorState, setRightEditorState] = useState({ dirty: false, busy: false })
  const handleRightStateChange = useCallback((dirty: boolean, busy: boolean) => {
    setRightEditorState({ dirty, busy })
  }, [])
  const { toast } = useToast()
  const [catalog, setCatalog] = useState<PromptCatalog | null>(null)
  const [language, setLanguage] = useState('zh-CN')
  const [filename, setFilename] = useState('')
  // 手机：先看文件列表，点进文件才进入编辑页
  const isMobileViewport = useMediaQuery('(max-width: 1023.98px)')
  const [mobileEditing, setMobileEditing] = useState(false)
  const [content, setContent] = useState('')
  const [savedContent, setSavedContent] = useState('')
  const [loadingCatalog, setLoadingCatalog] = useState(true)
  const [loadingFile, setLoadingFile] = useState(false)
  const [saving, setSaving] = useState(false)
  const [applyingVersion, setApplyingVersion] = useState(false)
  const [deletingVersion, setDeletingVersion] = useState(false)
  const [deleteVersionDialogOpen, setDeleteVersionDialogOpen] = useState(false)
  const [loadingDefaultPrompt, setLoadingDefaultPrompt] = useState(false)
  const [defaultPromptOpen, setDefaultPromptOpen] = useState(false)
  const [defaultPromptContent, setDefaultPromptContent] = useState('')
  const [diffMode, setDiffMode] = useState(false)
  const [loadingDiffDefault, setLoadingDiffDefault] = useState(false)
  const [diffDefaultContent, setDiffDefaultContent] = useState('')
  const [query, setQuery] = useState('')
  const [showAdvancedPrompts, setShowAdvancedPrompts] = useState(false)
  const [promptKind, setPromptKind] = useState<'main' | 'instruction'>('main')
  const [focusEditing, setFocusEditing] = useState(false)
  const [onlyCustomized, setOnlyCustomized] = useState(false)
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

  const rootFiles = useMemo(
    () => promptFiles.filter((file) => (file.kind ?? 'main') === promptKind),
    [promptFiles, promptKind]
  )
  const visiblePromptFiles = useMemo(
    () => (showAdvancedPrompts ? rootFiles : rootFiles.filter((file) => !file.advanced)),
    [rootFiles, showAdvancedPrompts]
  )
  const rootPath = promptKind === 'main' ? 'main' : 'instructions'
  const filteredFiles = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase()
    return visiblePromptFiles.filter((file) => {
      if (onlyCustomized && !file.customized) return false
      return [file.name, file.relative_path, file.display_name, file.description]
        .join(' ')
        .toLowerCase()
        .includes(normalizedQuery)
    })
  }, [visiblePromptFiles, query, onlyCustomized])

  const selectedFile = promptFiles.find((file) => file.name === filename)
  const isCustomized = selectedFile?.customized ?? false
  const selectedCustomVersion = selectedVersionId !== DEFAULT_VERSION_ID
  const editingDefaultPrompt = editDefaultPrompt && !selectedCustomVersion
  const saveLabel = editingDefaultPrompt
    ? t('settings.other.saveDefaultPrompt')
    : selectedCustomVersion
      ? '保存修改'
      : '保存为新版本'
  const selectedVersionIsApplied = selectedCustomVersion
    ? selectedVersionId === activeVersionId
    : !isCustomized
  const canApplySelectedVersion = !selectedVersionIsApplied && !hasUnsavedChanges
  const canDeleteSelectedVersion =
    selectedCustomVersion && !hasUnsavedChanges && !loadingFile && !saving && !applyingVersion
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
      setSelectedVersionId(nextSelectedVersionId ?? result.active_version_id ?? DEFAULT_VERSION_ID)
      setValidation(result.validation ?? null)
    },
    []
  )

  useEffect(() => {
    const refreshSetting = () => setEditDefaultPrompt(getSetting('editDefaultPrompt'))
    window.addEventListener('maibot-settings-change', refreshSetting)
    window.addEventListener('storage', refreshSetting)
    return () => {
      window.removeEventListener('maibot-settings-change', refreshSetting)
      window.removeEventListener('storage', refreshSetting)
    }
  }, [])

  useEffect(() => {
    diffModeRef.current = diffMode
  }, [diffMode])

  useEffect(() => {
    if (!filename || showAdvancedPrompts || sideBySideOpen) return
    const currentFile = promptFiles.find((file) => file.name === filename)
    if (!currentFile?.advanced) return
    setFilename('')
  }, [filename, promptFiles, showAdvancedPrompts, sideBySideOpen])

  const loadCatalog = useCallback(async () => {
    try {
      setLoadingCatalog(true)
      const result = await queryClient.fetchQuery({
        ...promptCatalogQueryOptions,
        staleTime: 0,
      })
      setCatalog(result)
      const nextLanguage =
        language && result.languages.includes(language)
          ? language
          : result.languages.includes('zh-CN')
            ? 'zh-CN'
            : (result.languages[0] ?? '')
      setLanguage(nextLanguage)

      const nextFiles = nextLanguage ? (result.files[nextLanguage] ?? []) : []
      setFilename((current) =>
        nextLanguage === language && nextFiles.some((file) => file.name === current) ? current : ''
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
      setLoadingFile(false)
      setMobileEditing(false)
      setFocusEditing(false)
      setDiffMode(false)
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
    if (!canLeaveEditor()) return
    setSideBySideOpen(false)
    setRightFilename('')
    setLanguage(nextLanguage)
    setQuery('')
    setFilename('')
  }

  const handleSave = async () => {
    if (!language || !filename) return

    try {
      setSaving(true)
      const result = editingDefaultPrompt
        ? await updateDefaultPromptFile(language, filename, content)
        : await updatePromptFile(language, filename, content, {
            versionId: selectedCustomVersion ? selectedVersionId : null,
            createVersion: !selectedCustomVersion,
          })
      const nextVersionId = editingDefaultPrompt
        ? DEFAULT_VERSION_ID
        : (result.active_version_id ?? DEFAULT_VERSION_ID)
      applyPromptContent(result, nextVersionId)
      if (editingDefaultPrompt) setDiffDefaultContent(result.content)
      if (selectedVersionStorageKey) {
        localStorage.setItem(selectedVersionStorageKey, nextVersionId)
      }
      toast({
        title: editingDefaultPrompt ? t('settings.other.defaultPromptSaved') : 'Prompt 已保存',
        description: `${language}/${filename}`,
      })
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
    if (!language || !filename || !canLeaveRightEditor()) return
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
    if (!language || !filename || !canLeaveRightEditor()) return

    try {
      setApplyingVersion(true)
      const result =
        selectedVersionId === DEFAULT_VERSION_ID
          ? await resetPromptFile(language, filename)
          : await activatePromptVersion(language, filename, selectedVersionId)
      applyPromptContent(result, result.active_version_id ?? selectedVersionId)
      if (selectedVersionStorageKey) {
        localStorage.setItem(
          selectedVersionStorageKey,
          result.active_version_id ?? selectedVersionId
        )
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

  const handleDeleteSelectedVersion = async () => {
    if (!language || !filename || !selectedCustomVersion || !canLeaveRightEditor()) return

    try {
      setDeletingVersion(true)
      const deletedVersion = versions.find((version) => version.id === selectedVersionId)
      const result = await deletePromptVersion(language, filename, selectedVersionId)
      const nextSelectedVersionId = result.active_version_id ?? DEFAULT_VERSION_ID
      applyPromptContent(result, nextSelectedVersionId)
      if (selectedVersionStorageKey) {
        localStorage.setItem(selectedVersionStorageKey, nextSelectedVersionId)
      }
      setDeleteVersionDialogOpen(false)
      toast({
        title: 'Prompt 版本已删除',
        description: `${deletedVersion?.label ?? selectedVersionId} · ${language}/${filename}`,
      })
      void loadCatalog()
    } catch (error) {
      toast({
        title: '删除 Prompt 版本失败',
        description: (error as Error).message,
        variant: 'destructive',
      })
    } finally {
      setDeletingVersion(false)
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

  const canLeaveRightEditor = () => {
    if (!sideBySideOpen || (!rightEditorState.dirty && !rightEditorState.busy)) return true
    toast({
      title: t('promptCompare.unsavedRight'),
      description: t('promptCompare.saveRightFirst'),
      variant: 'destructive',
    })
    return false
  }

  const canLeaveEditor = () => {
    if (!canLeaveRightEditor()) return false
    if (!hasUnsavedChanges) return true
    toast({
      title: '当前 Prompt 有未保存修改',
      description: '请先保存修改，再切换文件或分类。',
      variant: 'destructive',
    })
    return false
  }

  const handleEditBlockTemplate = (file: PromptFileInfo) => {
    if (!canLeaveEditor()) return false
    setSideBySideOpen(false)
    setRightFilename('')
    setShowAdvancedPrompts(true)
    setPromptKind(file.kind ?? 'main')
    setQuery('')
    setOnlyCustomized(false)
    setFilename(file.name)
    setMobileEditing(true)
    return true
  }

  const handleRootChange = (value: string) => {
    if (value !== 'main' && value !== 'instruction') return
    if (sideBySideOpen) {
      setPromptKind(value)
      setQuery('')
      setOnlyCustomized(false)
      setMobileEditing(false)
      return
    }
    if (!canLeaveEditor()) return
    setSideBySideOpen(false)
    setRightFilename('')
    setPromptKind(value)
    setQuery('')
    setOnlyCustomized(false)
    setFilename('')
    setMobileEditing(false)
  }

  const mobileEditorOpen = isMobileViewport && mobileEditing && Boolean(filename)
  // 编辑页的大标题换成文件名
  useMobileTitleOverride(mobileEditorOpen ? filename.replace(/\.prompt$/, '') : null)

  return (
    <Tabs
      value={promptKind}
      onValueChange={handleRootChange}
      data-prompt-management="true"
      className="flex h-full min-h-0 flex-col p-3 sm:p-6"
    >
      {/* 手机页头右上角：编辑页返回文件列表 */}
      {mobileEditorOpen && (
        <MobileHeaderActions>
          <button
            type="button"
            onClick={() => setMobileEditing(false)}
            data-mobile-header-text-button="true"
            className={cn('h-11 rounded-full px-4 text-[15px]', MOBILE_GLASS_CLASS)}
          >
            文件列表
          </button>
        </MobileHeaderActions>
      )}
      {/* 手机编辑页：有未保存修改时浮出保存条 */}
      {mobileEditorOpen && hasUnsavedChanges && (
        <div
          data-mobile-save-bar="true"
          className={cn(
            'fixed inset-x-4 bottom-[calc(var(--mobile-dock-height,0px)+8px)] z-30 flex h-[60px] items-center gap-2 rounded-full pr-2 pl-5',
            MOBILE_GLASS_CLASS
          )}
        >
          <span className="flex-1 text-[15px] font-medium">有未保存修改</span>
          <button
            type="button"
            onClick={handleSave}
            disabled={saving || loadingFile}
            className="bg-foreground text-background h-11 rounded-full px-5 text-[15px] font-semibold disabled:opacity-50"
          >
            {saving ? '保存中' : saveLabel}
          </button>
        </div>
      )}
      <h1 className="sr-only">Prompt 管理</h1>
      <DashboardFolderTabBar
        size="large"
        aria-label="Prompt 根目录"
        trailing={
          <div className="flex flex-wrap items-center gap-2 pb-1">
            <Select value={language} onValueChange={handleLanguageChange} disabled={loadingCatalog}>
              <SelectTrigger className="h-8 w-[6.25rem] text-xs sm:h-9 sm:w-[160px] sm:text-sm">
                <SelectValue placeholder="选择语言" />
              </SelectTrigger>
              <SelectContent>
                {(catalog?.languages ?? []).map((item) => (
                  <SelectItem key={item} value={item}>
                    <span className="inline-flex items-center gap-2">
                      {item}
                      {catalog?.files[item]?.some((file) => file.validation?.valid === false) && (
                        <PromptWarningDot />
                      )}
                    </span>
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
              onClick={() => {
                if (sideBySideOpen || canLeaveEditor()) setShowAdvancedPrompts((current) => !current)
              }}
              aria-pressed={showAdvancedPrompts}
              aria-label={showAdvancedPrompts ? '隐藏高级' : '显示高级'}
              className="h-8 shrink-0 px-2 text-xs sm:h-9 sm:px-3 sm:text-sm"
            >
              <SlidersHorizontal className="h-3.5 w-3.5 sm:mr-2 sm:h-4 sm:w-4" />
              <span className="hidden sm:inline">
                {showAdvancedPrompts ? '隐藏高级' : '显示高级'}
              </span>
              {promptFiles.some((file) => file.advanced && file.validation?.valid === false) && (
                <PromptWarningDot message="高级 Prompt 中有模板不兼容或缺少变量" />
              )}
            </Button>
          </div>
        }
      >
        <DashboardFolderTab
          value="main"
          icon={<FileText className="size-4" />}
          description={
            <span className="inline-flex items-center gap-2">
              <span>main</span>
              <span>完整任务提示词</span>
              <span className="tabular-nums">
                {promptFiles.filter((file) => (file.kind ?? 'main') === 'main').length}
              </span>
            </span>
          }
        >
          主模板
        </DashboardFolderTab>
        <DashboardFolderTab
          value="instruction"
          icon={<List className="size-4" />}
          description={
            <span className="inline-flex items-center gap-2">
              <span>instructions</span>
              <span>插入对话的短指令</span>
              <span className="tabular-nums">
                {promptFiles.filter((file) => file.kind === 'instruction').length}
              </span>
            </span>
          }
        >
          指令片段
        </DashboardFolderTab>
      </DashboardFolderTabBar>

      <TabsContent value={promptKind} className="mt-0 flex min-h-0 flex-1" asChild>
        <DashboardFolderPanel
          data-prompt-workspace="true"
          data-prompt-focus={!isMobileViewport && focusEditing}
        >
          <DataPanel
            padding="none"
            data-prompt-folders="true"
            data-prompt-file-pane="true"
            hidden={isMobileViewport ? mobileEditing : focusEditing}
            className={cn(
              'min-h-0 min-w-0 flex-col overflow-hidden',
              (isMobileViewport ? mobileEditing : focusEditing) ? 'hidden' : 'flex'
            )}
          >
            <div className="flex shrink-0 items-center justify-between gap-2 px-3 pt-2">
              <span className="text-sm font-semibold">
                模板目录{' '}
                <span className="text-muted-foreground text-xs tabular-nums">
                  {filteredFiles.length}
                </span>
              </span>
              {!isMobileViewport && (
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-7"
                  aria-label="收起模板目录"
                  onClick={() => setFocusEditing(true)}
                >
                  <PanelLeftClose className="size-4" />
                </Button>
              )}
            </div>
            <div className="relative m-2 shrink-0">
              <Search className="text-muted-foreground pointer-events-none absolute top-1/2 left-3 z-10 size-4 -translate-y-1/2" />
              <Input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="搜索"
                aria-label="搜索 Prompt 名称、路径或描述"
                className="pl-9"
              />
            </div>
            <ScrollArea className="min-h-0 flex-1" scrollbars="vertical">
              {loadingCatalog ? (
                <div className="flex justify-center p-6">
                  <ThinkingIllustration size="sm" />
                </div>
              ) : filteredFiles.length ? (
                <PromptDirectory
                  key={`${language}/${promptKind}`}
                  files={filteredFiles}
                  selectedFile={
                    sideBySideOpen
                      ? promptFiles.find((file) => file.name === rightFilename)
                      : selectedFile
                  }
                  filtering={Boolean(query.trim()) || onlyCustomized}
                  onSelect={(file) => {
                    if (sideBySideOpen) {
                      if (file.name !== rightFilename && !canLeaveRightEditor()) return
                      setRightFilename(file.name)
                      setMobileEditing(true)
                      return
                    }
                    if (filename !== file.name && !canLeaveEditor()) return
                    setFilename(file.name)
                    setMobileEditing(true)
                  }}
                />
              ) : (
                <div className="text-muted-foreground p-6 text-center text-sm">
                  没有可编辑的 Prompt 文件
                </div>
              )}
            </ScrollArea>
            <div className="border-border shrink-0 border-t py-2">
              <SelectionListItem
                selected={onlyCustomized}
                onClick={() => setOnlyCustomized((current) => !current)}
                className="flex items-center justify-between px-3"
              >
                <span>仅看已修改</span>
                <span className="text-xs tabular-nums">
                  {visiblePromptFiles.filter((file) => file.customized).length}
                </span>
              </SelectionListItem>
            </div>
          </DataPanel>

          <section
            aria-label="Prompt 编辑器"
            data-prompt-editor="true"
            className={cn(
              'flex min-h-0 flex-col overflow-hidden',
              isMobileViewport && !mobileEditing && 'hidden'
            )}
          >
            <div
              hidden={!filename}
              className={cn('flex shrink-0 flex-col gap-1.5 pb-2', !filename && 'hidden')}
            >
              <div className="flex min-w-0 flex-col gap-1.5">
                <div className="flex min-w-0 items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-0.5">
                      <h2
                        data-prompt-title="true"
                        className="flex min-w-0 items-center gap-2 text-base"
                        title={selectedFile?.relative_path}
                      >
                        <span className="truncate">
                          {selectedFile?.display_name || filename || '未选择文件'}
                        </span>
                        {!loadingFile && validation?.valid === false && (
                          <PromptWarningDot message={validation.message} />
                        )}
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
                      </h2>
                      <div className="text-muted-foreground flex flex-wrap items-center gap-x-2 text-xs">
                        <span>{language}</span>
                        {selectedFile && (
                          <span className="tabular-nums">{formatFileSize(selectedFile.size)}</span>
                        )}
                        {versions.length > 0 && <span>{versions.length} 个自定义版本</span>}
                        {hasUnsavedChanges && <span>有未保存修改</span>}
                      </div>
                    </div>
                    {selectedFile?.description && (
                      <p
                        className="text-muted-foreground mt-0.5 truncate text-xs"
                        title={selectedFile.description}
                      >
                        {selectedFile.description}
                      </p>
                    )}
                  </div>
                  {!isMobileViewport && (
                    <Button
                      variant="outline"
                      size="sm"
                      className="h-7 shrink-0 gap-1 px-2"
                      aria-pressed={focusEditing}
                      onClick={() => setFocusEditing((current) => !current)}
                    >
                      {focusEditing ? (
                        <PanelLeftOpen className="mr-2 size-4" />
                      ) : (
                        <Maximize2 className="mr-2 size-4" />
                      )}
                      {focusEditing ? '展开模板目录' : '专注编辑'}
                    </Button>
                  )}
                </div>
                <div className="flex shrink-0 flex-wrap items-center gap-1.5">
                  <Select
                    value={selectedVersionId}
                    onValueChange={handleVersionChange}
                    disabled={loadingFile || saving || !filename}
                  >
                    <SelectTrigger
                      aria-label="Prompt 版本"
                      className="h-7 w-[160px] min-w-0 text-xs sm:w-[180px]"
                    >
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
                  <StatefulButton
                    variant="default"
                    size="sm"
                    status={
                      applyingVersion
                        ? 'loading'
                        : selectedVersionIsApplied
                          ? 'success'
                          : 'idle'
                    }
                    idleLabel="应用此版本"
                    loadingLabel="应用中"
                    successLabel="已应用"
                    idleIcon={<CheckCircle2 className="size-3.5" />}
                    successIcon={<CheckCircle2 className="size-3.5" />}
                    onClick={() => void handleApplySelectedVersion()}
                    disabled={
                      !canApplySelectedVersion || applyingVersion || loadingFile || !filename
                    }
                    className="h-7 shrink-0 gap-1 px-2 text-xs"
                  />
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button variant="outline" size="sm" className="h-7 gap-1 px-2" disabled={!filename}>
                        <MoreHorizontal className="size-3.5" />
                        版本操作
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="start">
                      <DropdownMenuItem
                        onSelect={() => void handleShowDefault()}
                        disabled={loadingDefaultPrompt || loadingFile || !filename}
                      >
                        <Eye className="size-3.5" />
                        查看默认
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        onSelect={() => setDeleteVersionDialogOpen(true)}
                        disabled={!canDeleteSelectedVersion || deletingVersion}
                        className="text-destructive focus:text-destructive"
                      >
                        <Trash2 className="size-3.5" />
                        删除版本
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-7 gap-1 px-2"
                    aria-pressed={sideBySideOpen}
                    onClick={() => {
                      if (!canLeaveRightEditor()) return
                      setSideBySideOpen((current) => !current)
                      if (sideBySideOpen && selectedFile) setPromptKind(selectedFile.kind ?? 'main')
                      setDiffMode(false)
                      setRightFilename('')
                      setFocusEditing(false)
                      if (isMobileViewport) setMobileEditing(sideBySideOpen)
                    }}
                    disabled={loadingFile || !filename}
                  >
                    <PanelLeftOpen className="size-3.5" />
                    {t(sideBySideOpen ? 'promptCompare.exit' : 'promptCompare.title')}
                  </Button>
                  <Button
                    variant={diffMode ? 'default' : 'outline'}
                    size="sm"
                    onClick={() => {
                      if (!canLeaveRightEditor()) return
                      setSideBySideOpen(false)
                      void handleToggleDiffMode()
                    }}
                    disabled={loadingDiffDefault || loadingFile || !filename}
                    className="h-7 gap-1 px-2 text-xs"
                  >
                    <GitCompareArrows
                      className={cn(
                        'size-3.5',
                        loadingDiffDefault && 'animate-pulse'
                      )}
                    />
                    {diffMode ? '退出对比' : '对比默认'}
                  </Button>
                  <StatefulButton
                    size="sm"
                    status={saving ? 'loading' : hasUnsavedChanges ? 'idle' : 'success'}
                    idleLabel={saveLabel}
                    loadingLabel="保存中"
                    successLabel="已保存"
                    idleIcon={<Save className="size-3.5" />}
                    successIcon={<Check className="size-3.5" />}
                    className="ml-auto h-7 shrink-0 gap-1 px-2 text-xs"
                    onClick={handleSave}
                    disabled={!hasUnsavedChanges || saving || loadingFile || !filename}
                  />
                </div>
              </div>

              {editingDefaultPrompt && (
                <details className="rounded-md border border-border bg-muted/30 px-2 py-1 text-xs">
                  <summary className="cursor-pointer text-muted-foreground">
                    {t('settings.other.editDefaultPrompt')}
                  </summary>
                  <p className="pt-1 leading-relaxed text-muted-foreground">
                    {t('settings.other.editingDefaultPromptDesc')}
                  </p>
                </details>
              )}
              {diffMode && (
                <div className="text-muted-foreground flex flex-wrap items-center gap-2 text-xs">
                  <Badge
                    variant="outline"
                    className="border-green-500/50 bg-green-500/10 text-green-700"
                  >
                    新增 {diffState.added}
                  </Badge>
                  <Badge variant="outline" className="border-red-500/50 bg-red-500/10 text-red-700">
                    删除 {diffState.removed}
                  </Badge>
                  <Badge
                    variant="outline"
                    className="border-amber-500/50 bg-amber-500/10 text-amber-700"
                  >
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
            </div>
            <div className="min-h-0 flex-1">
              {!filename ? (
                <DataPanel className="flex h-full flex-col items-center justify-center gap-3 text-center">
                  <FileText className="text-muted-foreground size-8" />
                  <h2 data-prompt-title="true" className="text-lg">
                    请选择提示词
                  </h2>
                  <p className="text-muted-foreground text-sm">
                    从左侧目录选择一个提示词后开始编辑
                  </p>
                </DataPanel>
              ) : loadingFile ? (
                <div className="text-muted-foreground flex h-full min-h-[320px] items-center justify-center gap-2 text-sm">
                  <ThinkingIllustration />
                </div>
              ) : sideBySideOpen ? (
                <PromptSideBySide
                  language={language}
                  files={promptFiles}
                  filename={filename}
                  content={content}
                  onChange={setContent}
                  rightFilename={rightFilename}
                  onEditTemplate={handleEditBlockTemplate}
                  editDefaultPrompt={editDefaultPrompt}
                  onSave={handleSave}
                  saving={saving}
                  hasUnsavedChanges={hasUnsavedChanges}
                  saveLabel={saveLabel}
                  onRightStateChange={handleRightStateChange}
                  onSaved={() => void loadCatalog()}
                />
              ) : diffMode ? (
                <div className="grid h-full min-h-[320px] grid-rows-[minmax(0,1fr)_minmax(0,1fr)] gap-2 p-2 lg:grid-cols-2 lg:grid-rows-1">
                  <div className="flex min-h-0 flex-col gap-1">
                    <div className="text-muted-foreground px-1 text-xs">默认版本</div>
                    <PromptBlockEditor
                      promptLanguage={language}
                      promptFilename={filename}
                      files={promptFiles}
                      onEditTemplate={handleEditBlockTemplate}
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
                    <PromptBlockEditor
                      promptLanguage={language}
                      promptFilename={filename}
                      files={promptFiles}
                      onEditTemplate={handleEditBlockTemplate}
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
                <PromptBlockEditor
                  promptLanguage={language}
                  promptFilename={filename}
                  files={promptFiles}
                  onEditTemplate={handleEditBlockTemplate}
                  value={content}
                  onChange={setContent}
                  language="text"
                  height="100%"
                  minHeight="0"
                  readOnly={!filename}
                  placeholder="选择一个 Prompt 文件后开始编辑"
                  className="h-full"
                />
              )}
            </div>
            <div
              hidden={!filename}
              className={cn(
                'text-muted-foreground flex shrink-0 flex-wrap justify-between gap-2 pt-2 text-xs',
                !filename && 'hidden'
              )}
            >
              <span className="flex items-center gap-2 font-mono">
                {selectedFile ? (
                  <>
                    <span>{promptFolder(selectedFile) || rootPath}</span>
                    <span>{filename}</span>
                  </>
                ) : null}
              </span>
              <span className="flex items-center gap-2">
                <span className="font-mono">{language}</span>
                <span className="tabular-nums">{splitPromptLines(content).length} 行</span>
                {hasUnsavedChanges && <span>未保存</span>}
              </span>
            </div>
          </section>
        </DashboardFolderPanel>
      </TabsContent>

      <AlertDialog open={deleteVersionDialogOpen} onOpenChange={setDeleteVersionDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>确认删除 Prompt 版本</AlertDialogTitle>
            <AlertDialogDescription>
              {selectedVersionIsApplied
                ? '删除当前启用的版本后，将恢复使用默认 Prompt。此操作无法撤销。'
                : '删除后无法恢复该自定义 Prompt 版本。'}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deletingVersion}>取消</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => void handleDeleteSelectedVersion()}
              disabled={deletingVersion}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {deletingVersion ? '删除中' : '确认删除'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

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
              <PromptBlockEditor
                promptLanguage={language}
                promptFilename={filename}
                files={promptFiles}
                onEditTemplate={handleEditBlockTemplate}
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
    </Tabs>
  )
}
