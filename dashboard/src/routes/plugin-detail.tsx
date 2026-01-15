import { useState, useEffect } from 'react'
import { useNavigate, useSearch } from '@tanstack/react-router'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  ArrowLeft,
  Download,
  ExternalLink,
  CheckCircle2,
  AlertCircle,
  Loader2,
  Trash2,
  RefreshCw,
  User,
  Package,
  Shield,
  Globe,
  Tag,
  GitBranch,
  Info,
} from 'lucide-react'
import { useToast } from '@/hooks/use-toast'
import { fetchWithAuth } from '@/lib/fetch-with-auth'
import type { PluginInfo } from '@/types/plugin'
import {
  checkGitStatus,
  getMaimaiVersion,
  isPluginCompatible,
  installPlugin,
  uninstallPlugin,
  updatePlugin,
  checkPluginInstalled,
  getInstalledPluginVersion,
  getInstalledPlugins,
  type GitStatus,
  type MaimaiVersion,
} from '@/lib/plugin-api'
import { PluginStats } from '@/components/plugin-stats'
import { MarkdownRenderer } from '@/components'
import { recordPluginDownload } from '@/lib/plugin-stats'

// 分类名称映射
const CATEGORY_NAMES: Record<string, string> = {
  'Group Management': '群组管理',
  'Entertainment & Interaction': '娱乐互动',
  'Utility Tools': '实用工具',
  'Content Generation': '内容生成',
  Multimedia: '多媒体',
  'External Integration': '外部集成',
  'Data Analysis & Insights': '数据分析与洞察',
  Other: '其他',
}

