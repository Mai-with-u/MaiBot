import { useState, useRef, useEffect, useCallback } from 'react'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Info, Upload, Download, FileText, Trash2, FolderOpen, Save, RefreshCw, Package, ChevronDown } from 'lucide-react'
import { ScrollArea } from '@/components/ui/scroll-area'
import { useToast } from '@/hooks/use-toast'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
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
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import {
  getSavedConfigPath,
  saveConfigPath,
  loadConfigFromPath,
  saveConfigToPath,
} from '@/lib/adapter-config-api'
import type { AdapterConfig, PresetKey } from './adapter/types'
import { DEFAULT_CONFIG, PRESETS } from './adapter/types'
import { parseTOML, generateTOML, validatePath } from './adapter/utils'

export function AdapterConfigPage() {
  // 工作模式：'upload' = 上传文件模式, 'path' = 指定路径模式, 'preset' = 预设模式
  const [mode, setMode] = useState<'upload' | 'path' | 'preset'>('upload')
  const [config, setConfig] = useState<AdapterConfig | null>(null)
  const [fileName, setFileName] = useState<string>('')
  const [configPath, setConfigPath] = useState<string>('')
  const [selectedPreset, setSelectedPreset] = useState<PresetKey>('oneclick')
  const [pathError, setPathError] = useState<string>('')
  const [isSaving, setIsSaving] = useState(false)
  const [isLoading, setIsLoading] = useState(false)
  const [showModeSwitchDialog, setShowModeSwitchDialog] = useState(false)
  const [showClearPathDialog, setShowClearPathDialog] = useState(false)
  const [pendingMode, setPendingMode] = useState<'upload' | 'path' | 'preset' | null>(null)
  const [isModeConfigOpen, setIsModeConfigOpen] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const { toast } = useToast()
  const saveTimeoutRef = useRef<number | null>(null)

  // 处理路径输入变化
  const handlePathChange = (value: string) => {
    setConfigPath(value)
    
    // 实时验证
    if (value.trim()) {
      const validation = validatePath(value)
      setPathError(validation.error)
    } else {
      setPathError('')
    }
  }

  // 从预设加载配置
  const handleLoadFromPreset = useCallback(async (presetKey: PresetKey) => {
    const preset = PRESETS[presetKey]
    setIsLoading(true)
    try {
      const content = await loadConfigFromPath(preset.path)
      const parsedConfig = parseTOML(content)
      setConfig(parsedConfig)
      setSelectedPreset(presetKey)
      setConfigPath(preset.path)
      
      // 保存路径偏好
      await saveConfigPath(preset.path)
      
      toast({
        title: '加载成功',
        description: `已从${preset.name}预设加载配置`,
      })
    } catch (error) {
      console.error('加载预设配置失败:', error)
      toast({
        title: '加载失败',
        description: error instanceof Error ? error.message : '无法读取预设配置文件',
        variant: 'destructive',
      })
    } finally {
      setIsLoading(false)
    }
  }, [toast])

  // 从指定路径加载配置
  const handleLoadFromPath = useCallback(async (path: string) => {
    // 验证路径
    const validation = validatePath(path)
    if (!validation.valid) {
      setPathError(validation.error)
      toast({
        title: '路径无效',
        description: validation.error,
        variant: 'destructive',
      })
      return
    }

    setPathError('')
    setIsLoading(true)
    try {
      const content = await loadConfigFromPath(path)
      const parsedConfig = parseTOML(content)
      setConfig(parsedConfig)
      setConfigPath(path)
      
      // 保存路径偏好
      await saveConfigPath(path)
      
      toast({
        title: '加载成功',
        description: `已从配置文件加载`,
      })
    } catch (error) {
      console.error('加载配置失败:', error)
      toast({
        title: '加载失败',
        description: error instanceof Error ? error.message : '无法读取配置文件',
        variant: 'destructive',
      })
    } finally {
      setIsLoading(false)
    }
  }, [toast])

  // 组件挂载时加载保存的路径
  useEffect(() => {
    const loadSavedPath = async () => {
      try {
        const savedPath = await getSavedConfigPath()
        if (savedPath && savedPath.path) {
          setConfigPath(savedPath.path)
          
          // 检查是否是预设路径
          const presetEntry = Object.entries(PRESETS).find(([, preset]) => preset.path === savedPath.path)
          if (presetEntry) {
            setMode('preset')
            setSelectedPreset(presetEntry[0] as PresetKey)
            await handleLoadFromPreset(presetEntry[0] as PresetKey)
          } else {
            setMode('path')
            await handleLoadFromPath(savedPath.path)
          }
        }
      } catch (error) {
        console.error('加载保存的路径失败:', error)
      }
    }
    loadSavedPath()
  }, [handleLoadFromPath, handleLoadFromPreset])

  // 自动保存配置到路径（防抖）
  const autoSaveToPath = useCallback((updatedConfig: AdapterConfig) => {
    if ((mode !== 'path' && mode !== 'preset') || !configPath) return

    // 清除之前的定时器
    if (saveTimeoutRef.current) {
      clearTimeout(saveTimeoutRef.current)
    }

    // 设置新的定时器（1秒后保存）
    saveTimeoutRef.current = setTimeout(async () => {
      setIsSaving(true)
      try {
        const tomlContent = generateTOML(updatedConfig)
        await saveConfigToPath(configPath, tomlContent)
        toast({
          title: '自动保存成功',
          description: '配置已保存到文件',
        })
      } catch (error) {
        console.error('自动保存失败:', error)
        toast({
          title: '自动保存失败',
          description: error instanceof Error ? error.message : '保存配置失败',
          variant: 'destructive',
        })
      } finally {
        setIsSaving(false)
      }
    }, 1000)
  }, [mode, configPath, toast])

  // 手动保存配置
  const handleManualSave = async () => {
    if (!config || !configPath) return

    // 再次验证路径
    const validation = validatePath(configPath)
    if (!validation.valid) {
      toast({
        title: '保存失败',
        description: validation.error,
        variant: 'destructive',
      })
      return
    }

    setIsSaving(true)
    try {
      const tomlContent = generateTOML(config)
      await saveConfigToPath(configPath, tomlContent)
      toast({
        title: '保存成功',
        description: '配置已保存到文件',
      })
    } catch (error) {
      console.error('保存失败:', error)
      toast({
        title: '保存失败',
        description: error instanceof Error ? error.message : '保存配置失败',
        variant: 'destructive',
      })
    } finally {
      setIsSaving(false)
    }
  }

  // 刷新配置（重新从文件加载）
  const handleRefresh = async () => {
    if (!configPath) return
    await handleLoadFromPath(configPath)
  }

  // 切换模式
  const handleModeChange = (newMode: 'upload' | 'path' | 'preset') => {
    if (newMode === mode) return
    
    // 如果有未保存的配置，显示确认对话框
    if (config) {
      setPendingMode(newMode)
      setShowModeSwitchDialog(true)
      return
    }
    
    // 直接切换模式
    performModeSwitch(newMode)
  }

  // 执行模式切换
  const performModeSwitch = (newMode: 'upload' | 'path' | 'preset') => {
    setConfig(null)
    setFileName('')
    setPathError('')
    setMode(newMode)
    
    // 如果切换到预设模式，自动加载默认预设
    if (newMode === 'preset') {
      handleLoadFromPreset('oneclick')
    }
    
    const modeNames = {
      upload: '现在可以上传配置文件',
      path: '现在可以指定配置文件路径',
      preset: '现在可以使用预设配置',
    }
    
    toast({
      title: '已切换模式',
      description: modeNames[newMode],
    })
  }

  // 确认模式切换
  const confirmModeSwitch = () => {
    if (pendingMode) {
      performModeSwitch(pendingMode)
      setPendingMode(null)
    }
    setShowModeSwitchDialog(false)
  }

  // 清空路径
  const handleClearPath = () => {
    if (config) {
      setShowClearPathDialog(true)
      return
    }
    
    // 直接清空
    performClearPath()
  }

  // 执行清空路径
  const performClearPath = () => {
    setConfigPath('')
    setConfig(null)
    setPathError('')
    toast({
      title: '已清空',
      description: '路径和配置已清空',
    })
  }

  // 确认清空路径
  const confirmClearPath = () => {
    performClearPath()
    setShowClearPathDialog(false)
  }

  // 上传文件处理
  const handleFileUpload = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file) return

    const reader = new FileReader()
    reader.onload = (e) => {
      try {
        const content = e.target?.result as string
        const parsedConfig = parseTOML(content)
        setConfig(parsedConfig)
        setFileName(file.name)
        toast({
          title: '上传成功',
          description: `已加载配置文件：${file.name}`,
        })
      } catch (error) {
        console.error('解析配置文件失败:', error)
        toast({
          title: '解析失败',
          description: '配置文件格式错误，请检查文件内容',
          variant: 'destructive',
        })
      }
    }
    reader.readAsText(file)
  }

  // 下载配置文件
  const handleDownload = () => {
    if (!config) return

    const tomlContent = generateTOML(config)
    const blob = new Blob([tomlContent], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = fileName || 'config.toml'
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)

    toast({
      title: '下载成功',
      description: '配置文件已下载，请手动覆盖并重启适配器',
    })
  }

  // 使用默认配置
  const handleUseDefault = () => {
    setConfig(JSON.parse(JSON.stringify(DEFAULT_CONFIG)))
    setFileName('config.toml')
    toast({
      title: '已加载默认配置',
      description: '可以开始编辑配置',
    })
  }

  return (
    <ScrollArea className="h-full">
      <div className="space-y-4 sm:space-y-6 p-4 sm:p-6">
        {/* 页面标题 */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div>
            <h1 className="text-2xl sm:text-3xl font-bold">麦麦适配器配置</h1>
            <p className="text-muted-foreground mt-1 sm:mt-2 text-sm sm:text-base">
              管理麦麦的 QQ 适配器的配置文件
            </p>
          </div>
        </div>

        {/* 模式选择 */}
        <Collapsible open={isModeConfigOpen} onOpenChange={setIsModeConfigOpen}>
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <div>
                <CardTitle>工作模式</CardTitle>
                <CardDescription>选择配置文件的管理方式</CardDescription>
              </div>
              <CollapsibleTrigger asChild>
                <Button variant="ghost" size="sm" className="w-9 p-0">
                  <ChevronDown className={`h-4 w-4 transition-transform duration-200 ${
                    isModeConfigOpen ? 'transform rotate-180' : ''
                  }`} />
                  <span className="sr-only">切换</span>
                </Button>
              </CollapsibleTrigger>
            </div>
          </CardHeader>
          <CollapsibleContent>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3 md:gap-4">
              {/* 预设模式 */}
              <div
                className={`border-2 rounded-lg p-3 md:p-4 cursor-pointer transition-all ${
                  mode === 'preset'
                    ? 'border-primary bg-primary/5'
                    : 'border-muted hover:border-primary/50 active:border-primary/70'
                }`}
                onClick={() => handleModeChange('preset')}
              >
                <div className="flex items-start gap-2 md:gap-3">
                  <Package className="h-4 w-4 md:h-5 md:w-5 mt-0.5 flex-shrink-0" />
                  <div className="min-w-0">
                    <h3 className="font-semibold text-sm md:text-base">预设模式</h3>
                    <p className="text-xs md:text-sm text-muted-foreground mt-1 line-clamp-2">
                      使用预设的部署配置
                    </p>
                  </div>
                </div>
              </div>

              {/* 上传模式 */}
              <div
                className={`border-2 rounded-lg p-3 md:p-4 cursor-pointer transition-all ${
                  mode === 'upload'
                    ? 'border-primary bg-primary/5'
                    : 'border-muted hover:border-primary/50 active:border-primary/70'
                }`}
                onClick={() => handleModeChange('upload')}
              >
                <div className="flex items-start gap-2 md:gap-3">
                  <Upload className="h-4 w-4 md:h-5 md:w-5 mt-0.5 flex-shrink-0" />
                  <div className="min-w-0">
                    <h3 className="font-semibold text-sm md:text-base">上传文件模式</h3>
                    <p className="text-xs md:text-sm text-muted-foreground mt-1 line-clamp-2">
                      上传配置文件，编辑后下载并手动覆盖
                    </p>
                  </div>
                </div>
              </div>

              {/* 路径模式 */}
              <div
                className={`border-2 rounded-lg p-3 md:p-4 cursor-pointer transition-all ${
                  mode === 'path'
                    ? 'border-primary bg-primary/5'
                    : 'border-muted hover:border-primary/50 active:border-primary/70'
                }`}
                onClick={() => handleModeChange('path')}
              >
                <div className="flex items-start gap-2 md:gap-3">
                  <FolderOpen className="h-4 w-4 md:h-5 md:w-5 mt-0.5 flex-shrink-0" />
                  <div className="min-w-0">
                    <h3 className="font-semibold text-sm md:text-base">指定路径模式</h3>
                    <p className="text-xs md:text-sm text-muted-foreground mt-1 line-clamp-2">
                      指定配置文件路径，自动加载和保存
                    </p>
                  </div>
                </div>
              </div>
            </div>

            {/* 预设模式配置 */}
            {mode === 'preset' && (
              <div className="space-y-3 pt-2 border-t">
                <Label className="text-sm md:text-base">选择部署方式</Label>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  {Object.entries(PRESETS).map(([key, preset]) => {
                    const Icon = preset.icon
                    const isSelected = selectedPreset === key
                    return (
                      <div
                        key={key}
                        className={`border-2 rounded-lg p-3 cursor-pointer transition-all ${
                          isSelected
                            ? 'border-primary bg-primary/5'
                            : 'border-muted hover:border-primary/50'
                        }`}
                        onClick={() => {
                          setSelectedPreset(key as PresetKey)
                          handleLoadFromPreset(key as PresetKey)
                        }}
                      >
                        <div className="flex items-start gap-3">
                          <Icon className="h-5 w-5 mt-0.5 flex-shrink-0" />
                          <div className="min-w-0 flex-1">
                            <h4 className="font-semibold text-sm">{preset.name}</h4>
                            <p className="text-xs text-muted-foreground mt-1">
                              {preset.description}
                            </p>
                            <p className="text-xs text-muted-foreground mt-1 font-mono break-all">
                              {preset.path}
                            </p>
                          </div>
                        </div>
                      </div>
                    )
                  })}
                </div>
              </div>
            )}

            {/* 路径模式配置 */}
            {mode === 'path' && (
              <div className="space-y-3 pt-2 border-t">
                <div className="space-y-2">
                  <Label htmlFor="config-path" className="text-sm md:text-base">配置文件路径</Label>
                  <div className="flex flex-col sm:flex-row gap-2">
                    <div className="flex-1 space-y-1">
                      <Input
                        id="config-path"
                        value={configPath}
                        onChange={(e) => handlePathChange(e.target.value)}
                        placeholder="例: C:\Adapter\config.toml"
                        className={`text-sm ${pathError ? 'border-destructive' : ''}`}
                      />
                      {pathError && (
                        <p className="text-xs text-destructive">{pathError}</p>
                      )}
                    </div>
                    <Button
                      onClick={() => handleLoadFromPath(configPath)}
                      disabled={isLoading || !configPath || !!pathError}
                      className="w-full sm:w-auto"
                    >
                      {isLoading ? (
                        <>
                          <RefreshCw className="h-4 w-4 animate-spin mr-2" />
                          <span className="sm:hidden">加载中...</span>
                        </>
                      ) : (
                        <>
                          <span className="sm:hidden">加载配置</span>
                          <span className="hidden sm:inline">加载</span>
                        </>
                      )}
                    </Button>
                  </div>
                </div>
                
                <details className="rounded-lg bg-muted/50 p-3 group">
                  <summary className="text-xs font-medium cursor-pointer select-none list-none flex items-center justify-between">
                    <span>路径格式说明</span>
                    <svg className="h-4 w-4 transition-transform group-open:rotate-180" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                    </svg>
                  </summary>
                  <div className="mt-2 space-y-2 text-xs text-muted-foreground">
                    <div className="space-y-1">
                      <div className="flex items-center gap-2">
                        <span className="font-mono bg-background px-1.5 py-0.5 rounded text-[10px] md:text-xs whitespace-nowrap">Windows</span>
                      </div>
                      <div className="pl-2 space-y-0.5 text-[10px] md:text-xs break-all">
                        <div>C:\Adapter\config.toml</div>
                        <div className="hidden sm:block">D:\MaiBot\adapter\config.toml</div>
                        <div className="hidden sm:block">\\server\share\config.toml</div>
                      </div>
                    </div>
                    <div className="space-y-1">
                      <div className="flex items-center gap-2">
                        <span className="font-mono bg-background px-1.5 py-0.5 rounded text-[10px] md:text-xs whitespace-nowrap">Linux</span>
                      </div>
                      <div className="pl-2 space-y-0.5 text-[10px] md:text-xs break-all">
                        <div>/opt/adapter/config.toml</div>
                        <div className="hidden sm:block">/home/user/adapter/config.toml</div>
                        <div className="hidden sm:block">~/adapter/config.toml</div>
                      </div>
                    </div>
                    <p className="pt-1 border-t text-[10px] md:text-xs">
                      💡 配置会自动保存到指定文件，修改后 1 秒自动保存
                    </p>
                  </div>
                </details>
              </div>
            )}
          </CardContent>
          </CollapsibleContent>
        </Card>
        </Collapsible>

        {/* 操作提示 */}
        <Alert>
          <Info className="h-4 w-4" />
          <AlertDescription>
            {mode === 'preset' ? (
              <>
                <strong>预设模式：</strong>选择预设的部署方式，配置会自动加载，修改后 1 秒自动保存{isSaving && ' (正在保存...)'}
              </>
            ) : mode === 'upload' ? (
              <>
                <strong>上传文件模式：</strong>上传配置文件 → 在线编辑 → 下载文件 → 手动覆盖并重启适配器
              </>
            ) : (
              <>
                <strong>指定路径模式：</strong>指定配置文件路径后，配置会自动加载，修改后 1 秒自动保存{isSaving && ' (正在保存...)'}
              </>
            )}
          </AlertDescription>
        </Alert>

        {/* 上传模式的操作按钮 */}
        {mode === 'upload' && !config && (
          <div className="flex flex-col sm:flex-row gap-2 w-full">
            <input
              ref={fileInputRef}
              type="file"
              accept=".toml"
              className="hidden"
              onChange={handleFileUpload}
            />
            <Button
              onClick={() => fileInputRef.current?.click()}
              size="sm"
              variant="outline"
              className="w-full sm:w-auto"
            >
              <Upload className="mr-2 h-4 w-4" />
              上传配置
            </Button>
            <Button onClick={handleUseDefault} size="sm" className="w-full sm:w-auto">
              <FileText className="mr-2 h-4 w-4" />
              使用默认配置
            </Button>
          </div>
        )}

        {/* 上传模式的下载按钮 */}
        {mode === 'upload' && config && (
          <div className="flex gap-2">
            <Button onClick={handleDownload} size="sm" className="w-full sm:w-auto">
              <Download className="mr-2 h-4 w-4" />
              下载配置
            </Button>
          </div>
        )}

        {/* 预设和路径模式的操作按钮 */}
        {(mode === 'preset' || mode === 'path') && config && (
          <div className="flex flex-col sm:flex-row gap-2">
            <Button onClick={handleManualSave} size="sm" disabled={isSaving || !!pathError} className="w-full sm:w-auto">
              <Save className="mr-2 h-4 w-4" />
              {isSaving ? '保存中...' : '立即保存'}
            </Button>
            <Button onClick={handleRefresh} size="sm" variant="outline" disabled={isLoading} className="w-full sm:w-auto">
              <RefreshCw className={`mr-2 h-4 w-4 ${isLoading ? 'animate-spin' : ''}`} />
              刷新
            </Button>
            {mode === 'path' && (
              <Button onClick={handleClearPath} size="sm" variant="destructive" className="w-full sm:w-auto">
                <Trash2 className="mr-2 h-4 w-4" />
                清空路径
              </Button>
            )}
          </div>
        )}

        {/* 配置编辑区域 */}
        {!config ? (
          <div className="rounded-lg border bg-card p-6 md:p-12">
            <div className="text-center space-y-3 md:space-y-4">
              <FileText className="h-12 w-12 md:h-16 md:w-16 mx-auto text-muted-foreground" />
              <div>
                <h3 className="text-base md:text-lg font-semibold">尚未加载配置</h3>
                <p className="text-xs md:text-sm text-muted-foreground mt-2 px-4">
                  {mode === 'preset'
                    ? '请选择预设的部署方式'
                    : mode === 'upload'
                    ? '请上传现有配置文件，或使用默认配置开始编辑'
                    : '请指定配置文件路径并点击加载按钮'}
                </p>
              </div>
            </div>
          </div>
        ) : (
          <Tabs defaultValue="napcat" className="w-full">
            <div className="overflow-x-auto -mx-4 px-4 sm:mx-0 sm:px-0">
              <TabsList className="inline-flex w-auto min-w-full sm:grid sm:w-full sm:grid-cols-5">
                <TabsTrigger value="napcat" className="flex-shrink-0 text-xs sm:text-sm whitespace-nowrap">
                  <span className="hidden sm:inline">Napcat 连接</span>
                  <span className="sm:hidden">Napcat</span>
                </TabsTrigger>
                <TabsTrigger value="maibot" className="flex-shrink-0 text-xs sm:text-sm whitespace-nowrap">
                  <span className="hidden sm:inline">麦麦连接</span>
                  <span className="sm:hidden">麦麦</span>
                </TabsTrigger>
                <TabsTrigger value="chat" className="flex-shrink-0 text-xs sm:text-sm whitespace-nowrap">
                  <span className="hidden sm:inline">聊天控制</span>
                  <span className="sm:hidden">聊天</span>
                </TabsTrigger>
                <TabsTrigger value="voice" className="flex-shrink-0 text-xs sm:text-sm whitespace-nowrap">
                  <span className="hidden sm:inline">语音与转发</span>
                  <span className="sm:hidden">语音</span>
                </TabsTrigger>
                <TabsTrigger value="debug" className="flex-shrink-0 text-xs sm:text-sm whitespace-nowrap">调试</TabsTrigger>
              </TabsList>
            </div>

            {/* Napcat 服务器配置 */}
            <TabsContent value="napcat" className="space-y-4">
              <NapcatServerSection 
                config={config} 
                onChange={(newConfig) => {
                  setConfig(newConfig)
                  autoSaveToPath(newConfig)
                }} 
              />
            </TabsContent>

            {/* 麦麦服务器配置 */}
            <TabsContent value="maibot" className="space-y-4">
              <MaiBotServerSection 
                config={config} 
                onChange={(newConfig) => {
                  setConfig(newConfig)
                  autoSaveToPath(newConfig)
                }} 
              />
            </TabsContent>

            {/* 聊天控制配置 */}
            <TabsContent value="chat" className="space-y-4">
              <ChatControlSection 
                config={config} 
                onChange={(newConfig) => {
                  setConfig(newConfig)
                  autoSaveToPath(newConfig)
                }} 
              />
            </TabsContent>

            {/* 语音配置 */}
            <TabsContent value="voice" className="space-y-4">
              <VoiceSection 
                config={config} 
                onChange={(newConfig) => {
                  setConfig(newConfig)
                  autoSaveToPath(newConfig)
                }} 
              />
            </TabsContent>

            {/* 调试配置 */}
            <TabsContent value="debug" className="space-y-4">
              <DebugSection 
                config={config} 
                onChange={(newConfig) => {
                  setConfig(newConfig)
                  autoSaveToPath(newConfig)
                }} 
              />
            </TabsContent>
          </Tabs>
        )}

        {/* 模式切换确认对话框 */}
        <AlertDialog open={showModeSwitchDialog} onOpenChange={setShowModeSwitchDialog}>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>确认切换模式</AlertDialogTitle>
              <AlertDialogDescription>
                切换模式将清空当前配置，确定要继续吗？
                <br />
                <span className="text-destructive font-medium">请确保已保存重要配置</span>
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel onClick={() => {
                setShowModeSwitchDialog(false)
                setPendingMode(null)
              }}>
                取消
              </AlertDialogCancel>
              <AlertDialogAction onClick={confirmModeSwitch}>
                确认切换
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>

        {/* 清空路径确认对话框 */}
        <AlertDialog open={showClearPathDialog} onOpenChange={setShowClearPathDialog}>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>确认清空路径</AlertDialogTitle>
              <AlertDialogDescription>
                清空路径将清除当前配置，确定要继续吗？
                <br />
                <span className="text-muted-foreground text-sm">此操作不会删除配置文件，只是清除界面中的配置</span>
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel onClick={() => setShowClearPathDialog(false)}>
                取消
              </AlertDialogCancel>
              <AlertDialogAction onClick={confirmClearPath} className="bg-destructive hover:bg-destructive/90">
                确认清空
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </div>
    </ScrollArea>
  )
}

