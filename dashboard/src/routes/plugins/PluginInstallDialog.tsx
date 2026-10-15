import { forwardRef, useImperativeHandle, useState } from 'react'

import { InstallDialog } from './InstallDialog'
import type { PluginInfo, PluginProgressById } from './types'

export interface PluginInstallDialogHandle {
  open: (plugin: PluginInfo) => void
}

interface PluginInstallDialogProps {
  progressById: PluginProgressById
  onInstall: (plugin: PluginInfo, target: string) => void
}

// 弹窗开关只更新此组件，避免打开和关闭时重新渲染整个市场页。
export const PluginInstallDialog = forwardRef<PluginInstallDialogHandle, PluginInstallDialogProps>(
  function PluginInstallDialog({ progressById, onInstall }, ref) {
    const [plugin, setPlugin] = useState<PluginInfo | null>(null)
    const progress = plugin ? progressById[plugin.id] ?? null : null

    useImperativeHandle(ref, () => ({ open: setPlugin }), [])

    return (
      <InstallDialog
        open={plugin !== null}
        plugin={plugin}
        loadProgress={progress}
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
