import { useState, useEffect } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Checkbox } from '@/components/ui/checkbox'
import { Search, Download, Star, CheckCircle2, AlertCircle, Loader2, AlertTriangle, RefreshCw, Trash2, Settings2, RotateCw, Info } from 'lucide-react'
import type { PluginInfo } from '@/types/plugin'
import { RestartProvider, useRestart } from '@/lib/restart-context'
import { RestartOverlay } from '@/components/restart-overlay'
import { 
  fetchPluginList, 
  checkGitStatus, 
  connectPluginProgressWebSocket, 
  installPlugin, 
  uninstallPlugin,
  updatePlugin,
  getMaimaiVersion, 
  isPluginCompatible, 
  getInstalledPlugins,
  checkPluginInstalled,
  getInstalledPluginVersion,
  type GitStatus, 
  type PluginLoadProgress, 
  type MaimaiVersion,
  type InstalledPlugin
} from '@/lib/plugin-api'
import { useToast } from '@/hooks/use-toast'
import { Progress } from '@/components/ui/progress'
import { recordPluginDownload, getPluginStats, type PluginStatsData } from '@/lib/plugin-stats'

// 分类名称映射
const CATEGORY_NAMES: Record<string, string> = {
  'Group Management': '群组管理',
  'Entertainment & Interaction': '娱乐互动',
  'Utility Tools': '实用工具',
  'Content Generation': '内容生成',
  'Multimedia': '多媒体',
  'External Integration': '外部集成',
  'Data Analysis & Insights': '数据分析与洞察',
  'Other': '其他',
}

// 主导出组件：包装 RestartProvider
export function PluginsPage() {
  return (
    <RestartProvider>
      <PluginsPageContent />
    </RestartProvider>
  )
}

