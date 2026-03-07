import { AlertTriangle, Database, Download, HardDrive, RefreshCw, RotateCcw, Trash2, Upload } from 'lucide-react'
import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from '@tanstack/react-router'

import { cn } from '@/lib/utils'
import { fetchWithAuth } from '@/lib/fetch-with-auth'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Slider } from '@/components/ui/slider'
import { useToast } from '@/hooks/use-toast'
import { clearLocalCache, DEFAULT_SETTINGS, exportSettings, formatBytes, getSetting, getStorageUsage, importSettings, resetAllSettings, setSetting } from '@/lib/settings-manager'
import { logWebSocket } from '@/lib/log-websocket'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger } from '@/components/ui/alert-dialog'

// 其他设置标签页
export function OtherTab() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { toast } = useToast()
  const [isResetting, setIsResetting] = useState(false)
  const [shouldThrowError, setShouldThrowError] = useState(false)
  
  // 性能与存储设置状态
  const [logCacheSize, setLogCacheSize] = useState(() => getSetting('logCacheSize'))
  const [wsReconnectInterval, setWsReconnectInterval] = useState(() => getSetting('wsReconnectInterval'))
  const [wsMaxReconnectAttempts, setWsMaxReconnectAttempts] = useState(() => getSetting('wsMaxReconnectAttempts'))
  const [dataSyncInterval, setDataSyncInterval] = useState(() => getSetting('dataSyncInterval'))
  const [storageUsage, setStorageUsage] = useState(() => getStorageUsage())
  
  // 导入/导出状态
  const [isExporting, setIsExporting] = useState(false)
  const [isImporting, setIsImporting] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  // 手动触发 React 错误
  if (shouldThrowError) {
    throw new Error('这是一个手动触发的测试错误，用于验证错误边界组件是否正常工作。')
  }

  // 刷新存储使用情况
  const refreshStorageUsage = () => {
    setStorageUsage(getStorageUsage())
  }

  // 处理日志缓存大小变更
  const handleLogCacheSizeChange = (value: number[]) => {
    const size = value[0]
    setLogCacheSize(size)
    setSetting('logCacheSize', size)
  }

  // 处理 WebSocket 重连间隔变更
  const handleWsReconnectIntervalChange = (value: number[]) => {
    const interval = value[0]
    setWsReconnectInterval(interval)
    setSetting('wsReconnectInterval', interval)
  }

  // 处理 WebSocket 最大重连次数变更
  const handleWsMaxReconnectAttemptsChange = (value: number[]) => {
    const attempts = value[0]
    setWsMaxReconnectAttempts(attempts)
    setSetting('wsMaxReconnectAttempts', attempts)
  }

  // 处理数据同步间隔变更
  const handleDataSyncIntervalChange = (value: number[]) => {
    const interval = value[0]
    setDataSyncInterval(interval)
    setSetting('dataSyncInterval', interval)
  }

  // 清除日志缓存
  const handleClearLogCache = () => {
    logWebSocket.clearLogs()
    toast({
      title: t('settings.other.logCleared'),
      description: t('settings.other.logClearedDesc'),
    })
  }

  // 清除本地缓存
  const handleClearLocalCache = () => {
    const result = clearLocalCache()
    refreshStorageUsage()
    toast({
      title: t('settings.other.cacheCleared'),
      description: t('settings.other.cacheClearedDesc', { count: result.clearedKeys.length }),
    })
  }

  // 导出设置
  const handleExportSettings = () => {
    setIsExporting(true)
    try {
      const settings = exportSettings()
      const dataStr = JSON.stringify(settings, null, 2)
      const blob = new Blob([dataStr], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `maibot-webui-settings-${new Date().toISOString().slice(0, 10)}.json`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
      toast({
        title: t('settings.other.exportSuccess'),
        description: t('settings.other.exportSuccessDesc'),
      })
    } catch (error) {
      console.error('导出设置失败:', error)
      toast({
        title: t('settings.other.exportFailed'),
        description: t('settings.other.exportFailedDesc'),
        variant: 'destructive',
      })
    } finally {
      setIsExporting(false)
    }
  }

  // 导入设置
  const handleImportSettings = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file) return

    setIsImporting(true)
    const reader = new FileReader()
    reader.onload = (e) => {
      try {
        const content = e.target?.result as string
        const settings = JSON.parse(content)
        const result = importSettings(settings)
        
        if (result.success) {
          // 刷新页面状态
          setLogCacheSize(getSetting('logCacheSize'))
          setWsReconnectInterval(getSetting('wsReconnectInterval'))
          setWsMaxReconnectAttempts(getSetting('wsMaxReconnectAttempts'))
          setDataSyncInterval(getSetting('dataSyncInterval'))
          refreshStorageUsage()
          
          toast({
            title: t('settings.other.importSuccess'),
            description: t('settings.other.importSuccessDesc', { imported: result.imported.length }) + (result.skipped.length > 0 ? t('settings.other.importSkippedSuffix', { skipped: result.skipped.length }) : ''),
          })
          
          // 提示用户刷新页面以应用所有更改
          if (result.imported.includes('theme') || result.imported.includes('accentColor')) {
            toast({
              title: t('settings.other.importRefreshHint'),
              description: t('settings.other.importRefreshHintDesc'),
            })
          }
        } else {
          toast({
            title: t('settings.other.importFailed'),
            description: t('settings.other.importNoDataDesc'),
            variant: 'destructive',
          })
        }
      } catch (error) {
        console.error('导入设置失败:', error)
        toast({
          title: t('settings.other.importFailed'),
          description: t('settings.other.importInvalidDesc'),
          variant: 'destructive',
        })
      } finally {
        setIsImporting(false)
        // 清空 input，允许重复选择同一文件
        if (fileInputRef.current) {
          fileInputRef.current.value = ''
        }
      }
    }
    reader.readAsText(file)
  }

  // 重置所有设置
  const handleResetAllSettings = () => {
    resetAllSettings()
    // 刷新页面状态
    setLogCacheSize(DEFAULT_SETTINGS.logCacheSize)
    setWsReconnectInterval(DEFAULT_SETTINGS.wsReconnectInterval)
    setWsMaxReconnectAttempts(DEFAULT_SETTINGS.wsMaxReconnectAttempts)
    setDataSyncInterval(DEFAULT_SETTINGS.dataSyncInterval)
    refreshStorageUsage()
    toast({
      title: t('settings.other.resetDone'),
      description: t('settings.other.resetDoneDesc'),
    })
  }

  const handleResetSetup = async () => {
    setIsResetting(true)

    try {
      // 调用后端API重置首次配置状态
      const response = await fetchWithAuth('/api/webui/setup/reset', {
        method: 'POST',
      })

      const data = await response.json()

      if (response.ok && data.success) {
        toast({
          title: t('settings.other.resetSuccess'),
          description: t('settings.other.clearStorageSuccess'),
        })

        // 延迟跳转到配置向导
        setTimeout(() => {
          navigate({ to: '/setup' })
        }, 1000)
      } else {
        toast({
          title: t('settings.other.resetFailed'),
          description: data.message || t('settings.other.clearStorageFailed'),
          variant: 'destructive',
        })
      }
    } catch (error) {
      console.error('重置配置状态错误:', error)
      toast({
        title: t('settings.other.resetFailed'),
        description: t('settings.other.clearStorageFailed'),
        variant: 'destructive',
      })
    } finally {
      setIsResetting(false)
    }
  }

  return (
    <div className="space-y-4 sm:space-y-6">
      {/* 性能与存储 */}
      <div className="rounded-lg border bg-card p-4 sm:p-6">
        <h3 className="text-base sm:text-lg font-semibold mb-3 sm:mb-4 flex items-center gap-2">
          <Database className="h-5 w-5" />
          {t('settings.other.performance')}
        </h3>
        <div className="space-y-4 sm:space-y-5">
          {/* 存储使用情况 */}
          <div className="rounded-lg bg-muted/50 p-3 sm:p-4">
            <div className="flex items-center justify-between mb-2">
              <span className="text-sm font-medium flex items-center gap-2">
                <HardDrive className="h-4 w-4" />
                {t('settings.other.localStorage')}
              </span>
              <Button variant="ghost" size="sm" onClick={refreshStorageUsage} className="h-7 px-2">
                <RefreshCw className="h-3 w-3" />
              </Button>
            </div>
            <div className="text-2xl font-bold text-primary">{formatBytes(storageUsage.used)}</div>
            <p className="text-xs text-muted-foreground mt-1">{t('settings.other.storageItems', { count: storageUsage.items })}</p>
          </div>

          {/* 日志缓存大小 */}
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Label className="text-sm font-medium">{t('settings.other.logCache')}</Label>
              <span className="text-sm text-muted-foreground">{logCacheSize} {t('settings.other.logCacheSizeUnit')}</span>
            </div>
            <Slider
              value={[logCacheSize]}
              onValueChange={handleLogCacheSizeChange}
              min={100}
              max={5000}
              step={100}
              className="w-full"
            />
            <p className="text-xs text-muted-foreground">
              {t('settings.other.logCacheSizeDesc')}
            </p>
          </div>

          {/* 数据刷新间隔 */}
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Label className="text-sm font-medium">{t('settings.other.dataSyncIntervalLabel')}</Label>
              <span className="text-sm text-muted-foreground">{dataSyncInterval} {t('settings.other.dataSyncIntervalUnit')}</span>
            </div>
            <Slider
              value={[dataSyncInterval]}
              onValueChange={handleDataSyncIntervalChange}
              min={10}
              max={120}
              step={5}
              className="w-full"
            />
            <p className="text-xs text-muted-foreground">
              {t('settings.other.dataSyncIntervalDesc')}
            </p>
          </div>

          {/* WebSocket 重连间隔 */}
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Label className="text-sm font-medium">{t('settings.other.wsReconnectLabel')}</Label>
              <span className="text-sm text-muted-foreground">{wsReconnectInterval / 1000} {t('settings.other.wsReconnectUnit')}</span>
            </div>
            <Slider
              value={[wsReconnectInterval]}
              onValueChange={handleWsReconnectIntervalChange}
              min={1000}
              max={10000}
              step={500}
              className="w-full"
            />
            <p className="text-xs text-muted-foreground">
              {t('settings.other.wsReconnectDesc')}
            </p>
          </div>

          {/* WebSocket 最大重连次数 */}
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Label className="text-sm font-medium">{t('settings.other.wsMaxReconnectLabel')}</Label>
              <span className="text-sm text-muted-foreground">{wsMaxReconnectAttempts} {t('settings.other.wsMaxReconnectUnit')}</span>
            </div>
            <Slider
              value={[wsMaxReconnectAttempts]}
              onValueChange={handleWsMaxReconnectAttemptsChange}
              min={3}
              max={30}
              step={1}
              className="w-full"
            />
            <p className="text-xs text-muted-foreground">
              {t('settings.other.wsMaxReconnectDesc')}
            </p>
          </div>

          {/* 清理按钮 */}
          <div className="flex flex-wrap gap-2 pt-2">
            <Button variant="outline" size="sm" onClick={handleClearLogCache} className="gap-2">
              <Trash2 className="h-4 w-4" />
              {t('settings.other.clearLogCacheFn')}
            </Button>
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button variant="outline" size="sm" className="gap-2">
                  <Trash2 className="h-4 w-4" />
                  {t('settings.other.clearLocalCache')}
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>{t('settings.other.confirmClearCache')}</AlertDialogTitle>
                  <AlertDialogDescription>
                    {t('settings.other.confirmClearCacheDesc')}
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>{t('common.cancel')}</AlertDialogCancel>
                  <AlertDialogAction onClick={handleClearLocalCache}>
                    {t('settings.other.confirmClear')}
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </div>
        </div>
      </div>

      {/* 导入/导出设置 */}
      <div className="rounded-lg border bg-card p-4 sm:p-6">
        <h3 className="text-base sm:text-lg font-semibold mb-3 sm:mb-4 flex items-center gap-2">
          <Download className="h-5 w-5" />
          {t('settings.other.importExport')}
        </h3>
        <div className="space-y-4">
          <p className="text-xs sm:text-sm text-muted-foreground">
            {t('settings.other.importExportDesc')}
          </p>
          
          <div className="flex flex-wrap gap-2">
            <Button 
              variant="outline" 
              onClick={handleExportSettings} 
              disabled={isExporting}
              className="gap-2"
            >
              <Download className="h-4 w-4" />
              {isExporting ? t('settings.other.exporting') : t('settings.other.exportSettings')}
            </Button>
            
            <input
              ref={fileInputRef}
              type="file"
              accept=".json"
              onChange={handleImportSettings}
              className="hidden"
            />
            <Button 
              variant="outline" 
              onClick={() => fileInputRef.current?.click()}
              disabled={isImporting}
              className="gap-2"
            >
              <Upload className="h-4 w-4" />
              {isImporting ? t('settings.other.importing') : t('settings.other.importSettings')}
            </Button>
          </div>

          {/* 重置所有设置 */}
          <div className="pt-2 border-t">
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button variant="outline" size="sm" className="gap-2 text-destructive hover:text-destructive">
                  <RotateCcw className="h-4 w-4" />
                  {t('settings.other.resetAllSettingsBtn')}
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>{t('settings.other.confirmResetAll')}</AlertDialogTitle>
                  <AlertDialogDescription>
                    {t('settings.other.confirmResetAllDesc')}
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>{t('common.cancel')}</AlertDialogCancel>
                  <AlertDialogAction onClick={handleResetAllSettings}>
                    {t('settings.other.resetAllSettingsConfirm')}
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </div>
        </div>
      </div>

      {/* 配置向导 */}
      <div className="rounded-lg border bg-card p-4 sm:p-6">
        <h3 className="text-base sm:text-lg font-semibold mb-3 sm:mb-4">{t('settings.other.configWizard')}</h3>
        <div className="space-y-3 sm:space-y-4">
          <div className="space-y-2">
            <p className="text-xs sm:text-sm text-muted-foreground">
              {t('settings.other.configWizardDesc')}
            </p>
          </div>
          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button variant="outline" disabled={isResetting} className="gap-2">
                <RotateCcw className={cn('h-4 w-4', isResetting && 'animate-spin')} />
                {t('settings.other.rerunSetup')}
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>{t('settings.other.confirmRerunSetup')}</AlertDialogTitle>
                <AlertDialogDescription>
                  {t('settings.other.confirmRerunSetupDesc')}
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>{t('common.cancel')}</AlertDialogCancel>
                <AlertDialogAction onClick={handleResetSetup}>
                  {t('settings.other.resetAllSettingsConfirm')}
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </div>
      </div>

      {/* 开发者工具 */}
      <div className="rounded-lg border border-dashed border-yellow-500/50 bg-yellow-500/5 p-4 sm:p-6">
        <h3 className="text-base sm:text-lg font-semibold mb-3 sm:mb-4 flex items-center gap-2">
          <AlertTriangle className="h-5 w-5 text-yellow-500" />
          {t('settings.other.devTools')}
        </h3>
        <div className="space-y-3 sm:space-y-4">
          <div className="space-y-2">
            <p className="text-xs sm:text-sm text-muted-foreground">
              {t('settings.other.devToolsDesc')}
            </p>
          </div>
          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button variant="destructive" className="gap-2">
                <AlertTriangle className="h-4 w-4" />
                {t('settings.other.triggerError')}
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>{t('settings.other.confirmTriggerError')}</AlertDialogTitle>
                <AlertDialogDescription>
                  {t('settings.other.confirmTriggerErrorDesc')}
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>{t('common.cancel')}</AlertDialogCancel>
                <AlertDialogAction 
                  onClick={() => setShouldThrowError(true)}
                  className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                >
                  {t('settings.other.confirmTrigger')}
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </div>
      </div>
    </div>
  )
}
