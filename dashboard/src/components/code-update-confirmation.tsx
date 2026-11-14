import { useSyncExternalStore } from 'react'
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import {
  getUpdateConfirmation, resolveUpdateConfirmation, subscribeUpdateConfirmation,
} from '@/lib/update-confirmation'

export function CodeUpdateConfirmation() {
  const request = useSyncExternalStore(subscribeUpdateConfirmation, getUpdateConfirmation)
  return (
    <AlertDialog open={request !== null} onOpenChange={(open) => { if (!open) resolveUpdateConfirmation(false) }}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>备份并强制更新</AlertDialogTitle>
          <AlertDialogDescription>
            {request?.message}。将备份插件代码和配置到 plugins/.update_backups，再替换代码。
            现有配置和运行数据保留，本地代码修改将被覆盖。
          </AlertDialogDescription>
        </AlertDialogHeader>
        {request?.diverged && <p className="text-sm">本地提交与目标版本分叉，强制更新后将对齐到目标版本。</p>}
        {!!request?.files.length && (
          <ul className="max-h-52 overflow-auto text-sm break-all">
            {request.files.map((file) => <li key={file}><code>{file}</code></li>)}
          </ul>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel onClick={() => resolveUpdateConfirmation(false)}>取消</AlertDialogCancel>
          <AlertDialogAction onClick={() => resolveUpdateConfirmation(true)}>备份并强制更新</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