// Napcat 服务器配置组件
function NapcatServerSection({
  config,
  onChange,
}: {
  config: AdapterConfig
  onChange: (config: AdapterConfig) => void
}) {
  return (
    <div className="rounded-lg border bg-card p-4 md:p-6 space-y-4 md:space-y-6">
      <div>
        <h3 className="text-base md:text-lg font-semibold mb-3 md:mb-4">Napcat WebSocket 服务设置</h3>
        <div className="grid gap-3 md:gap-4">
          <div className="grid gap-2">
            <Label htmlFor="napcat-host" className="text-sm md:text-base">主机地址</Label>
            <Input
              id="napcat-host"
              value={config.napcat_server.host}
              onChange={(e) =>
                onChange({
                  ...config,
                  napcat_server: { ...config.napcat_server, host: e.target.value },
                })
              }
              placeholder="localhost"
              className="text-sm md:text-base"
            />
            <p className="text-xs text-muted-foreground">Napcat 设定的主机地址</p>
          </div>

          <div className="grid gap-2">
            <Label htmlFor="napcat-port" className="text-sm md:text-base">端口</Label>
            <Input
              id="napcat-port"
              type="number"
              value={config.napcat_server.port || ''}
              onChange={(e) =>
                onChange({
                  ...config,
                  napcat_server: { ...config.napcat_server, port: e.target.value ? parseInt(e.target.value) : 0 },
                })
              }
              placeholder="8095"
              className="text-sm md:text-base"
            />
            <p className="text-xs text-muted-foreground">Napcat 设定的端口（留空使用默认值 8095）</p>
          </div>

          <div className="grid gap-2">
            <Label htmlFor="napcat-token" className="text-sm md:text-base">访问令牌（Token）</Label>
            <Input
              id="napcat-token"
              type="password"
              value={config.napcat_server.token}
              onChange={(e) =>
                onChange({
                  ...config,
                  napcat_server: { ...config.napcat_server, token: e.target.value },
                })
              }
              placeholder="留空表示无需令牌"
              className="text-sm md:text-base"
            />
            <p className="text-xs text-muted-foreground">Napcat 设定的访问令牌，若无则留空</p>
          </div>

          <div className="grid gap-2">
            <Label htmlFor="napcat-heartbeat" className="text-sm md:text-base">心跳间隔（秒）</Label>
            <Input
              id="napcat-heartbeat"
              type="number"
              value={config.napcat_server.heartbeat_interval || ''}
              onChange={(e) =>
                onChange({
                  ...config,
                  napcat_server: {
                    ...config.napcat_server,
                    heartbeat_interval: e.target.value ? parseInt(e.target.value) : 0,
                  },
                })
              }
              placeholder="30"
              className="text-sm md:text-base"
            />
            <p className="text-xs text-muted-foreground">与 Napcat 设置的心跳间隔保持一致（留空使用默认值 30）</p>
          </div>
        </div>
      </div>
    </div>
  )
}

