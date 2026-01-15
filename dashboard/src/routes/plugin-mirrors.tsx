import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { fetchWithAuth } from '@/lib/fetch-with-auth'
import { Card } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Switch } from '@/components/ui/switch'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { ArrowLeft, Plus, Pencil, Trash2, Loader2, AlertTriangle, ChevronUp, ChevronDown } from 'lucide-react'
import { useToast } from '@/hooks/use-toast'

interface MirrorConfig {
  id: string
  name: string
  raw_prefix: string
  clone_prefix: string
  enabled: boolean
  priority: number
  created_at?: string
  updated_at?: string
}

export function PluginMirrorsPage() {
  const navigate = useNavigate()
  const { toast } = useToast()
  const [mirrors, setMirrors] = useState<MirrorConfig[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [editingMirror, setEditingMirror] = useState<MirrorConfig | null>(null)
  const [isAddDialogOpen, setIsAddDialogOpen] = useState(false)
  const [isEditDialogOpen, setIsEditDialogOpen] = useState(false)

  // 表单状态
  const [formData, setFormData] = useState({
    id: '',
    name: '',
    raw_prefix: '',
    clone_prefix: '',
    enabled: true,
    priority: 1
  })

  // 加载镜像源列表
  const loadMirrors = useCallback(async () => {
    try {
      setLoading(true)
      setError(null)
      
      const response = await fetchWithAuth('/api/webui/plugins/mirrors')
      
      if (!response.ok) {
        throw new Error('获取镜像源列表失败')
      }
      
      const data = await response.json()
      setMirrors(data.mirrors || [])
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : '加载镜像源失败'
      setError(errorMessage)
      toast({
        title: '加载失败',
        description: errorMessage,
        variant: 'destructive',
      })
    } finally {
      setLoading(false)
    }
  }, [toast])

  useEffect(() => {
    loadMirrors()
  }, [loadMirrors])

  // 添加镜像源
  const handleAddMirror = async () => {
    try {
      const response = await fetchWithAuth('/api/webui/plugins/mirrors', {
        method: 'POST',
        body: JSON.stringify(formData)
      })

      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(errorData.detail || '添加镜像源失败')
      }

      toast({
        title: '添加成功',
        description: '镜像源已添加'
      })

      setIsAddDialogOpen(false)
      setFormData({
        id: '',
        name: '',
        raw_prefix: '',
        clone_prefix: '',
        enabled: true,
        priority: 1
      })
      loadMirrors()
    } catch (err) {
      toast({
        title: '添加失败',
        description: err instanceof Error ? err.message : '未知错误',
        variant: 'destructive'
      })
    }
  }

  // 更新镜像源
  const handleUpdateMirror = async () => {
    if (!editingMirror) return

    try {
      const response = await fetchWithAuth(`/api/webui/plugins/mirrors/${editingMirror.id}`, {
        method: 'PUT',
        body: JSON.stringify({
          name: formData.name,
          raw_prefix: formData.raw_prefix,
          clone_prefix: formData.clone_prefix,
          enabled: formData.enabled,
          priority: formData.priority
        })
      })

      if (!response.ok) {
        throw new Error('更新镜像源失败')
      }

      toast({
        title: '更新成功',
        description: '镜像源已更新'
      })

      setIsEditDialogOpen(false)
      setEditingMirror(null)
      loadMirrors()
    } catch (err) {
      toast({
        title: '更新失败',
        description: err instanceof Error ? err.message : '未知错误',
        variant: 'destructive'
      })
    }
  }

  // 删除镜像源
  const handleDeleteMirror = async (id: string) => {
    if (!confirm('确定要删除这个镜像源吗？')) return

    try {
      const response = await fetchWithAuth(`/api/webui/plugins/mirrors/${id}`, {
        method: 'DELETE'
      })

      if (!response.ok) {
        throw new Error('删除镜像源失败')
      }

      toast({
        title: '删除成功',
        description: '镜像源已删除'
      })

      loadMirrors()
    } catch (err) {
      toast({
        title: '删除失败',
        description: err instanceof Error ? err.message : '未知错误',
        variant: 'destructive'
      })
    }
  }

  // 切换启用状态
  const handleToggleEnabled = async (mirror: MirrorConfig) => {
    try {
      const response = await fetchWithAuth(`/api/webui/plugins/mirrors/${mirror.id}`, {
        method: 'PUT',
        body: JSON.stringify({
          enabled: !mirror.enabled
        })
      })

      if (!response.ok) {
        throw new Error('更新状态失败')
      }

      loadMirrors()
    } catch (err) {
      toast({
        title: '更新失败',
        description: err instanceof Error ? err.message : '未知错误',
        variant: 'destructive'
      })
    }
  }

  // 打开编辑对话框
  const openEditDialog = (mirror: MirrorConfig) => {
    setEditingMirror(mirror)
    setFormData({
      id: mirror.id,
      name: mirror.name,
      raw_prefix: mirror.raw_prefix,
      clone_prefix: mirror.clone_prefix,
      enabled: mirror.enabled,
      priority: mirror.priority
    })
    setIsEditDialogOpen(true)
  }

  // 调整优先级
  const adjustPriority = async (mirror: MirrorConfig, direction: 'up' | 'down') => {
    const newPriority = direction === 'up' ? mirror.priority - 1 : mirror.priority + 1
    if (newPriority < 1) return

    try {
      const response = await fetchWithAuth(`/api/webui/plugins/mirrors/${mirror.id}`, {
        method: 'PUT',
        body: JSON.stringify({
          priority: newPriority
        })
      })

      if (!response.ok) {
        throw new Error('更新优先级失败')
      }

      loadMirrors()
    } catch (err) {
      toast({
        title: '更新失败',
        description: err instanceof Error ? err.message : '未知错误',
        variant: 'destructive'
      })
    }
  }

  return (
    <ScrollArea className="h-full">
      <div className="space-y-6 p-4 sm:p-6">
        {/* 标题栏 */}
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
          <div className="flex items-center gap-4">
            <Button
              variant="ghost"
              size="icon"
              onClick={() => navigate({ to: '/plugins' })}
            >
              <ArrowLeft className="h-5 w-5" />
            </Button>
            <div>
              <h1 className="text-2xl sm:text-3xl font-bold">镜像源配置</h1>
              <p className="text-sm text-muted-foreground mt-1">
                管理 Git 克隆和文件下载的镜像源
              </p>
            </div>
          </div>
          <Button onClick={() => setIsAddDialogOpen(true)}>
            <Plus className="h-4 w-4 mr-2" />
            添加镜像源
          </Button>
        </div>

        {/* 加载状态 */}
        {loading ? (
          <Card className="p-6">
            <div className="flex items-center justify-center py-8">
              <Loader2 className="h-8 w-8 animate-spin text-primary" />
            </div>
          </Card>
        ) : error ? (
          <Card className="p-6">
            <div className="flex flex-col items-center justify-center py-8 text-center">
              <AlertTriangle className="h-12 w-12 text-destructive mb-4" />
              <h3 className="text-lg font-semibold mb-2">加载失败</h3>
              <p className="text-sm text-muted-foreground mb-4">{error}</p>
              <Button onClick={loadMirrors}>重新加载</Button>
            </div>
          </Card>
        ) : (
          <Card>
            {/* 桌面端表格 */}
            <div className="hidden md:block">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>状态</TableHead>
                    <TableHead>名称</TableHead>
                    <TableHead>ID</TableHead>
                    <TableHead>优先级</TableHead>
                    <TableHead className="text-right">操作</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {mirrors.map((mirror) => (
                    <TableRow key={mirror.id}>
                      <TableCell>
                        <Switch
                          checked={mirror.enabled}
                          onCheckedChange={() => handleToggleEnabled(mirror)}
                        />
                      </TableCell>
                      <TableCell>
                        <div>
                          <div className="font-medium">{mirror.name}</div>
                          <div className="text-xs text-muted-foreground mt-1">
                            Raw: {mirror.raw_prefix}
                          </div>
                        </div>
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline">{mirror.id}</Badge>
                      </TableCell>
                      <TableCell>
                        <div className="flex items-center gap-2">
                          <span className="font-mono">{mirror.priority}</span>
                          <div className="flex flex-col gap-1">
                            <Button
                              variant="ghost"
                              size="icon"
                              className="h-5 w-5"
                              onClick={() => adjustPriority(mirror, 'up')}
                              disabled={mirror.priority === 1}
                            >
                              <ChevronUp className="h-3 w-3" />
                            </Button>
                            <Button
                              variant="ghost"
                              size="icon"
                              className="h-5 w-5"
                              onClick={() => adjustPriority(mirror, 'down')}
                            >
                              <ChevronDown className="h-3 w-3" />
                            </Button>
                          </div>
                        </div>
                      </TableCell>
                      <TableCell className="text-right">
                        <div className="flex items-center justify-end gap-2">
                          <Button
                            variant="ghost"
                            size="icon"
                            onClick={() => openEditDialog(mirror)}
                          >
                            <Pencil className="h-4 w-4" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon"
                            onClick={() => handleDeleteMirror(mirror.id)}
                          >
                            <Trash2 className="h-4 w-4 text-destructive" />
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>

            {/* 移动端卡片 */}
            <div className="md:hidden p-4 space-y-4">
              {mirrors.map((mirror) => (
                <Card key={mirror.id} className="p-4">
                  <div className="space-y-3">
                    <div className="flex items-start justify-between">
                      <div className="flex-1">
                        <div className="flex items-center gap-2">
                          <h3 className="font-semibold">{mirror.name}</h3>
                          {mirror.enabled && (
                            <Badge variant="default" className="text-xs">启用</Badge>
                          )}
                        </div>
                        <Badge variant="outline" className="mt-1 text-xs">{mirror.id}</Badge>
                      </div>
                      <Switch
                        checked={mirror.enabled}
                        onCheckedChange={() => handleToggleEnabled(mirror)}
                      />
                    </div>

                    <div className="text-sm space-y-1">
                      <div className="text-muted-foreground">
                        <span className="font-medium">Raw: </span>
                        <span className="break-all">{mirror.raw_prefix}</span>
                      </div>
                      <div className="text-muted-foreground">
                        <span className="font-medium">优先级: </span>
                        <span className="font-mono">{mirror.priority}</span>
                      </div>
                    </div>

                    <div className="flex items-center gap-2 pt-2 border-t">
                      <Button
                        variant="outline"
                        size="sm"
                        className="flex-1"
                        onClick={() => openEditDialog(mirror)}
                      >
                        <Pencil className="h-4 w-4 mr-1" />
                        编辑
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => adjustPriority(mirror, 'up')}
                        disabled={mirror.priority === 1}
                      >
                        <ChevronUp className="h-4 w-4" />
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => adjustPriority(mirror, 'down')}
                      >
                        <ChevronDown className="h-4 w-4" />
                      </Button>
                      <Button
                        variant="destructive"
                        size="sm"
                        onClick={() => handleDeleteMirror(mirror.id)}
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </div>
                  </div>
                </Card>
              ))}
            </div>
          </Card>
        )}

        {/* 添加镜像源对话框 */}
        <Dialog open={isAddDialogOpen} onOpenChange={setIsAddDialogOpen}>
          <DialogContent className="max-w-lg">
            <DialogHeader>
              <DialogTitle>添加镜像源</DialogTitle>
              <DialogDescription>
                添加新的 Git 镜像源配置
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-4 py-4">
              <div className="space-y-2">
                <Label htmlFor="add-id">镜像源 ID *</Label>
                <Input
                  id="add-id"
                  placeholder="例如: my-mirror"
                  value={formData.id}
                  onChange={(e) => setFormData({ ...formData, id: e.target.value })}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="add-name">名称 *</Label>
                <Input
                  id="add-name"
                  placeholder="例如: 我的镜像源"
                  value={formData.name}
                  onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="add-raw">Raw 文件前缀 *</Label>
                <Input
                  id="add-raw"
                  placeholder="https://example.com/raw"
                  value={formData.raw_prefix}
                  onChange={(e) => setFormData({ ...formData, raw_prefix: e.target.value })}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="add-clone">克隆前缀 *</Label>
                <Input
                  id="add-clone"
                  placeholder="https://example.com/clone"
                  value={formData.clone_prefix}
                  onChange={(e) => setFormData({ ...formData, clone_prefix: e.target.value })}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="add-priority">优先级</Label>
                <Input
                  id="add-priority"
                  type="number"
                  min="1"
                  value={formData.priority}
                  onChange={(e) => setFormData({ ...formData, priority: parseInt(e.target.value) || 1 })}
                />
                <p className="text-xs text-muted-foreground">数字越小优先级越高</p>
              </div>
              <div className="flex items-center space-x-2">
                <Switch
                  id="add-enabled"
                  checked={formData.enabled}
                  onCheckedChange={(checked) => setFormData({ ...formData, enabled: checked })}
                />
                <Label htmlFor="add-enabled">启用此镜像源</Label>
              </div>
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => setIsAddDialogOpen(false)}>
                取消
              </Button>
              <Button onClick={handleAddMirror}>
                添加
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {/* 编辑镜像源对话框 */}
        <Dialog open={isEditDialogOpen} onOpenChange={setIsEditDialogOpen}>
          <DialogContent className="max-w-lg">
            <DialogHeader>
              <DialogTitle>编辑镜像源</DialogTitle>
              <DialogDescription>
                修改镜像源配置
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-4 py-4">
              <div className="space-y-2">
                <Label>镜像源 ID</Label>
                <Input value={formData.id} disabled />
              </div>
              <div className="space-y-2">
                <Label htmlFor="edit-name">名称 *</Label>
                <Input
                  id="edit-name"
                  value={formData.name}
                  onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="edit-raw">Raw 文件前缀 *</Label>
                <Input
                  id="edit-raw"
                  value={formData.raw_prefix}
                  onChange={(e) => setFormData({ ...formData, raw_prefix: e.target.value })}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="edit-clone">克隆前缀 *</Label>
                <Input
                  id="edit-clone"
                  value={formData.clone_prefix}
                  onChange={(e) => setFormData({ ...formData, clone_prefix: e.target.value })}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="edit-priority">优先级</Label>
                <Input
                  id="edit-priority"
                  type="number"
                  min="1"
                  value={formData.priority}
                  onChange={(e) => setFormData({ ...formData, priority: parseInt(e.target.value) || 1 })}
                />
                <p className="text-xs text-muted-foreground">数字越小优先级越高</p>
              </div>
              <div className="flex items-center space-x-2">
                <Switch
                  id="edit-enabled"
                  checked={formData.enabled}
                  onCheckedChange={(checked) => setFormData({ ...formData, enabled: checked })}
                />
                <Label htmlFor="edit-enabled">启用此镜像源</Label>
              </div>
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => setIsEditDialogOpen(false)}>
                取消
              </Button>
              <Button onClick={handleUpdateMirror}>
                保存
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>
    </ScrollArea>
  )
}
