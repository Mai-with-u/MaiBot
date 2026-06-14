import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Progress } from '@/components/ui/progress'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { AlertCircle, CheckCircle2, Download, Loader2 } from 'lucide-react'

import type { PluginInfo, PluginLoadProgress } from './types'
import { getPluginProgressDetail } from './types'

interface InstallDialogProps {
  open: boolean
  plugin: PluginInfo | null
  loadProgress: PluginLoadProgress | null
  onOpenChange: (open: boolean) => void
  onInstall: (branch: string) => void
}

export function InstallDialog({ open, plugin, loadProgress, onOpenChange, onInstall }: InstallDialogProps) {
  const [selectedBranch, setSelectedBranch] = useState('main')
  const [customBranch, setCustomBranch] = useState('')
  const [branchInputMode, setBranchInputMode] = useState<'preset' | 'custom'>('preset')
  const [showAdvancedOptions, setShowAdvancedOptions] = useState(false)
  const installProgress = loadProgress?.operation === 'install' && loadProgress.plugin_id === plugin?.id
    ? loadProgress
    : null
  const [lastInstallProgress, setLastInstallProgress] = useState<PluginLoadProgress | null>(null)
  const displayedProgress = installProgress ?? lastInstallProgress
  const isInstalling = displayedProgress?.stage === 'loading'
  const installFinished = displayedProgress?.stage === 'success' || displayedProgress?.stage === 'error'
  const progressDetail = displayedProgress ? getPluginProgressDetail(displayedProgress) : null

  useEffect(() => {
    if (!installProgress) {
      return
    }
    const timer = window.setTimeout(() => setLastInstallProgress(installProgress), 0)
    return () => window.clearTimeout(timer)
  }, [installProgress])

  useEffect(() => {
    if (open) {
      return
    }
    const timer = window.setTimeout(() => setLastInstallProgress(null), 0)
    return () => window.clearTimeout(timer)
  }, [open])

  useEffect(() => {
    const timer = window.setTimeout(() => setLastInstallProgress(null), 0)
    return () => window.clearTimeout(timer)
  }, [plugin?.id])

  const handleInstall = () => {
    const branch = branchInputMode === 'custom' ? customBranch : selectedBranch
    
    if (!branch || branch.trim() === '') {
      return
    }

    onInstall(branch)
  }

  return (
    <Dialog open={open} onOpenChange={(nextOpen) => {
      if (!nextOpen && isInstalling) {
        return
      }
      onOpenChange(nextOpen)
    }}>
      <DialogContent preventOutsideClose={isInstalling} hideCloseButton={isInstalling}>
        <DialogHeader>
          <DialogTitle>安装插件</DialogTitle>
          <DialogDescription>
            安装 {plugin?.manifest.name}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          {/* 基本信息 */}
          <div>
            <p className="text-sm text-muted-foreground">
              版本: {plugin?.manifest.version}
            </p>
            <p className="text-sm text-muted-foreground">
              作者: {typeof plugin?.manifest.author === 'string' 
                ? plugin.manifest.author 
                : plugin?.manifest.author?.name}
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
                {/* eslint-disable-next-line jsx-a11y/label-has-associated-control -- section heading above Tabs, not a form label */}
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

          {displayedProgress && (
            <div className={`space-y-3 rounded-lg border p-3 ${
              displayedProgress.stage === 'success'
                ? 'border-green-200 bg-green-50 dark:border-green-900 dark:bg-green-950/20'
                : displayedProgress.stage === 'error'
                  ? 'border-red-200 bg-red-50 dark:border-red-900 dark:bg-red-950/20'
                  : 'bg-muted/50'
            }`}>
              <div className="flex items-center justify-between gap-3">
                <div className="flex min-w-0 items-center gap-2">
                  {displayedProgress.stage === 'loading' ? (
                    <Loader2 className="h-4 w-4 shrink-0 animate-spin" />
                  ) : displayedProgress.stage === 'success' ? (
                    <CheckCircle2 className="h-4 w-4 shrink-0 text-green-600" />
                  ) : (
                    <AlertCircle className="h-4 w-4 shrink-0 text-red-600" />
                  )}
                  <span className={`text-sm font-medium ${
                    displayedProgress.stage === 'success'
                      ? 'text-green-700 dark:text-green-300'
                      : displayedProgress.stage === 'error'
                        ? 'text-red-700 dark:text-red-300'
                        : ''
                  }`}>
                    {displayedProgress.stage === 'loading' && '正在安装'}
                    {displayedProgress.stage === 'success' && '安装完成'}
                    {displayedProgress.stage === 'error' && '安装失败'}
                  </span>
                </div>
                {displayedProgress.stage !== 'error' && (
                  <span className={`shrink-0 text-sm font-medium ${
                    displayedProgress.stage === 'success' ? 'text-green-700 dark:text-green-300' : ''
                  }`}>
                    {displayedProgress.progress}%
                  </span>
                )}
              </div>
              {displayedProgress.stage !== 'error' && (
                <Progress
                  value={displayedProgress.progress}
                  className={`h-2 ${displayedProgress.stage === 'success' ? '[&>div]:bg-green-500' : ''}`}
                />
              )}
              <p className={`break-words text-sm ${
                displayedProgress.stage === 'success'
                  ? 'text-green-600 dark:text-green-400'
                  : displayedProgress.stage === 'error'
                    ? 'text-red-600 dark:text-red-400'
                    : 'text-muted-foreground'
              }`}>
                {displayedProgress.stage === 'error'
                  ? (displayedProgress.error || displayedProgress.message || '操作失败')
                  : displayedProgress.message}
              </p>
              <div className="grid grid-cols-2 gap-2 text-xs text-muted-foreground">
                <div>插件 ID：{displayedProgress.plugin_id || plugin?.id}</div>
                <div>分支：{branchInputMode === 'custom' ? customBranch : selectedBranch}</div>
              </div>
              {progressDetail && (
                <div className="break-words text-xs text-muted-foreground">
                  {progressDetail}
                </div>
              )}
            </div>
          )}
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={isInstalling}
          >
            {installFinished ? '关闭' : '取消'}
          </Button>
          {!installFinished && (
            <Button onClick={handleInstall} disabled={isInstalling}>
              {isInstalling ? (
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
              ) : (
                <Download className="h-4 w-4 mr-2" />
              )}
              {isInstalling ? '安装中' : '安装'}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