// 麦麦服务器配置组件
function MaiBotServerSection({
  config,
  onChange,
}: {
  config: AdapterConfig
  onChange: (config: AdapterConfig) => void
}) {
  return (
    <div className="rounded-lg border bg-card p-4 md:p-6 space-y-4 md:space-y-6">
      <div>
        <h3 className="text-base md:text-lg font-semibold mb-3 md:mb-4">麦麦 WebSocket 服务设置</h3>
        <div className="grid gap-3 md:gap-4">
          <div className="grid gap-2">
            <Label htmlFor="maibot-host" className="text-sm md:text-base">主机地址</Label>
            <Input
              id="maibot-host"
              value={config.maibot_server.host}
              onChange={(e) =>
                onChange({
                  ...config,
                  maibot_server: { ...config.maibot_server, host: e.target.value },
                })
              }
              placeholder="localhost"
              className="text-sm md:text-base"
            />
            <p className="text-xs text-muted-foreground">麦麦在 .env 文件中设置的 HOST 字段</p>
          </div>

          <div className="grid gap-2">
            <Label htmlFor="maibot-port" className="text-sm md:text-base">端口</Label>
            <Input
              id="maibot-port"
              type="number"
              value={config.maibot_server.port || ''}
              onChange={(e) =>
                onChange({
                  ...config,
                  maibot_server: { ...config.maibot_server, port: e.target.value ? parseInt(e.target.value) : 0 },
                })
              }
              placeholder="8000"
              className="text-sm md:text-base"
            />
            <p className="text-xs text-muted-foreground">麦麦在 .env 文件中设置的 PORT 字段（留空使用默认值 8000）</p>
          </div>
        </div>
      </div>
    </div>
  )
}

