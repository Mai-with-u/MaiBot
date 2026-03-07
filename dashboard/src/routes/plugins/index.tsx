import { useState, useEffect } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Progress } from '@/components/ui/progress'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { AlertCircle, AlertTriangle, CheckCircle2, Info, Loader2, RotateCw, Search, Settings2 } from 'lucide-react'

import { RestartOverlay } from '@/components/restart-overlay'
import { useToast } from '@/hooks/use-toast'
import { RestartProvider, useRestart } from '@/lib/restart-context'
import {
  checkGitStatus,
  checkPluginInstalled,
  connectPluginProgressWebSocket,
  fetchPluginList,
  getInstalledPluginVersion,
  getInstalledPlugins,
  getMaimaiVersion,
  installPlugin,
  isPluginCompatible,
  uninstallPlugin,
  updatePlugin,
  type InstalledPlugin,
} from '@/lib/plugin-api'
import { getPluginStats, recordPluginDownload, type PluginStatsData } from '@/lib/plugin-stats'

import { InstallDialog } from './InstallDialog'
import { InstalledTab } from './InstalledTab'
import { MarketplaceTab } from './MarketplaceTab'
import type { GitStatus, MaimaiVersion, PluginInfo, PluginLoadProgress } from './types'

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
        const statusResult = await checkGitStatus()
        if (!statusResult.success) {
          toast({
            title: 'Git 状态检查失败',
            description: statusResult.error,
            variant: 'destructive',
          })
          setGitStatus({ installed: false, error: statusResult.error })
        } else {
          setGitStatus(statusResult.data)
          
          if (!statusResult.data.installed) {
            toast({
              title: 'Git 未安装',
              description: statusResult.data.error || '请先安装 Git 才能使用插件安装功能',
              variant: 'destructive',
            })
          }
        }
      }

      // 4. 获取麦麦版本
      if (!isUnmounted) {
        const versionResult = await getMaimaiVersion()
        if (!versionResult.success) {
          toast({
            title: '版本获取失败',
            description: versionResult.error,
            variant: 'destructive',
          })
        } else {
          setMaimaiVersion(versionResult.data)
        }
      }
      // 5. 加载插件列表（包含已安装信息）
      if (!isUnmounted) {
        try {
          setLoading(true)
          setError(null)
          const apiResult = await fetchPluginList()
          if (!apiResult.success) {
            if (!isUnmounted) {
              setError(apiResult.error)
              toast({
                title: '加载失败',
                description: apiResult.error,
                variant: 'destructive',
              })
            }
            return
          }
          const data = apiResult.data
          
          if (!isUnmounted) {
            // 获取已安装插件列表
            const installedResult = await getInstalledPlugins()
            if (!installedResult.success) {
              toast({
                title: '获取已安装插件失败',
                description: installedResult.error,
                variant: 'destructive',
              })
              return
            }
            const installed = installedResult.data
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
    setInstallDialogOpen(true)
  }

  // 安装插件处理
  const handleInstall = async (branch: string) => {
    if (!installingPlugin) return

    if (!branch || branch.trim() === '') {
      toast({
        title: '分支名称不能为空',
        variant: 'destructive',
      })
      return
    }

    try {
      setInstallDialogOpen(false)
      
      const installResult = await installPlugin(
        installingPlugin.id,
        installingPlugin.manifest.repository_url || '',
        branch
      )
      
      if (!installResult.success) {
        toast({
          title: '安装失败',
          description: installResult.error,
          variant: 'destructive',
        })
        return
      }
      
      // 记录下载统计
      recordPluginDownload(installingPlugin.id).catch(err => {
        console.warn('Failed to record download:', err)
      })
      
      toast({
        title: '安装成功',
        description: `${installingPlugin.manifest.name} 已成功安装`,
      })
      
      // 重新加载已安装插件列表
      const installedResult = await getInstalledPlugins()
      if (!installedResult.success) {
        toast({
          title: '获取已安装插件失败',
          description: installedResult.error,
          variant: 'destructive',
        })
        return
      }
      const installed = installedResult.data
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
      const uninstallResult = await uninstallPlugin(plugin.id)
      
      if (!uninstallResult.success) {
        toast({
          title: '卸载失败',
          description: uninstallResult.error,
          variant: 'destructive',
        })
        return
      }
      
      toast({
        title: '卸载成功',
        description: `${plugin.manifest.name} 已成功卸载`,
      })
      
      // 重新加载已安装插件列表
      const installedResult = await getInstalledPlugins()
      if (!installedResult.success) {
        toast({
          title: '获取已安装插件失败',
          description: installedResult.error,
          variant: 'destructive',
        })
        return
      }
      const installed = installedResult.data
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
      const updateResult = await updatePlugin(
        plugin.id,
        plugin.manifest.repository_url || '',
        'main'
      )
      
      if (!updateResult.success) {
        toast({
          title: '更新失败',
          description: updateResult.error,
          variant: 'destructive',
        })
        return
      }
      
      toast({
        title: '更新成功',
        description: `${plugin.manifest.name} 已从 ${updateResult.data.old_version} 更新到 ${updateResult.data.new_version}`,
      })
      
      // 重新加载已安装插件列表
      const installedResult = await getInstalledPlugins()
      if (!installedResult.success) {
        toast({
          title: '获取已安装插件失败',
          description: installedResult.error,
          variant: 'destructive',
        })
        return
      }
      const installed = installedResult.data
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

  // 过滤插件用于标签页统计
  const getFilteredPluginCount = (tab: 'all' | 'installed' | 'updates') => {
    return plugins.filter(p => {
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
      
      let matchesTab = true
      if (tab === 'installed') {
        matchesTab = p.installed === true
      } else if (tab === 'updates') {
        matchesTab = p.installed === true && needsUpdate(p)
      }
      
      return matchesSearch && matchesCategory && matchesCompatibility && matchesTab
    }).length
  }

  // 过滤插件用于可更新标签页
  const filteredUpdatablePlugins = plugins.filter(plugin => {
    if (!plugin.manifest) return false
    
    const matchesSearch = searchQuery === '' ||
      plugin.manifest.name?.toLowerCase().includes(searchQuery.toLowerCase()) ||
      plugin.manifest.description?.toLowerCase().includes(searchQuery.toLowerCase()) ||
      (plugin.manifest.keywords && plugin.manifest.keywords.some(k => k.toLowerCase().includes(searchQuery.toLowerCase())))
    
    const matchesCategory = categoryFilter === 'all' ||
      (plugin.manifest.categories && plugin.manifest.categories.includes(categoryFilter))
    
    const matchesCompatibility = !showCompatibleOnly || 
      !maimaiVersion || 
      checkPluginCompatibility(plugin)
    
    return plugin.installed && needsUpdate(plugin) && matchesSearch && matchesCategory && matchesCompatibility
  })

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
              全部插件 ({getFilteredPluginCount('all')})
            </TabsTrigger>
            <TabsTrigger value="installed">
              已安装 ({getFilteredPluginCount('installed')})
            </TabsTrigger>
            <TabsTrigger value="updates">
              可更新 ({getFilteredPluginCount('updates')})
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
        ) : activeTab === 'all' ? (
          <MarketplaceTab
            plugins={plugins}
            searchQuery={searchQuery}
            categoryFilter={categoryFilter}
            showCompatibleOnly={showCompatibleOnly}
            gitStatus={gitStatus}
            maimaiVersion={maimaiVersion}
            pluginStats={pluginStats}
            loadProgress={loadProgress}
            onInstall={openInstallDialog}
            onUpdate={handleUpdate}
            onUninstall={handleUninstall}
            checkPluginCompatibility={checkPluginCompatibility}
            needsUpdate={needsUpdate}
            getStatusBadge={getStatusBadge}
          />
        ) : activeTab === 'installed' ? (
          <InstalledTab
            plugins={plugins}
            searchQuery={searchQuery}
            categoryFilter={categoryFilter}
            showCompatibleOnly={showCompatibleOnly}
            gitStatus={gitStatus}
            maimaiVersion={maimaiVersion}
            pluginStats={pluginStats}
            loadProgress={loadProgress}
            onInstall={openInstallDialog}
            onUpdate={handleUpdate}
            onUninstall={handleUninstall}
            checkPluginCompatibility={checkPluginCompatibility}
            needsUpdate={needsUpdate}
            getStatusBadge={getStatusBadge}
          />
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {filteredUpdatablePlugins.map((plugin) => (
              <div key={plugin.id}>
                {/* PluginCard would go here */}
              </div>
            ))}
          </div>
        )}

        {/* 安装对话框 */}
        <InstallDialog
          open={installDialogOpen}
          plugin={installingPlugin}
          onOpenChange={setInstallDialogOpen}
          onInstall={handleInstall}
        />

        {/* 重启遮罩层 */}
        <RestartOverlay />
      </div>
    </ScrollArea>
  )
}