export function PluginDetailPage() {
  const navigate = useNavigate()
  const search = useSearch({ strict: false }) as { pluginId?: string }
  const { toast } = useToast()

  const [plugin, setPlugin] = useState<PluginInfo | null>(null)
  const [readme, setReadme] = useState<string>('')
  const [loading, setLoading] = useState(true)
  const [readmeLoading, setReadmeLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [gitStatus, setGitStatus] = useState<GitStatus | null>(null)
  const [maimaiVersion, setMaimaiVersion] = useState<MaimaiVersion | null>(null)
  const [isInstalled, setIsInstalled] = useState(false)
  const [installedVersion, setInstalledVersion] = useState<string | undefined>()
  const [operating, setOperating] = useState(false)

  // 加载插件信息
  useEffect(() => {
    const loadPluginInfo = async () => {
      if (!search.pluginId) {
        setError('缺少插件 ID')
        setLoading(false)
        return
      }

      try {
        setLoading(true)
        setError(null)

        // 从插件列表 API 获取数据
        const response = await fetchWithAuth('/api/webui/plugins/fetch-raw', {
          method: 'POST',
          body: JSON.stringify({
            owner: 'Mai-with-u',
            repo: 'plugin-repo',
            branch: 'main',
            file_path: 'plugin_details.json',
          }),
        })

        if (!response.ok) {
          throw new Error('获取插件列表失败')
        }

        const result = await response.json()

        if (!result.success || !result.data) {
          throw new Error(result.error || '获取插件列表失败')
        }

        const pluginList = JSON.parse(result.data)
        const foundPlugin = pluginList.find((p: any) => p.id === search.pluginId)

        if (!foundPlugin) {
          throw new Error('未找到该插件')
        }

        // 转换为 PluginInfo 格式
        const pluginInfo: PluginInfo = {
          id: foundPlugin.id,
          manifest: foundPlugin.manifest,
          downloads: 0,
          rating: 0,
          review_count: 0,
          installed: false,
          published_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        }

        setPlugin(pluginInfo)

        // 加载额外信息
        const [gitStatusResult, versionResult, installedPlugins] = await Promise.all([
          checkGitStatus(),
          getMaimaiVersion(),
          getInstalledPlugins(),
        ])

        setGitStatus(gitStatusResult)
        setMaimaiVersion(versionResult)
        setIsInstalled(checkPluginInstalled(search.pluginId, installedPlugins))
        setInstalledVersion(getInstalledPluginVersion(search.pluginId, installedPlugins))
      } catch (err) {
        setError(err instanceof Error ? err.message : '加载失败')
      } finally {
        setLoading(false)
      }
    }

    loadPluginInfo()
  }, [search.pluginId])

  // 加载 README
  useEffect(() => {
    const loadReadme = async () => {
      if (!plugin?.manifest?.repository_url) {
        setReadmeLoading(false)
        return
      }

      try {
        setReadmeLoading(true)

        // 如果插件已安装，优先尝试从本地读取 README
        if (isInstalled && search.pluginId) {
          try {
            const localResponse = await fetchWithAuth(`/api/webui/plugins/local-readme/${search.pluginId}`)
            
            if (localResponse.ok) {
              const localResult = await localResponse.json()
              
              if (localResult.success && localResult.data) {
                setReadme(localResult.data)
                setReadmeLoading(false)
                return // 成功获取本地 README，直接返回
              }
            }
          } catch (err) {
            console.log('本地 README 获取失败，尝试远程获取:', err)
            // 继续执行远程获取逻辑
          }
        }

        // 从 repository_url 解析仓库信息
        // 格式: https://github.com/owner/repo
        const match = plugin.manifest.repository_url.match(/github\.com\/([^/]+)\/([^/\s]+)/)
        if (!match) {
          setReadme('无法解析仓库地址')
          return
        }

        const [, owner, repo] = match
        const cleanRepo = repo.replace(/\.git$/, '')

        // 使用后端代理获取 README.md
        const response = await fetchWithAuth('/api/webui/plugins/fetch-raw', {
          method: 'POST',
          body: JSON.stringify({
            owner,
            repo: cleanRepo,
            branch: 'main',
            file_path: 'README.md',
          }),
        })

        if (!response.ok) {
          throw new Error('获取 README 失败')
        }

        const result = await response.json()

        if (result.success && result.data) {
          setReadme(result.data)
        } else {
          setReadme('该插件暂无 README 文档')
        }
      } catch (err) {
        console.error('加载 README 失败:', err)
        setReadme('加载 README 失败')
      } finally {
        setReadmeLoading(false)
      }
    }

    loadReadme()
  }, [plugin, isInstalled, search.pluginId])

  // 检查是否需要更新
  const needsUpdate = () => {
    if (!plugin || !isInstalled || !installedVersion) return false
    return installedVersion !== plugin.manifest.version
  }

  // 检查兼容性
  const checkCompatibility = () => {
    if (!plugin || !maimaiVersion) return true
    return isPluginCompatible(
      plugin.manifest.host_application.min_version,
      plugin.manifest.host_application.max_version,
      maimaiVersion
    )
  }

  // 安装插件
  const handleInstall = async () => {
    if (!plugin || !gitStatus?.installed) return

    try {
      setOperating(true)

      await installPlugin(plugin.id, plugin.manifest.repository_url || '', 'main')

      // 记录下载统计
      recordPluginDownload(plugin.id).catch((err) => {
        console.warn('Failed to record download:', err)
      })

      toast({
        title: '安装成功',
        description: `${plugin.manifest.name} 已成功安装`,
      })

      // 重新加载安装状态
      const installedPlugins = await getInstalledPlugins()
      setIsInstalled(checkPluginInstalled(plugin.id, installedPlugins))
      setInstalledVersion(getInstalledPluginVersion(plugin.id, installedPlugins))
    } catch (error) {
      toast({
        title: '安装失败',
        description: error instanceof Error ? error.message : '未知错误',
        variant: 'destructive',
      })
    } finally {
      setOperating(false)
    }
  }

  // 卸载插件
  const handleUninstall = async () => {
    if (!plugin) return

    try {
      setOperating(true)

      await uninstallPlugin(plugin.id)

      toast({
        title: '卸载成功',
        description: `${plugin.manifest.name} 已成功卸载`,
      })

      // 重新加载安装状态
      const installedPlugins = await getInstalledPlugins()
      setIsInstalled(checkPluginInstalled(plugin.id, installedPlugins))
      setInstalledVersion(getInstalledPluginVersion(plugin.id, installedPlugins))
    } catch (error) {
      toast({
        title: '卸载失败',
        description: error instanceof Error ? error.message : '未知错误',
        variant: 'destructive',
      })
    } finally {
      setOperating(false)
    }
  }

  // 更新插件
  const handleUpdate = async () => {
    if (!plugin || !gitStatus?.installed) return

    try {
      setOperating(true)

      const result = await updatePlugin(plugin.id, plugin.manifest.repository_url || '', 'main')

      toast({
        title: '更新成功',
        description: `${plugin.manifest.name} 已从 ${result.old_version} 更新到 ${result.new_version}`,
      })

      // 重新加载安装状态
      const installedPlugins = await getInstalledPlugins()
      setIsInstalled(checkPluginInstalled(plugin.id, installedPlugins))
      setInstalledVersion(getInstalledPluginVersion(plugin.id, installedPlugins))
    } catch (error) {
      toast({
        title: '更新失败',
        description: error instanceof Error ? error.message : '未知错误',
        variant: 'destructive',
      })
    } finally {
      setOperating(false)
    }
  }



  if (loading) {
    return (
      <div className="space-y-4 sm:space-y-6 p-4 sm:p-6">
        <div className="flex items-center gap-3">
          <Button 
            variant="ghost" 
            size="icon"
            onClick={() => navigate({ to: '/plugins' })}
          >
            <ArrowLeft className="h-5 w-5" />
          </Button>
          <div>
            <h1 className="text-2xl sm:text-3xl font-bold">插件详情</h1>
          </div>
        </div>
        <div className="flex items-center justify-center py-12">
          <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
          <span className="ml-3 text-muted-foreground">加载插件信息中...</span>
        </div>
      </div>
    )
  }

  if (error || !plugin) {
    return (
      <div className="space-y-4 sm:space-y-6 p-4 sm:p-6">
        <div className="flex items-center gap-3">
          <Button 
            variant="ghost" 
            size="icon"
            onClick={() => navigate({ to: '/plugins' })}
          >
            <ArrowLeft className="h-5 w-5" />
          </Button>
          <div>
            <h1 className="text-2xl sm:text-3xl font-bold">插件详情</h1>
          </div>
        </div>
        <Card className="p-6">
          <div className="flex flex-col items-center justify-center py-8 text-center">
            <AlertCircle className="h-12 w-12 text-destructive mb-4" />
            <h3 className="text-lg font-semibold mb-2">加载失败</h3>
            <p className="text-sm text-muted-foreground mb-4">{error}</p>
            <Button onClick={() => navigate({ to: '/plugins' })}>返回插件列表</Button>
          </div>
        </Card>
      </div>
    )
  }

  const isCompatible = checkCompatibility()

  return (
    <div className="space-y-4 sm:space-y-6 p-4 sm:p-6">
      {/* 页面标题和返回按钮 */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <Button 
            variant="ghost" 
            size="icon"
            onClick={() => navigate({ to: '/plugins' })}
            className="shrink-0"
          >
            <ArrowLeft className="h-5 w-5" />
          </Button>
          <div>
            <h1 className="text-2xl sm:text-3xl font-bold">插件详情</h1>
            <p className="text-muted-foreground mt-1 sm:mt-2 text-sm sm:text-base">
              {plugin.manifest.name}
            </p>
          </div>
        </div>

        {/* 操作按钮 */}
        <div className="flex flex-wrap gap-2">
          {isInstalled ? (
            <>
              {needsUpdate() ? (
                <Button
                  disabled={!gitStatus?.installed || operating}
                  onClick={handleUpdate}
                  title={!gitStatus?.installed ? 'Git 未安装' : undefined}
                >
                  {operating ? (
                    <>
                      <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                      更新中...
                    </>
                  ) : (
                    <>
                      <RefreshCw className="h-4 w-4 mr-2" />
                      更新
                    </>
                  )}
                </Button>
              ) : null}
              <Button
                variant="destructive"
                disabled={!gitStatus?.installed || operating}
                onClick={handleUninstall}
                title={!gitStatus?.installed ? 'Git 未安装' : undefined}
              >
                {operating ? (
                  <>
                    <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                    卸载中...
                  </>
                ) : (
                  <>
                    <Trash2 className="h-4 w-4 mr-2" />
                    卸载
                  </>
                )}
              </Button>
            </>
          ) : (
            <Button
              disabled={!gitStatus?.installed || !isCompatible || operating}
              onClick={handleInstall}
              title={
                !gitStatus?.installed
                  ? 'Git 未安装'
                  : !isCompatible
                    ? `不兼容当前版本 (需要 ${plugin.manifest.host_application.min_version}${plugin.manifest.host_application.max_version ? ` - ${plugin.manifest.host_application.max_version}` : '+'}，当前 ${maimaiVersion?.version})`
                    : undefined
              }
            >
              {operating ? (
                <>
                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                  安装中...
                </>
              ) : (
                <>
                  <Download className="h-4 w-4 mr-2" />
                  安装
                </>
              )}
            </Button>
          )}
        </div>
      </div>

      <ScrollArea className="h-[calc(100vh-200px)] sm:h-[calc(100vh-220px)]">
        <div className="space-y-6 pr-4">
          {/* 插件头部信息卡片 */}
          <Card>
            <CardHeader>
              <div className="flex items-start justify-between gap-4">
                <div className="flex-1 space-y-2">
                  <div className="flex items-center gap-3 flex-wrap">
                    <CardTitle className="text-2xl">{plugin.manifest.name}</CardTitle>
                    <Badge variant="secondary" className="text-sm">
                      v{plugin.manifest.version}
                    </Badge>
                    {isInstalled && (
                      <Badge variant="default" className="text-sm">
                        <CheckCircle2 className="h-3 w-3 mr-1" />
                        已安装 {installedVersion && `(v${installedVersion})`}
                      </Badge>
                    )}
                    {needsUpdate() && (
                      <Badge variant="outline" className="text-sm border-orange-500 text-orange-500">
                        <RefreshCw className="h-3 w-3 mr-1" />
                        可更新
                      </Badge>
                    )}
                    {!isCompatible && (
                      <Badge variant="destructive" className="text-sm">
                        <AlertCircle className="h-3 w-3 mr-1" />
                        不兼容
                      </Badge>
                    )}
                  </div>
                  <CardDescription className="text-base">{plugin.manifest.description}</CardDescription>
                </div>
              </div>
            </CardHeader>
          </Card>

          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            {/* 左侧 - 详细信息 */}
            <div className="lg:col-span-1 space-y-6">
              {/* 统计信息 */}
              <Card>
                <CardHeader>
                  <CardTitle className="text-lg">统计信息</CardTitle>
                </CardHeader>
                <CardContent>
                  <PluginStats pluginId={plugin.id} />
                </CardContent>
              </Card>

              {/* 基本信息 */}
              <Card>
                <CardHeader>
                  <CardTitle className="text-lg">基本信息</CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="space-y-3">
                    <div className="flex items-center gap-2 text-sm">
                      <User className="h-4 w-4 text-muted-foreground" />
                      <span className="text-muted-foreground">作者:</span>
                      <span className="font-medium">{plugin.manifest.author?.name || 'Unknown'}</span>
                      {plugin.manifest.author?.url && (
                        <a
                          href={plugin.manifest.author.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-primary hover:underline"
                        >
                          <ExternalLink className="h-3 w-3" />
                        </a>
                      )}
                    </div>

                    <div className="flex items-center gap-2 text-sm">
                      <Package className="h-4 w-4 text-muted-foreground" />
                      <span className="text-muted-foreground">版本:</span>
                      <span className="font-medium">v{plugin.manifest.version}</span>
                    </div>

                    <div className="flex items-center gap-2 text-sm">
                      <Shield className="h-4 w-4 text-muted-foreground" />
                      <span className="text-muted-foreground">许可证:</span>
                      <span className="font-medium">{plugin.manifest.license}</span>
                    </div>

                    {plugin.manifest.homepage_url && (
                      <div className="flex items-center gap-2 text-sm">
                        <Globe className="h-4 w-4 text-muted-foreground" />
                        <span className="text-muted-foreground">主页:</span>
                        <a
                          href={plugin.manifest.homepage_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-primary hover:underline flex items-center gap-1"
                        >
                          访问
                          <ExternalLink className="h-3 w-3" />
                        </a>
                      </div>
                    )}

                    {plugin.manifest.repository_url && (
                      <div className="flex items-center gap-2 text-sm">
                        <GitBranch className="h-4 w-4 text-muted-foreground" />
                        <span className="text-muted-foreground">仓库:</span>
                        <a
                          href={plugin.manifest.repository_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-primary hover:underline flex items-center gap-1"
                        >
                          GitHub
                          <ExternalLink className="h-3 w-3" />
                        </a>
                      </div>
                    )}

                    <div className="pt-2 border-t">
                      <div className="flex items-center gap-2 text-sm mb-2">
                        <Info className="h-4 w-4 text-muted-foreground" />
                        <span className="text-muted-foreground">支持版本:</span>
                      </div>
                      <div className="text-sm pl-6 font-medium">
                        {plugin.manifest.host_application.min_version}
                        {plugin.manifest.host_application.max_version
                          ? ` - ${plugin.manifest.host_application.max_version}`
                          : ' - 最新版本'}
                      </div>
                    </div>
                  </div>
                </CardContent>
              </Card>

              {/* 分类和标签 */}
              {(plugin.manifest.categories || plugin.manifest.keywords) && (
                <Card>
                  <CardHeader>
                    <CardTitle className="text-lg">分类与标签</CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-4">
                    {plugin.manifest.categories && plugin.manifest.categories.length > 0 && (
                      <div>
                        <p className="text-sm text-muted-foreground mb-2">分类</p>
                        <div className="flex flex-wrap gap-2">
                          {plugin.manifest.categories.map((category) => (
                            <Badge key={category} variant="secondary">
                              {CATEGORY_NAMES[category] || category}
                            </Badge>
                          ))}
                        </div>
                      </div>
                    )}

                    {plugin.manifest.keywords && plugin.manifest.keywords.length > 0 && (
                      <div>
                        <p className="text-sm text-muted-foreground mb-2">标签</p>
                        <div className="flex flex-wrap gap-2">
                          {plugin.manifest.keywords.map((keyword) => (
                            <Badge key={keyword} variant="outline" className="text-xs">
                              <Tag className="h-3 w-3 mr-1" />
                              {keyword}
                            </Badge>
                          ))}
                        </div>
                      </div>
                    )}
                  </CardContent>
                </Card>
              )}
            </div>

            {/* 右侧 - README */}
            <Card className="lg:col-span-2">
              <CardHeader>
                <CardTitle className="text-lg">插件说明</CardTitle>
              </CardHeader>
              <CardContent>
                <ScrollArea className="h-[600px] pr-4">
                  {readmeLoading ? (
                    <div className="flex items-center justify-center py-12">
                      <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                      <span className="ml-3 text-sm text-muted-foreground">加载说明文档中...</span>
                    </div>
                  ) : readme ? (
                    <MarkdownRenderer content={readme} />
                  ) : (
                    <div className="text-center text-muted-foreground py-12">
                      暂无说明文档
                    </div>
                  )}
                </ScrollArea>
              </CardContent>
            </Card>
          </div>
        </div>
      </ScrollArea>
    </div>
  )
}
