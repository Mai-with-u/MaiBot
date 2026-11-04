import { forwardRef, useImperativeHandle, useState } from 'react'

import { InstallDialog } from './InstallDialog'
import type { PluginInfo, PluginProgressById } from './types'

export interface PluginInstallDialogHandle {
  open: (plugin: PluginInfo) => void
}

interface PluginInstallDialogProps {
  progressById: PluginProgressById
  onDependenciesChanged: () => void
  onResetProgress: (pluginId: string) => void
  onInstall: (plugin: PluginInfo, target: string) => void
}

// 弹窗开关只更新此组件，避免打开和关闭时重新渲染整个市场页。
export const PluginInstallDialog = forwardRef<PluginInstallDialogHandle, PluginInstallDialogProps>(
  function PluginInstallDialog({ progressById, onResetProgress, onInstall, onDependenciesChanged }, ref) {
    const [plugin, setPlugin] = useState<PluginInfo | null>(null)
    const progress = plugin ? progressById[plugin.id] ?? null : null

    useImperativeHandle(ref, () => ({
      open: (target) => {
        onResetProgress(target.id)
        setPlugin(target)
      },
    }), [onResetProgress])

    // 关闭后卸载弹窗内部的进度缓存，下一次安装从全新状态开始。
    if (!plugin) return null

    return (
      <InstallDialog
        key={plugin.id}
        open={plugin !== null}
        plugin={plugin}
        loadProgress={progress}
        onDependenciesChanged={onDependenciesChanged}
        onOpenChange={(open) => {
          if (open || (progress?.operation === 'install' && progress.stage === 'loading')) return
          setPlugin(null)
        }}
        onInstall={(target) => {
          if (plugin) onInstall(plugin, target)
        }}
      />
    )
  }
)