// 聊天控制配置组件
function ChatControlSection({
  config,
  onChange,
}: {
  config: AdapterConfig
  onChange: (config: AdapterConfig) => void
}) {
  const addToList = (listType: 'group' | 'private' | 'ban') => {
    const newConfig = { ...config }
    if (listType === 'group') {
      newConfig.chat.group_list = [...newConfig.chat.group_list, 0]
    } else if (listType === 'private') {
      newConfig.chat.private_list = [...newConfig.chat.private_list, 0]
    } else {
      newConfig.chat.ban_user_id = [...newConfig.chat.ban_user_id, 0]
    }
    onChange(newConfig)
  }

  const removeFromList = (listType: 'group' | 'private' | 'ban', index: number) => {
    const newConfig = { ...config }
    if (listType === 'group') {
      newConfig.chat.group_list = newConfig.chat.group_list.filter((_, i) => i !== index)
    } else if (listType === 'private') {
      newConfig.chat.private_list = newConfig.chat.private_list.filter((_, i) => i !== index)
    } else {
      newConfig.chat.ban_user_id = newConfig.chat.ban_user_id.filter((_, i) => i !== index)
    }
    onChange(newConfig)
  }

  const updateListItem = (listType: 'group' | 'private' | 'ban', index: number, value: number) => {
    const newConfig = { ...config }
    if (listType === 'group') {
      newConfig.chat.group_list[index] = value
    } else if (listType === 'private') {
      newConfig.chat.private_list[index] = value
    } else {
      newConfig.chat.ban_user_id[index] = value
    }
    onChange(newConfig)
  }

  return (
    <div className="rounded-lg border bg-card p-4 md:p-6 space-y-4 md:space-y-6">
      <div>
        <h3 className="text-base md:text-lg font-semibold mb-3 md:mb-4">聊天黑白名单功能</h3>
        <div className="grid gap-4 md:gap-6">
          {/* 群组名单 */}
          <div className="space-y-3 md:space-y-4">
            <div className="grid gap-2">
              <Label className="text-sm md:text-base">群组名单类型</Label>
              <Select
                value={config.chat.group_list_type}
                onValueChange={(value: 'whitelist' | 'blacklist') =>
                  onChange({
                    ...config,
                    chat: { ...config.chat, group_list_type: value },
                  })
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="whitelist">白名单（仅名单内可聊天）</SelectItem>
                  <SelectItem value="blacklist">黑名单（名单内禁止聊天）</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-2">
              <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 sm:gap-0">
                <Label className="text-sm md:text-base">群组列表</Label>
                <Button onClick={() => addToList('group')} size="sm" variant="outline" className="w-full sm:w-auto">
                  <FileText className="mr-1 h-4 w-4" />
                  添加群号
                </Button>
              </div>
              {config.chat.group_list.map((groupId, index) => (
                <div key={index} className="flex gap-2">
                  <Input
                    type="number"
                    value={groupId}
                    onChange={(e) => updateListItem('group', index, parseInt(e.target.value) || 0)}
                    placeholder="输入群号"
                    className="text-sm md:text-base"
                  />
                  <AlertDialog>
                    <AlertDialogTrigger asChild>
                      <Button size="icon" variant="outline">
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </AlertDialogTrigger>
                    <AlertDialogContent>
                      <AlertDialogHeader>
                        <AlertDialogTitle>确认删除</AlertDialogTitle>
                        <AlertDialogDescription>
                          确定要删除群号 {groupId} 吗？此操作无法撤销。
                        </AlertDialogDescription>
                      </AlertDialogHeader>
                      <AlertDialogFooter>
                        <AlertDialogCancel>取消</AlertDialogCancel>
                        <AlertDialogAction onClick={() => removeFromList('group', index)}>
                          删除
                        </AlertDialogAction>
                      </AlertDialogFooter>
                    </AlertDialogContent>
                  </AlertDialog>
                </div>
              ))}
              {config.chat.group_list.length === 0 && (
                <p className="text-sm text-muted-foreground">暂无群组</p>
              )}
            </div>
          </div>

          {/* 私聊名单 */}
          <div className="space-y-3 md:space-y-4">
            <div className="grid gap-2">
              <Label className="text-sm md:text-base">私聊名单类型</Label>
              <Select
                value={config.chat.private_list_type}
                onValueChange={(value: 'whitelist' | 'blacklist') =>
                  onChange({
                    ...config,
                    chat: { ...config.chat, private_list_type: value },
                  })
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="whitelist">白名单（仅名单内可聊天）</SelectItem>
                  <SelectItem value="blacklist">黑名单（名单内禁止聊天）</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-2">
              <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 sm:gap-0">
                <Label className="text-sm md:text-base">私聊列表</Label>
                <Button onClick={() => addToList('private')} size="sm" variant="outline" className="w-full sm:w-auto">
                  <FileText className="mr-1 h-4 w-4" />
                  添加用户
                </Button>
              </div>
              {config.chat.private_list.map((userId, index) => (
                <div key={index} className="flex gap-2">
                  <Input
                    type="number"
                    value={userId}
                    onChange={(e) => updateListItem('private', index, parseInt(e.target.value) || 0)}
                    placeholder="输入QQ号"
                    className="text-sm md:text-base"
                  />
                  <AlertDialog>
                    <AlertDialogTrigger asChild>
                      <Button size="icon" variant="outline">
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </AlertDialogTrigger>
                    <AlertDialogContent>
                      <AlertDialogHeader>
                        <AlertDialogTitle>确认删除</AlertDialogTitle>
                        <AlertDialogDescription>
                          确定要删除用户 {userId} 吗？此操作无法撤销。
                        </AlertDialogDescription>
                      </AlertDialogHeader>
                      <AlertDialogFooter>
                        <AlertDialogCancel>取消</AlertDialogCancel>
                        <AlertDialogAction onClick={() => removeFromList('private', index)}>
                          删除
                        </AlertDialogAction>
                      </AlertDialogFooter>
                    </AlertDialogContent>
                  </AlertDialog>
                </div>
              ))}
              {config.chat.private_list.length === 0 && (
                <p className="text-sm text-muted-foreground">暂无用户</p>
              )}
            </div>
          </div>

          {/* 全局禁止名单 */}
          <div className="space-y-2">
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 sm:gap-0">
              <div>
                <Label className="text-sm md:text-base">全局禁止名单</Label>
                <p className="text-xs text-muted-foreground mt-1">名单中的用户无法进行任何聊天</p>
              </div>
              <Button onClick={() => addToList('ban')} size="sm" variant="outline" className="w-full sm:w-auto">
                <FileText className="mr-1 h-4 w-4" />
                添加用户
              </Button>
            </div>
            {config.chat.ban_user_id.map((userId, index) => (
              <div key={index} className="flex gap-2">
                <Input
                  type="number"
                  value={userId}
                  onChange={(e) => updateListItem('ban', index, parseInt(e.target.value) || 0)}
                  placeholder="输入QQ号"
                  className="text-sm md:text-base"
                />
                <AlertDialog>
                  <AlertDialogTrigger asChild>
                    <Button size="icon" variant="outline">
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </AlertDialogTrigger>
                  <AlertDialogContent>
                    <AlertDialogHeader>
                      <AlertDialogTitle>确认删除</AlertDialogTitle>
                      <AlertDialogDescription>
                        确定要从全局禁止名单中删除用户 {userId} 吗？此操作无法撤销。
                      </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                      <AlertDialogCancel>取消</AlertDialogCancel>
                      <AlertDialogAction onClick={() => removeFromList('ban', index)}>
                        删除
                      </AlertDialogAction>
                    </AlertDialogFooter>
                  </AlertDialogContent>
                </AlertDialog>
              </div>
            ))}
            {config.chat.ban_user_id.length === 0 && (
              <p className="text-sm text-muted-foreground">暂无禁止用户</p>
            )}
          </div>

          {/* 其他设置 */}
          <div className="flex items-center justify-between">
            <div>
              <Label className="text-sm md:text-base">屏蔽QQ官方机器人</Label>
              <p className="text-xs text-muted-foreground mt-1">是否屏蔽来自QQ官方机器人的消息</p>
            </div>
            <Switch
              checked={config.chat.ban_qq_bot}
              onCheckedChange={(checked) =>
                onChange({
                  ...config,
                  chat: { ...config.chat, ban_qq_bot: checked },
                })
              }
            />
          </div>

          <div className="flex items-center justify-between">
            <div>
              <Label className="text-sm md:text-base">启用戳一戳功能</Label>
              <p className="text-xs text-muted-foreground mt-1">是否响应戳一戳消息</p>
            </div>
            <Switch
              checked={config.chat.enable_poke}
              onCheckedChange={(checked) =>
                onChange({
                  ...config,
                  chat: { ...config.chat, enable_poke: checked },
                })
              }
            />
          </div>
        </div>
      </div>
    </div>
  )
}

// 语音和转发消息配置组件
function VoiceSection({
  config,
  onChange,
}: {
  config: AdapterConfig
  onChange: (config: AdapterConfig) => void
}) {
  return (
    <div className="rounded-lg border bg-card p-4 md:p-6 space-y-4 md:space-y-6">
      {/* 语音设置 */}
      <div>
        <h3 className="text-base md:text-lg font-semibold mb-3 md:mb-4">发送语音设置</h3>
        <div className="flex items-center justify-between">
          <div>
            <Label className="text-sm md:text-base">使用 TTS 语音</Label>
            <p className="text-xs text-muted-foreground mt-1">
              请确保已配置 TTS 并有对应的适配器
            </p>
          </div>
          <Switch
            checked={config.voice.use_tts}
            onCheckedChange={(checked) =>
              onChange({
                ...config,
                voice: { ...config.voice, use_tts: checked },
              })
            }
          />
        </div>
      </div>

      {/* 转发消息处理设置 */}
      <div>
        <h3 className="text-base md:text-lg font-semibold mb-3 md:mb-4">转发消息处理设置</h3>
        <div className="grid gap-2">
          <Label htmlFor="image-threshold" className="text-sm md:text-base">图片数量阈值</Label>
          <Input
            id="image-threshold"
            type="number"
            value={config.forward.image_threshold || ''}
            onChange={(e) =>
              onChange({
                ...config,
                forward: { 
                  ...config.forward, 
                  image_threshold: e.target.value ? parseInt(e.target.value) : 0 
                },
              })
            }
            placeholder="30"
            className="text-sm md:text-base"
          />
          <p className="text-xs text-muted-foreground">
            转发消息中图片数量超过此值时使用占位符（避免麦麦VLM处理卡死）
          </p>
        </div>
      </div>
    </div>
  )
}

// 调试配置组件
function DebugSection({
  config,
  onChange,
}: {
  config: AdapterConfig
  onChange: (config: AdapterConfig) => void
}) {
  return (
    <div className="rounded-lg border bg-card p-4 md:p-6 space-y-4 md:space-y-6">
      <div>
        <h3 className="text-base md:text-lg font-semibold mb-3 md:mb-4">调试设置</h3>
        <div className="grid gap-3 md:gap-4">
          <div className="grid gap-2">
            <Label className="text-sm md:text-base">日志等级</Label>
            <Select
              value={config.debug.level}
              onValueChange={(value) =>
                onChange({
                  ...config,
                  debug: { level: value },
                })
              }
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="DEBUG">DEBUG（调试）</SelectItem>
                <SelectItem value="INFO">INFO（信息）</SelectItem>
                <SelectItem value="WARNING">WARNING（警告）</SelectItem>
                <SelectItem value="ERROR">ERROR（错误）</SelectItem>
                <SelectItem value="CRITICAL">CRITICAL（严重）</SelectItem>
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">设置适配器的日志输出等级</p>
          </div>
        </div>
      </div>
    </div>
  )
}
