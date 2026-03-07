import { useState } from 'react'
import { Check, Loader2, Pencil, Plus, Server, Trash2 } from 'lucide-react'

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ScrollArea } from '@/components/ui/scroll-area'
import { useBackendConnections } from '@/hooks/useBackendConnections'
import { isElectron } from '@/lib/runtime'
import type { BackendConnection } from '@/types/electron'

export interface BackendManagerProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function BackendManager({ open, onOpenChange }: BackendManagerProps) {
  const {
    activeId,
    addBackend,
    backends,
    loading,
    removeBackend,
    switchBackend,
    updateBackend,
  } = useBackendConnections()

  const [editConn, setEditConn] = useState<Partial<BackendConnection> | null>(null)
  const [deleteConn, setDeleteConn] = useState<BackendConnection | null>(null)

  if (!isElectron()) return null

  const handleSave = async () => {
    if (!editConn?.name || !editConn?.url) return
    const urlPattern = /^https?:\/\//
    if (!urlPattern.test(editConn.url)) return

    if (editConn.id) {
      await updateBackend(editConn.id, editConn)
    } else {
      await addBackend({
        name: editConn.name,
        url: editConn.url,
        isDefault: editConn.isDefault ?? false,
      })
    }
    setEditConn(null)
  }

  const handleDelete = async () => {
    if (!deleteConn) return
    if (deleteConn.id === activeId) return
    await removeBackend(deleteConn.id)
    setDeleteConn(null)
  }

  const handleSwitch = async (id: string) => {
    if (id === activeId) return
    await switchBackend(id)
  }

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="max-w-md sm:max-w-[425px]">
          <DialogHeader>
            <DialogTitle>后端连接管理</DialogTitle>
          </DialogHeader>

          {loading ? (
            <div className="flex h-32 items-center justify-center">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          ) : (
            <ScrollArea className="max-h-[60vh] pr-4">
              <div className="flex flex-col gap-3 py-4">
                {backends.map((backend) => {
                  const isActive = backend.id === activeId
                  return (
                    <div
                      key={backend.id}
                      className={`flex items-center justify-between rounded-lg border p-3 transition-colors ${
                        isActive ? 'border-blue-500 bg-blue-500/10' : 'border-border'
                      }`}
                    >
                      <div className="flex flex-1 items-center gap-3 overflow-hidden">
                        <div className="flex-shrink-0">
                          {isActive ? (
                            <Check className="h-5 w-5 text-blue-500" />
                          ) : (
                            <div className="h-3 w-3 rounded-full bg-muted-foreground/30 ml-1" title="未知状态" />
                          )}
                        </div>
                        <div className="flex flex-col overflow-hidden">
                          <span className="truncate font-medium leading-none">
                            {backend.name}
                          </span>
                          <span className="truncate text-xs text-muted-foreground mt-1">
                            {backend.url}
                          </span>
                        </div>
                      </div>

                      <div className="flex items-center gap-1 ml-2">
                        {!isActive && (
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-8 w-8"
                            onClick={() => handleSwitch(backend.id)}
                            title="切换到此后端"
                          >
                            <Server className="h-4 w-4" />
                            <span className="sr-only">切换到此后端</span>
                          </Button>
                        )}
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-8 w-8"
                          onClick={() => setEditConn(backend)}
                          title="编辑"
                        >
                          <Pencil className="h-4 w-4" />
                          <span className="sr-only">编辑</span>
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-8 w-8"
                          onClick={() => setDeleteConn(backend)}
                          disabled={isActive}
                          title={isActive ? '无法删除活跃后端' : '删除'}
                        >
                          <Trash2 className="h-4 w-4" />
                          <span className="sr-only">删除</span>
                        </Button>
                      </div>
                    </div>
                  )
                })}
              </div>
            </ScrollArea>
          )}

          <div className="flex justify-end pt-4 border-t">
            <Button
              className="w-full"
              onClick={() => setEditConn({ name: '', url: 'http://', isDefault: false })}
            >
              <Plus className="mr-2 h-4 w-4" />
              添加新连接
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* Edit/Add Dialog */}
      <Dialog open={!!editConn} onOpenChange={(open) => !open && setEditConn(null)}>
        <DialogContent className="sm:max-w-[425px]">
          <DialogHeader>
            <DialogTitle>{editConn?.id ? '编辑连接' : '添加连接'}</DialogTitle>
          </DialogHeader>
          <div className="grid gap-4 py-4">
            <div className="grid gap-2">
              <Label htmlFor="name">名称</Label>
              <Input
                id="name"
                value={editConn?.name || ''}
                onChange={(e) =>
                  setEditConn((prev) => (prev ? { ...prev, name: e.target.value } : null))
                }
                placeholder="我的服务器"
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="url">URL</Label>
              <Input
                id="url"
                value={editConn?.url || ''}
                onChange={(e) =>
                  setEditConn((prev) => (prev ? { ...prev, url: e.target.value } : null))
                }
                placeholder="http://192.168.1.100:8001"
              />
            </div>
          </div>
          <div className="flex justify-end gap-3">
            <Button variant="outline" onClick={() => setEditConn(null)}>
              取消
            </Button>
            <Button
              onClick={handleSave}
              disabled={
                !editConn?.name ||
                !editConn?.url ||
                !/^https?:\/\//.test(editConn.url)
              }
            >
              保存
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* Delete Confirmation */}
      <AlertDialog open={!!deleteConn} onOpenChange={(open) => !open && setDeleteConn(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除连接</AlertDialogTitle>
            <AlertDialogDescription>
              确定要删除 {deleteConn?.name} 吗？此操作不可撤销。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleDelete}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
