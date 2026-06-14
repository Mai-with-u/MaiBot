import { useCallback, useEffect, useMemo, useState } from 'react'
import { Eye, RefreshCw, RotateCcw, Save, Search, SlidersHorizontal } from 'lucide-react'

import { CodeEditor } from '@/components/CodeEditor'
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
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Separator } from '@/components/ui/separator'
import { ThinkingIllustration } from '@/components/ui/thinking-illustration'
import { useToast } from '@/hooks/use-toast'
import {
  getDefaultPromptFile,
  getPromptCatalog,
  getPromptFile,
  resetPromptFile,
  updatePromptFile,
  type PromptCatalog,
  type PromptFileInfo,
} from '@/lib/prompt-api'
import { cn } from '@/lib/utils'

function formatFileSize(size: number) {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
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
  const [resetting, setResetting] = useState(false)
  const [loadingDefaultPrompt, setLoadingDefaultPrompt] = useState(false)
  const [defaultPromptOpen, setDefaultPromptOpen] = useState(false)
  const [defaultPromptContent, setDefaultPromptContent] = useState('')
  const [query, setQuery] = useState('')
  const [showAdvancedPrompts, setShowAdvancedPrompts] = useState(false)

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
      const searchableText = [
        file.name,
        file.display_name,
        file.description,
      ].join(' ').toLowerCase()
      return searchableText.includes(normalizedQuery)
    })
  }, [visiblePromptFiles, query])

  const selectedFile = promptFiles.find((file) => file.name === filename)
  const isCustomized = selectedFile?.customized ?? false
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
      const nextLanguage = language && result.languages.includes(language)
        ? language
        : result.languages.includes('zh-CN')
          ? 'zh-CN'
        : result.languages[0] ?? ''
      setLanguage(nextLanguage)

      const nextFiles = nextLanguage ? result.files[nextLanguage] ?? [] : []
      const nextBasicFiles = nextFiles.filter((file) => !file.advanced)
      setFilename((current) =>
        nextFiles.some((file) => file.name === current) ? current : nextBasicFiles[0]?.name ?? nextFiles[0]?.name ?? ''
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
      return
    }

    let cancelled = false
    const loadFile = async () => {
      try {
        setLoadingFile(true)
        const result = await getPromptFile(language, filename)
        if (cancelled) return
        setContent(result.content)
        setSavedContent(result.content)
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
  }, [filename, language, toast])

  const handleLanguageChange = (nextLanguage: string) => {
    setLanguage(nextLanguage)
    setQuery('')
    const nextFiles = catalog?.files[nextLanguage] ?? []
    const nextVisibleFiles = showAdvancedPrompts ? nextFiles : nextFiles.filter((file) => !file.advanced)
    setFilename(nextVisibleFiles[0]?.name ?? '')
  }

  const handleSave = async () => {
    if (!language || !filename) return

    try {
      setSaving(true)
      const result = await updatePromptFile(language, filename, content)
      setContent(result.content)
      setSavedContent(result.content)
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

  const handleReset = async () => {
    if (!language || !filename || !isCustomized) return

    try {
      setResetting(true)
      const result = await resetPromptFile(language, filename)
      setContent(result.content)
      setSavedContent(result.content)
      toast({ title: '已恢复默认 Prompt', description: `${language}/${filename}` })
      void loadCatalog()
    } catch (error) {
      toast({
        title: '恢复默认 Prompt 失败',
        description: (error as Error).message,
        variant: 'destructive',
      })
    } finally {
      setResetting(false)
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 p-3 sm:gap-4 sm:p-6">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex min-w-0 items-center justify-between gap-2">
          <h1 className="text-xl font-bold sm:text-2xl md:text-3xl">Prompt 管理</h1>
        </div>
        <div className="flex min-w-0 items-center gap-1.5 sm:flex-wrap sm:gap-2">
          <Select value={language} onValueChange={handleLanguageChange} disabled={loadingCatalog}>
            <SelectTrigger className="h-8 w-[6.25rem] text-xs sm:h-9 sm:w-[160px] sm:text-sm">
              <SelectValue placeholder="选择语言" />
            </SelectTrigger>
            <SelectContent>
              {(catalog?.languages ?? []).map((item) => (
                <SelectItem key={item} value={item}>{item}</SelectItem>
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
            <span className="hidden sm:inline">{showAdvancedPrompts ? '隐藏高级' : '显示高级'}</span>
          </Button>
          <Button
            size="sm"
            className="h-8 shrink-0 px-2 text-xs sm:h-9 sm:px-3 sm:text-sm"
            onClick={handleSave}
            disabled={!hasUnsavedChanges || saving || loadingFile || !filename}
          >
            <Save className="h-3.5 w-3.5 sm:mr-2 sm:h-4 sm:w-4" />
            <span className="ml-1 sm:ml-0">{saving ? '保存中' : hasUnsavedChanges ? '保存' : '已保存'}</span>
          </Button>
        </div>
      </div>

      <div className="grid min-h-0 flex-1 grid-rows-[auto_minmax(0,1fr)] gap-3 lg:grid-cols-[18rem_minmax(0,1fr)] lg:grid-rows-1 lg:gap-4">
        <Card className="flex max-h-52 min-h-0 flex-col overflow-hidden sm:max-h-none">
          <CardHeader className="shrink-0 p-3 pb-2 sm:p-6 sm:pb-3">
            <div className="flex items-center gap-2">
              <div className="flex h-10 shrink-0 items-center px-1 text-4xl font-bold leading-none text-foreground sm:h-9">
                {filteredFiles.length}
              </div>
              <div className="relative min-w-0 flex-1">
                <Search className="pointer-events-none absolute left-2 top-2.5 h-4 w-4 text-muted-foreground" />
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
                <div className="flex items-center justify-center gap-2 p-6 text-sm text-muted-foreground">
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
                      filename === file.name ? 'bg-accent text-accent-foreground' : 'text-muted-foreground',
                    )}
                  >
                    <div className="flex items-center gap-2">
                      <div className="truncate font-medium" title={file.display_name || file.name}>
                        {file.display_name || file.name}
                      </div>
                      {file.advanced && <Badge variant="outline" className="shrink-0 text-[10px]">高级</Badge>}
                      {file.customized && <Badge variant="secondary" className="shrink-0 text-[10px]">自定义</Badge>}
                    </div>
                    <div className="mt-0.5 truncate text-xs text-muted-foreground">{file.name}</div>
                    {file.description && (
                      <div className="mt-1 line-clamp-2 text-xs text-muted-foreground">{file.description}</div>
                    )}
                  </button>
                ))
              ) : (
                <div className="p-6 text-center text-sm text-muted-foreground">没有可编辑的 Prompt 文件</div>
              )}
            </div>
          </ScrollArea>
        </Card>

        <Card className="flex min-h-0 flex-col overflow-hidden">
          <CardHeader className="flex flex-col gap-2 space-y-0 p-3 pb-2 sm:flex-row sm:items-start sm:justify-between sm:gap-3 sm:p-6 sm:pb-3">
            <div className="min-w-0">
              <CardTitle className="flex items-center gap-2 truncate text-sm">
                <span className="truncate">{selectedFile?.display_name || filename || '未选择文件'}</span>
                {selectedFile?.advanced && <Badge variant="outline" className="shrink-0">高级</Badge>}
                {isCustomized && <Badge variant="secondary" className="shrink-0">自定义</Badge>}
              </CardTitle>
              <p className="mt-1 text-xs text-muted-foreground">
                {language}
                {selectedFile ? ` · ${formatFileSize(selectedFile.size)}` : ''}
                {hasUnsavedChanges ? ' · 有未保存修改' : ''}
              </p>
              {selectedFile?.description && (
                <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">{selectedFile.description}</p>
              )}
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={handleReset}
                disabled={!isCustomized || resetting || loadingFile || !filename}
                className="h-8 flex-1 px-2 text-xs sm:h-9 sm:flex-none sm:px-3 sm:text-sm"
              >
                <RotateCcw className={cn('mr-1 h-3.5 w-3.5 sm:mr-2 sm:h-4 sm:w-4', resetting && 'animate-spin')} />
                恢复默认
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={handleShowDefault}
                disabled={loadingDefaultPrompt || loadingFile || !filename}
                className="h-8 flex-1 px-2 text-xs sm:h-9 sm:flex-none sm:px-3 sm:text-sm"
              >
                <Eye className={cn('mr-1 h-3.5 w-3.5 sm:mr-2 sm:h-4 sm:w-4', loadingDefaultPrompt && 'animate-pulse')} />
                查看默认
              </Button>
            </div>
          </CardHeader>
          <CardContent className="min-h-0 flex-1 p-0">
            {loadingFile ? (
              <div className="flex h-full min-h-[320px] items-center justify-center gap-2 text-sm text-muted-foreground">
                <ThinkingIllustration />
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
            <div className="flex min-h-0 flex-1 items-center justify-center gap-2 text-sm text-muted-foreground">
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