// 内部组件：实际内容
function PluginsPageContent() {
  const navigate = useNavigate()
  const { triggerRestart, isRestarting } = useRestart()
  const [searchQuery, setSearchQuery] = useState('')
  const [categoryFilter, setCategoryFilter] = useState('all')
  const [activeTab, setActiveTab] = useState('all')  // all | installed | updates
  const [showCompatibleOnly, setShowCompatibleOnly] = useState(true)  // 默认只显示兼容的
  const [plugins, setPlugins] = useState<PluginInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [gitStatus, setGitStatus] = useState<GitStatus | null>(null)
  const [loadProgress, setLoadProgress] = useState<PluginLoadProgress | null>(null)
  const [maimaiVersion, setMaimaiVersion] = useState<MaimaiVersion | null>(null)
  const [, setInstalledPlugins] = useState<InstalledPlugin[]>([])
  const [pluginStats, setPluginStats] = useState<Record<string, PluginStatsData>>({})
  
  // 安装对话框状态
  const [installDialogOpen, setInstallDialogOpen] = useState(false)
  const [installingPlugin, setInstallingPlugin] = useState<PluginInfo | null>(null)
  const [selectedBranch, setSelectedBranch] = useState('main')
  const [customBranch, setCustomBranch] = useState('')
  const [branchInputMode, setBranchInputMode] = useState<'preset' | 'custom'>('preset')
  const [showAdvancedOptions, setShowAdvancedOptions] = useState(false)
  
  const { toast } = useToast()

  // 加载插件统计数据
  const loadPluginStats = async (pluginList: PluginInfo[]) => {
    const statsPromises = pluginList.map(async (plugin) => {
      try {
        const stats = await getPluginStats(plugin.id)
        return { id: plugin.id, stats }
      } catch (error) {
        console.warn(`Failed to load stats for ${plugin.id}:`, error)
        return { id: plugin.id, stats: null }
      }
    })

    const results = await Promise.all(statsPromises)
    const statsMap: Record<string, PluginStatsData> = {}
    
    results.forEach(({ id, stats }) => {
      if (stats) {
        statsMap[id] = stats
      }
    })

    setPluginStats(statsMap)
  }

  // 统一管理 WebSocket 和数据加载
  useEffect(() => {
    let ws: WebSocket | null = null
    let isUnmounted = false

    const init = async () => {
      // 1. 先连接 WebSocket（异步获取 token）
      ws = await connectPluginProgressWebSocket(
        (progress) => {
          if (isUnmounted) return
          
          setLoadProgress(progress)
          
          // 如果加载完成，清除进度
          if (progress.stage === 'success') {
            setTimeout(() => {
              if (!isUnmounted) {
                setLoadProgress(null)
              }
            }, 2000)
          } else if (progress.stage === 'error') {
            setLoading(false)
            setError(progress.error || '加载失败')
          }
        },
        (error) => {
          console.error('WebSocket error:', error)
          if (!isUnmounted) {
            toast({
              title: 'WebSocket 连接失败',
              description: '无法实时显示加载进度',
              variant: 'destructive',
            })
          }
        }
      )

      // 2. 等待 WebSocket 连接建立
      await new Promise<void>((resolve) => {
        if (!ws) {
          resolve()
          return
        }
        
        const checkConnection = () => {
          if (ws && ws.readyState === WebSocket.OPEN) {
            console.log('WebSocket connected, starting to load plugins')
            resolve()
          } else if (ws && ws.readyState === WebSocket.CLOSED) {
            console.warn('WebSocket closed before loading plugins')
            resolve()
          } else {
            setTimeout(checkConnection, 100)
          }
        }
        
        checkConnection()
      })

      // 3. 检查 Git 状态
      if (!isUnmounted) {
        const status = await checkGitStatus()
        setGitStatus(status)
        
        if (!status.installed) {
          toast({
            title: 'Git 未安装',
            description: status.error || '请先安装 Git 才能使用插件安装功能',
            variant: 'destructive',
          })
        }
      }

      // 4. 获取麦麦版本
      if (!isUnmounted) {
        const version = await getMaimaiVersion()
        setMaimaiVersion(version)
      }

      // 5. 加载插件列表（包含已安装信息）
      if (!isUnmounted) {
        try {
          setLoading(true)
          setError(null)
          const data = await fetchPluginList()
          
          if (!isUnmounted) {
            // 获取已安装插件列表
            const installed = await getInstalledPlugins()
            setInstalledPlugins(installed)
            
            // 将已安装信息合并到插件数据中
            const mergedData = data.map(plugin => {
              const isInstalled = checkPluginInstalled(plugin.id, installed)
              const installedVersion = getInstalledPluginVersion(plugin.id, installed)
              
              return {
                ...plugin,
                installed: isInstalled,
                installed_version: installedVersion
              }
            })
          
            
            // 添加本地安装但不在市场的插件
            for (const installedPlugin of installed) {
              const existsInMarket = mergedData.some(p => p.id === installedPlugin.id)
              if (!existsInMarket && installedPlugin.manifest) {
                // 添加本地插件到列表
                mergedData.push({
                  id: installedPlugin.id,
                  manifest: {
                    manifest_version: installedPlugin.manifest.manifest_version || 1,
                    name: installedPlugin.manifest.name,
                    version: installedPlugin.manifest.version,
                    description: installedPlugin.manifest.description || '',
                    author: installedPlugin.manifest.author,
                    license: installedPlugin.manifest.license || 'Unknown',
                    host_application: installedPlugin.manifest.host_application,
                    homepage_url: installedPlugin.manifest.homepage_url,
                    repository_url: installedPlugin.manifest.repository_url,
                    keywords: installedPlugin.manifest.keywords || [],
                    categories: installedPlugin.manifest.categories || [],
                    default_locale: (installedPlugin.manifest.default_locale as string) || 'zh-CN',
                    locales_path: installedPlugin.manifest.locales_path as string | undefined,
                  },
                  downloads: 0,
                  rating: 0,
                  review_count: 0,
                  installed: true,
                  installed_version: installedPlugin.manifest.version,
                  published_at: new Date().toISOString(),
                  updated_at: new Date().toISOString(),
                })
              }
            }
            
            setPlugins(mergedData)
            
            // 6. 加载所有插件的统计数据
            loadPluginStats(mergedData)
          }
        } catch (err) {
          if (!isUnmounted) {
            const errorMessage = err instanceof Error ? err.message : '加载插件列表失败'
            setError(errorMessage)
            toast({
              title: '加载失败',
              description: errorMessage,
              variant: 'destructive',
            })
          }
        } finally {
          if (!isUnmounted) {
            setLoading(false)
          }
        }
      }
    }

    init()

    return () => {
      isUnmounted = true
      if (ws) {
        ws.close()
      }
    }
  }, [toast])

  // 获取插件状态徽章
  const getStatusBadge = (plugin: PluginInfo) => {
    // 优先显示兼容性状态
    if (!plugin.installed && maimaiVersion && !checkPluginCompatibility(plugin)) {
      return (
        <Badge variant="destructive" className="gap-1">
          <AlertCircle className="h-3 w-3" />
          不兼容
        </Badge>
      )
    }
    
    if (plugin.installed) {
      // 版本比较：去除两边空格并进行比较
      const installedVer = plugin.installed_version?.trim()
      const marketVer = plugin.manifest.version?.trim()
      
      if (installedVer !== marketVer) {
        // console.log(`[Plugin ${plugin.id}] 版本不一致:`, {
        //   installed: installedVer,
        //   market: marketVer,
        //   installedType: typeof plugin.installed_version,
        //   marketType: typeof plugin.manifest.version
        // })
        
        // 简单的版本比较：只有当市场版本比已安装版本新时才显示"可更新"
        // 如果本地版本更新（比如手动更新或市场数据过期），则显示"已安装"
        const installedParts = installedVer?.split('.').map(Number) || [0, 0, 0]
        const marketParts = marketVer?.split('.').map(Number) || [0, 0, 0]
        
        // 比较主版本号、次版本号、修订号
        for (let i = 0; i < 3; i++) {
          if ((marketParts[i] || 0) > (installedParts[i] || 0)) {
            // 市场版本更新
            return (
              <Badge variant="outline" className="gap-1 text-orange-600 border-orange-600">
                <AlertCircle className="h-3 w-3" />
                可更新
              </Badge>
            )
          } else if ((marketParts[i] || 0) < (installedParts[i] || 0)) {
            // 本地版本更新
            break
          }
        }
      }
      
      return (
        <Badge variant="default" className="gap-1">
          <CheckCircle2 className="h-3 w-3" />
          已安装
        </Badge>
      )
    }
    return null
  }

  // 检查插件兼容性
  const checkPluginCompatibility = (plugin: PluginInfo): boolean => {
    if (!maimaiVersion || !plugin.manifest?.host_application) return true
    
    return isPluginCompatible(
      plugin.manifest.host_application.min_version,
      plugin.manifest.host_application.max_version,
      maimaiVersion
    )
  }

  // 检查是否需要更新（市场版本比已安装版本新）
  const needsUpdate = (plugin: PluginInfo): boolean => {
    if (!plugin.installed || !plugin.installed_version || !plugin.manifest?.version) {
      return false
    }
    
    const installedVer = plugin.installed_version.trim()
    const marketVer = plugin.manifest.version.trim()
    
    if (installedVer === marketVer) return false
    
    const installedParts = installedVer.split('.').map(Number)
    const marketParts = marketVer.split('.').map(Number)
    
    // 比较主版本号、次版本号、修订号
    for (let i = 0; i < 3; i++) {
      if ((marketParts[i] || 0) > (installedParts[i] || 0)) {
        return true  // 市场版本更新
      } else if ((marketParts[i] || 0) < (installedParts[i] || 0)) {
        return false  // 本地版本更新
      }
    }
    
    return false
  }

  // 过滤插件
  const filteredPlugins = plugins.filter(plugin => {
    // 跳过没有 manifest 的插件
    if (!plugin.manifest) {
      console.warn('[过滤] 跳过无 manifest 的插件:', plugin.id)
      return false
    }
    
    // 搜索过滤
    const matchesSearch = searchQuery === '' ||
      plugin.manifest.name?.toLowerCase().includes(searchQuery.toLowerCase()) ||
      plugin.manifest.description?.toLowerCase().includes(searchQuery.toLowerCase()) ||
      (plugin.manifest.keywords && plugin.manifest.keywords.some(k => k.toLowerCase().includes(searchQuery.toLowerCase())))
    
    // 分类过滤
    const matchesCategory = categoryFilter === 'all' ||
      (plugin.manifest.categories && plugin.manifest.categories.includes(categoryFilter))
    
    // 标签页过滤
    let matchesTab = true
    if (activeTab === 'installed') {
      matchesTab = plugin.installed === true
    } else if (activeTab === 'updates') {
      matchesTab = plugin.installed === true && needsUpdate(plugin)
    }
    
    // 兼容性过滤
    const matchesCompatibility = !showCompatibleOnly || 
      !maimaiVersion || 
      checkPluginCompatibility(plugin)
    
    return matchesSearch && matchesCategory && matchesTab && matchesCompatibility
  })

  // 打开安装对话框
  const openInstallDialog = (plugin: PluginInfo) => {
    if (!gitStatus?.installed) {
      toast({
        title: '无法安装',
        description: 'Git 未安装',
        variant: 'destructive',
      })
      return
    }

    // 检查插件兼容性
    if (maimaiVersion && !checkPluginCompatibility(plugin)) {
      toast({
        title: '无法安装',
        description: '插件与当前麦麦版本不兼容',
        variant: 'destructive',
      })
      return
    }

    setInstallingPlugin(plugin)
    setSelectedBranch('main')
    setCustomBranch('')
    setBranchInputMode('preset')
    setShowAdvancedOptions(false)
    setInstallDialogOpen(true)
  }

  // 安装插件处理
  const handleInstall = async () => {
    if (!installingPlugin) return

    const branch = branchInputMode === 'custom' ? customBranch : selectedBranch
    
    if (!branch || branch.trim() === '') {
      toast({
        title: '分支名称不能为空',
        variant: 'destructive',
      })
      return
    }

    try {
      setInstallDialogOpen(false)
      
      await installPlugin(
        installingPlugin.id,
        installingPlugin.manifest.repository_url || '',
        branch
      )
      
      // 记录下载统计
      recordPluginDownload(installingPlugin.id).catch(err => {
        console.warn('Failed to record download:', err)
      })
      
      toast({
        title: '安装成功',
        description: `${installingPlugin.manifest.name} 已成功安装`,
      })
      
      // 重新加载已安装插件列表
      const installed = await getInstalledPlugins()
      setInstalledPlugins(installed)
      
      // 重新合并已安装信息到插件列表
      setPlugins(prevPlugins => 
        prevPlugins.map(p => {
          if (p.id === installingPlugin.id) {
            const isInstalled = checkPluginInstalled(p.id, installed)
            const installedVersion = getInstalledPluginVersion(p.id, installed)
            
            return {
              ...p,
              installed: isInstalled,
              installed_version: installedVersion
            }
          }
          return p
        })
      )
    } catch (error) {
      toast({
        title: '安装失败',
        description: error instanceof Error ? error.message : '未知错误',
        variant: 'destructive',
      })
    } finally {
      setInstallingPlugin(null)
    }
  }

  // 卸载插件处理
  const handleUninstall = async (plugin: PluginInfo) => {
    try {
      await uninstallPlugin(plugin.id)
      
      toast({
        title: '卸载成功',
        description: `${plugin.manifest.name} 已成功卸载`,
      })
      
      // 重新加载已安装插件列表
      const installed = await getInstalledPlugins()
      setInstalledPlugins(installed)
      
      // 重新合并已安装信息到插件列表
      setPlugins(prevPlugins => 
        prevPlugins.map(p => {
          if (p.id === plugin.id) {
            const isInstalled = checkPluginInstalled(p.id, installed)
            const installedVersion = getInstalledPluginVersion(p.id, installed)
            
            return {
              ...p,
              installed: isInstalled,
              installed_version: installedVersion
            }
          }
          return p
        })
      )
    } catch (error) {
      toast({
        title: '卸载失败',
        description: error instanceof Error ? error.message : '未知错误',
        variant: 'destructive',
      })
    }
  }

  // 更新插件处理
  const handleUpdate = async (plugin: PluginInfo) => {
    if (!gitStatus?.installed) {
      toast({
        title: '无法更新',
        description: 'Git 未安装',
        variant: 'destructive',
      })
      return
    }

    try {
      const result = await updatePlugin(
        plugin.id,
        plugin.manifest.repository_url || '',
        'main'
      )
      
      toast({
        title: '更新成功',
        description: `${plugin.manifest.name} 已从 ${result.old_version} 更新到 ${result.new_version}`,
      })
      
      // 重新加载已安装插件列表
      const installed = await getInstalledPlugins()
      setInstalledPlugins(installed)
      
      // 重新合并已安装信息到插件列表
      setPlugins(prevPlugins => 
        prevPlugins.map(p => {
          if (p.id === plugin.id) {
            const isInstalled = checkPluginInstalled(p.id, installed)
            const installedVersion = getInstalledPluginVersion(p.id, installed)
            
            return {
              ...p,
              installed: isInstalled,
              installed_version: installedVersion
            }
          }
          return p
        })
      )
    } catch (error) {
      toast({
        title: '更新失败',
        description: error instanceof Error ? error.message : '未知错误',
        variant: 'destructive',
      })
    }
  }

  return (
    <ScrollArea className="h-full">
      <div className="space-y-6 p-4 sm:p-6">
        {/* 标题 */}
        <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-4">
          <div>
            <h1 className="text-2xl sm:text-3xl font-bold">插件市场</h1>
            <p className="text-muted-foreground mt-2">浏览和管理麦麦的插件</p>
          </div>
          <div className="flex gap-2">
            <Button 
              variant="outline"
              onClick={() => triggerRestart()}
              disabled={isRestarting}
            >
              <RotateCw className={`h-4 w-4 mr-2 ${isRestarting ? 'animate-spin' : ''}`} />
              重启麦麦
            </Button>
            <Button onClick={() => navigate({ to: '/plugin-mirrors' })}>
              <Settings2 className="h-4 w-4 mr-2" />
              配置镜像源
            </Button>
          </div>
        </div>

        {/* 安装提示 */}
        <Card className="border-blue-200 bg-blue-50 dark:bg-blue-950/20 dark:border-blue-900">
          <CardContent className="py-3">
            <div className="flex items-center gap-2">
              <Info className="h-4 w-4 text-blue-600 flex-shrink-0" />
              <p className="text-sm text-blue-800 dark:text-blue-200">
                安装、卸载或更新插件后，需要<span className="font-semibold">重启麦麦</span>才能使更改生效
              </p>
            </div>
          </CardContent>
        </Card>

        {/* Git 状态警告 */}
        {gitStatus && !gitStatus.installed && (
          <Card className="border-orange-600 bg-orange-50 dark:bg-orange-950/20">
            <CardHeader>
              <div className="flex items-center gap-3">
                <AlertTriangle className="h-5 w-5 text-orange-600" />
                <div>
                  <CardTitle className="text-lg text-orange-900 dark:text-orange-100">
                    Git 未安装
                  </CardTitle>
                  <CardDescription className="text-orange-800 dark:text-orange-200">
                    {gitStatus.error || '请先安装 Git 才能使用插件安装功能'}
                  </CardDescription>
                </div>
              </div>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-orange-800 dark:text-orange-200">
                您可以从 <a href="https://git-scm.com/downloads" target="_blank" rel="noopener noreferrer" className="underline font-medium">git-scm.com</a> 下载并安装 Git。
                安装完成后，请重启麦麦应用。
              </p>
            </CardContent>
          </Card>
        )}

        {/* 搜索和筛选栏 */}
        <Card className="p-4">
          <div className="flex flex-col gap-4">
            <div className="flex flex-col sm:flex-row gap-4">
              {/* 搜索框 */}
              <div className="flex-1 relative">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                <Input
                  placeholder="搜索插件..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="pl-9"
                />
              </div>

              {/* 分类筛选 */}
              <Select value={categoryFilter} onValueChange={setCategoryFilter}>
                <SelectTrigger className="w-full sm:w-[200px]">
                  <SelectValue placeholder="选择分类" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">全部分类</SelectItem>
                  <SelectItem value="Group Management">群组管理</SelectItem>
                  <SelectItem value="Entertainment & Interaction">娱乐互动</SelectItem>
                  <SelectItem value="Utility Tools">实用工具</SelectItem>
                  <SelectItem value="Content Generation">内容生成</SelectItem>
                  <SelectItem value="Multimedia">多媒体</SelectItem>
                  <SelectItem value="External Integration">外部集成</SelectItem>
                  <SelectItem value="Data Analysis & Insights">数据分析与洞察</SelectItem>
                  <SelectItem value="Other">其他</SelectItem>
                </SelectContent>
              </Select>
            </div>
            
            {/* 兼容性筛选 */}
            <div className="flex items-center space-x-2">
              <Checkbox 
                id="compatible-only" 
                checked={showCompatibleOnly}
                onCheckedChange={(checked) => setShowCompatibleOnly(checked === true)}
              />
              <label
                htmlFor="compatible-only"
                className="text-sm font-medium leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70 cursor-pointer"
              >
                只显示兼容当前版本的插件
              </label>
            </div>
          </div>
        </Card>

        {/* 标签页 */}
        <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full">
          <TabsList className="grid w-full grid-cols-3">
            <TabsTrigger value="all">
              全部插件 ({
                plugins.filter(p => {
                  if (!p.manifest) return false
                  const matchesSearch = searchQuery === '' ||
                    p.manifest.name?.toLowerCase().includes(searchQuery.toLowerCase()) ||
                    p.manifest.description?.toLowerCase().includes(searchQuery.toLowerCase()) ||
                    (p.manifest.keywords && p.manifest.keywords.some(k => k.toLowerCase().includes(searchQuery.toLowerCase())))
                  const matchesCategory = categoryFilter === 'all' ||
                    (p.manifest.categories && p.manifest.categories.includes(categoryFilter))
                  const matchesCompatibility = !showCompatibleOnly || 
                    !maimaiVersion || 
                    checkPluginCompatibility(p)
                  return matchesSearch && matchesCategory && matchesCompatibility
                }).length
              })
            </TabsTrigger>
            <TabsTrigger value="installed">
              已安装 ({
                plugins.filter(p => {
                  if (!p.manifest) return false
                  const matchesSearch = searchQuery === '' ||
                    p.manifest.name?.toLowerCase().includes(searchQuery.toLowerCase()) ||
                    p.manifest.description?.toLowerCase().includes(searchQuery.toLowerCase()) ||
                    (p.manifest.keywords && p.manifest.keywords.some(k => k.toLowerCase().includes(searchQuery.toLowerCase())))
                  const matchesCategory = categoryFilter === 'all' ||
                    (p.manifest.categories && p.manifest.categories.includes(categoryFilter))
                  const matchesCompatibility = !showCompatibleOnly || 
                    !maimaiVersion || 
                    checkPluginCompatibility(p)
                  return p.installed && matchesSearch && matchesCategory && matchesCompatibility
                }).length
              })
            </TabsTrigger>
            <TabsTrigger value="updates">
              可更新 ({
                plugins.filter(p => {
                  if (!p.manifest) return false
                  const matchesSearch = searchQuery === '' ||
                    p.manifest.name?.toLowerCase().includes(searchQuery.toLowerCase()) ||
                    p.manifest.description?.toLowerCase().includes(searchQuery.toLowerCase()) ||
                    (p.manifest.keywords && p.manifest.keywords.some(k => k.toLowerCase().includes(searchQuery.toLowerCase())))
                  const matchesCategory = categoryFilter === 'all' ||
                    (p.manifest.categories && p.manifest.categories.includes(categoryFilter))
                  const matchesCompatibility = !showCompatibleOnly || 
                    !maimaiVersion || 
                    checkPluginCompatibility(p)
                  return p.installed && needsUpdate(p) && matchesSearch && matchesCategory && matchesCompatibility
                }).length
              })
            </TabsTrigger>
          </TabsList>
        </Tabs>

        {/* 进度条 - 仅显示插件清单加载进度 */}
        {loadProgress && loadProgress.stage === 'loading' && loadProgress.operation === 'fetch' && (
          <Card className="p-4">
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  <span className="text-sm font-medium">加载插件列表</span>
                </div>
                <span className="text-sm font-medium">{loadProgress.progress}%</span>
              </div>
              <Progress value={loadProgress.progress} className="h-2" />
              <div className="text-xs text-muted-foreground">
                {loadProgress.message}
              </div>
              {loadProgress.total_plugins > 0 && (
                <div className="text-xs text-muted-foreground text-center">
                  已加载 {loadProgress.loaded_plugins} / {loadProgress.total_plugins} 个插件
                </div>
              )}
            </div>
          </Card>
        )}

        {/* 加载错误显示 */}
        {loadProgress && loadProgress.stage === 'error' && loadProgress.error && (
          <Card className="border-destructive bg-destructive/10">
            <CardHeader>
              <div className="flex items-center gap-3">
                <AlertTriangle className="h-5 w-5 text-destructive" />
                <div>
                  <CardTitle className="text-lg text-destructive">
                    加载失败
                  </CardTitle>
                  <CardDescription className="text-destructive/80">
                    {loadProgress.error}
                  </CardDescription>
                </div>
              </div>
            </CardHeader>
          </Card>
        )}

        {/* 插件卡片网格 */}
        {loading ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
            <span className="ml-3 text-muted-foreground">加载插件列表中...</span>
          </div>
        ) : error ? (
          <Card className="p-6">
            <div className="flex flex-col items-center justify-center py-8 text-center">
              <AlertTriangle className="h-12 w-12 text-destructive mb-4" />
              <h3 className="text-lg font-semibold mb-2">加载失败</h3>
              <p className="text-sm text-muted-foreground mb-4">{error}</p>
              <Button onClick={() => window.location.reload()}>
                重新加载
              </Button>
            </div>
          </Card>
        ) : filteredPlugins.length === 0 ? (
          <Card className="p-6">
            <div className="flex flex-col items-center justify-center py-8 text-center">
              <Search className="h-12 w-12 text-muted-foreground mb-4" />
              <h3 className="text-lg font-semibold mb-2">未找到插件</h3>
              <p className="text-sm text-muted-foreground">
                {searchQuery || categoryFilter !== 'all' 
                  ? '尝试调整搜索条件或筛选器'
                  : '暂无可用插件'}
              </p>
            </div>
          </Card>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {filteredPlugins.map((plugin) => (
            <Card
              key={plugin.id}
              className="flex flex-col hover:shadow-lg transition-shadow h-full"
            >
              <CardHeader>
                <div className="flex items-start justify-between gap-2">
                  <CardTitle className="text-xl">{plugin.manifest?.name || plugin.id}</CardTitle>
                  <div className="flex flex-col gap-1">
                    {plugin.manifest?.categories && plugin.manifest.categories[0] && (
                      <Badge variant="secondary" className="text-xs whitespace-nowrap">
                        {CATEGORY_NAMES[plugin.manifest.categories[0]] || plugin.manifest.categories[0]}
                      </Badge>
                    )}
                    {getStatusBadge(plugin)}
                  </div>
                </div>
                <CardDescription className="line-clamp-2">{plugin.manifest?.description || '无描述'}</CardDescription>
              </CardHeader>
              <CardContent className="flex-1">
                <div className="space-y-3">
                  {/* 统计信息 */}
                  <div className="flex items-center gap-4 text-sm text-muted-foreground">
                    <div className="flex items-center gap-1">
                      <Download className="h-4 w-4" />
                      <span>{(pluginStats[plugin.id]?.downloads ?? plugin.downloads ?? 0).toLocaleString()}</span>
                    </div>
                    <div className="flex items-center gap-1">
                      <Star className="h-4 w-4 fill-yellow-400 text-yellow-400" />
                      <span>{(pluginStats[plugin.id]?.rating ?? plugin.rating ?? 0).toFixed(1)}</span>
                    </div>
                  </div>
                  {/* 标签 */}
                  <div className="flex flex-wrap gap-2">
                    {plugin.manifest?.keywords && plugin.manifest.keywords.slice(0, 3).map((keyword) => (
                      <Badge key={keyword} variant="outline" className="text-xs">
                        {keyword}
                      </Badge>
                    ))}
                    {plugin.manifest?.keywords && plugin.manifest.keywords.length > 3 && (
                      <Badge variant="outline" className="text-xs">
                        +{plugin.manifest.keywords.length - 3}
                      </Badge>
                    )}
                  </div>
                  {/* 版本和作者 */}
                  <div className="text-xs text-muted-foreground pt-2 border-t space-y-1">
                    <div>v{plugin.manifest?.version || 'unknown'} · {plugin.manifest?.author?.name || 'Unknown'}</div>
                    {/* 支持版本 */}
                    {plugin.manifest?.host_application && (
                      <div className="flex items-center gap-1">
                        <span>支持:</span>
                        <span className="font-medium">
                          {plugin.manifest.host_application.min_version}
                          {plugin.manifest.host_application.max_version 
                            ? ` - ${plugin.manifest.host_application.max_version}`
                            : ' - 最新版本'
                          }
                        </span>
                      </div>
                    )}
                  </div>
                </div>
              </CardContent>
              <CardFooter className="pt-4">
                <div className="flex items-center justify-end gap-2 w-full">
                  <Button 
                    variant="outline"
                    size="sm"
                    onClick={() => navigate({ to: '/plugin-detail', search: { pluginId: plugin.id } })}
                  >
                    查看详情
                  </Button>
                  {plugin.installed ? (
                    needsUpdate(plugin) ? (
                      <Button 
                        size="sm"
                        disabled={!gitStatus?.installed}
                        title={!gitStatus?.installed ? 'Git 未安装' : undefined}
                        onClick={() => handleUpdate(plugin)}
                      >
                        <RefreshCw className="h-4 w-4 mr-1" />
                        更新
                      </Button>
                    ) : (
                      <Button 
                        variant="destructive" 
                        size="sm"
                        disabled={!gitStatus?.installed}
                        title={!gitStatus?.installed ? 'Git 未安装' : undefined}
                        onClick={() => handleUninstall(plugin)}
                      >
                        <Trash2 className="h-4 w-4 mr-1" />
                        卸载
                      </Button>
                    )
                  ) : (
                    <Button 
                      size="sm"
                      disabled={
                        !gitStatus?.installed || 
                        loadProgress?.operation === 'install' ||
                        (maimaiVersion !== null && !checkPluginCompatibility(plugin))
                      }
                      title={
                        !gitStatus?.installed 
                          ? 'Git 未安装' 
                          : (maimaiVersion !== null && !checkPluginCompatibility(plugin))
                            ? `不兼容当前版本 (需要 ${plugin.manifest?.host_application?.min_version || '未知'}${plugin.manifest?.host_application?.max_version ? ` - ${plugin.manifest.host_application.max_version}` : '+'}，当前 ${maimaiVersion?.version})`
                            : undefined
                      }
                      onClick={() => openInstallDialog(plugin)}
                    >
                      <Download className="h-4 w-4 mr-1" />
                      {loadProgress?.operation === 'install' && loadProgress?.plugin_id === plugin.id ? '安装中...' : '安装'}
                    </Button>
                  )}
                </div>
              </CardFooter>
              {/* 安装/卸载/更新进度显示 - 在卡片下方 */}
              {loadProgress && 
                (loadProgress.stage === 'loading' || loadProgress.stage === 'success' || loadProgress.stage === 'error') && 
                loadProgress.operation !== 'fetch' && 
                loadProgress.plugin_id === plugin.id && (
                <div className="px-6 pb-4 -mt-2">
                  <div className={`space-y-2 p-3 rounded-lg border ${
                    loadProgress.stage === 'success' 
                      ? 'bg-green-50 dark:bg-green-950/20 border-green-200 dark:border-green-900' 
                      : loadProgress.stage === 'error'
                        ? 'bg-red-50 dark:bg-red-950/20 border-red-200 dark:border-red-900'
                        : 'bg-muted/50'
                  }`}>
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        {loadProgress.stage === 'loading' ? (
                          <Loader2 className="h-3 w-3 animate-spin" />
                        ) : loadProgress.stage === 'success' ? (
                          <CheckCircle2 className="h-3 w-3 text-green-600" />
                        ) : (
                          <AlertCircle className="h-3 w-3 text-red-600" />
                        )}
                        <span className={`text-xs font-medium ${
                          loadProgress.stage === 'success' 
                            ? 'text-green-700 dark:text-green-300' 
                            : loadProgress.stage === 'error'
                              ? 'text-red-700 dark:text-red-300'
                              : ''
                        }`}>
                          {loadProgress.stage === 'loading' ? (
                            <>
                              {loadProgress.operation === 'install' && '正在安装'}
                              {loadProgress.operation === 'uninstall' && '正在卸载'}
                              {loadProgress.operation === 'update' && '正在更新'}
                            </>
                          ) : loadProgress.stage === 'success' ? (
                            <>
                              {loadProgress.operation === 'install' && '安装完成'}
                              {loadProgress.operation === 'uninstall' && '卸载完成'}
                              {loadProgress.operation === 'update' && '更新完成'}
                            </>
                          ) : (
                            <>
                              {loadProgress.operation === 'install' && '安装失败'}
                              {loadProgress.operation === 'uninstall' && '卸载失败'}
                              {loadProgress.operation === 'update' && '更新失败'}
                            </>
                          )}
                        </span>
                      </div>
                      {loadProgress.stage !== 'error' && (
                        <span className={`text-xs font-medium ${
                          loadProgress.stage === 'success' ? 'text-green-700 dark:text-green-300' : ''
                        }`}>{loadProgress.progress}%</span>
                      )}
                    </div>
                    {loadProgress.stage !== 'error' && (
                      <Progress 
                        value={loadProgress.progress} 
                        className={`h-1.5 ${loadProgress.stage === 'success' ? '[&>div]:bg-green-500' : ''}`} 
                      />
                    )}
                    <div className={`text-xs ${
                      loadProgress.stage === 'success' 
                        ? 'text-green-600 dark:text-green-400 truncate' 
                        : loadProgress.stage === 'error'
                          ? 'text-red-600 dark:text-red-400'
                          : 'text-muted-foreground truncate'
                    }`}>
                      {loadProgress.stage === 'error' ? (loadProgress.error || loadProgress.message || '操作失败') : loadProgress.message}
                    </div>
                  </div>
                </div>
              )}
            </Card>
          ))}
          </div>
        )}

        {/* 安装对话框 */}
        <Dialog open={installDialogOpen} onOpenChange={setInstallDialogOpen}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>安装插件</DialogTitle>
              <DialogDescription>
                安装 {installingPlugin?.manifest.name}
              </DialogDescription>
            </DialogHeader>

            <div className="space-y-4">
              {/* 基本信息 */}
              <div>
                <p className="text-sm text-muted-foreground">
                  版本: {installingPlugin?.manifest.version}
                </p>
                <p className="text-sm text-muted-foreground">
                  作者: {typeof installingPlugin?.manifest.author === 'string' 
                    ? installingPlugin.manifest.author 
                    : installingPlugin?.manifest.author?.name}
                </p>
              </div>

              {/* 高级选项开关 */}
              <div className="flex items-center space-x-2">
                <Checkbox
                  id="advanced-options"
                  checked={showAdvancedOptions}
                  onCheckedChange={(checked) => setShowAdvancedOptions(checked as boolean)}
                />
                <label
                  htmlFor="advanced-options"
                  className="text-sm font-medium leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70"
                >
                  高级选项
                </label>
              </div>

              {/* 高级选项内容 */}
              {showAdvancedOptions && (
                <div className="space-y-4 p-4 border rounded-lg">
                  <div className="space-y-2">
                    <label className="text-sm font-medium">分支选择</label>
                    
                    <Tabs value={branchInputMode} onValueChange={(value) => setBranchInputMode(value as 'preset' | 'custom')}>
                      <TabsList className="grid w-full grid-cols-2">
                        <TabsTrigger value="preset" className="text-xs">预设分支</TabsTrigger>
                        <TabsTrigger value="custom" className="text-xs">自定义分支</TabsTrigger>
                      </TabsList>
                      
                      {/* 预设分支选择 */}
                      {branchInputMode === 'preset' && (
                        <div className="mt-3">
                          <Select value={selectedBranch} onValueChange={setSelectedBranch}>
                            <SelectTrigger>
                              <SelectValue placeholder="选择分支" />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="main">main (默认)</SelectItem>
                              <SelectItem value="master">master</SelectItem>
                              <SelectItem value="dev">dev (开发版)</SelectItem>
                              <SelectItem value="develop">develop</SelectItem>
                              <SelectItem value="beta">beta (测试版)</SelectItem>
                              <SelectItem value="stable">stable (稳定版)</SelectItem>
                            </SelectContent>
                          </Select>
                        </div>
                      )}

                      {/* 自定义分支输入 */}
                      {branchInputMode === 'custom' && (
                        <div className="space-y-2 mt-3">
                          <input
                            type="text"
                            className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                            placeholder="输入分支名称，例如: feature/new-feature"
                            value={customBranch}
                            onChange={(e) => setCustomBranch(e.target.value)}
                          />
                          <p className="text-xs text-muted-foreground">
                            输入 Git 分支名称、标签或提交哈希
                          </p>
                        </div>
                      )}
                    </Tabs>
                  </div>
                </div>
              )}

              {!showAdvancedOptions && (
                <p className="text-sm text-muted-foreground">
                  将从默认分支 (main) 安装插件
                </p>
              )}
            </div>

            <DialogFooter>
              <Button
                variant="outline"
                onClick={() => setInstallDialogOpen(false)}
              >
                取消
              </Button>
              <Button onClick={handleInstall}>
                <Download className="h-4 w-4 mr-2" />
                安装
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {/* 重启遮罩层 */}
        <RestartOverlay />
      </div>
    </ScrollArea>
  )
}

