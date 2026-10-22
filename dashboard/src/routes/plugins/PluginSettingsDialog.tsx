import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2 } from 'lucide-react'
import { backendApi } from '@/lib/http'
import { invalidatePluginMarketCache } from '@/lib/plugin-api'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useToast } from '@/hooks/use-toast'

interface SourceSettings {
  information_source: 'official' | 'cnb' | 'github'
  download_source: 'cnb' | 'github' | 'custom'
  custom_download_prefix: string | null
}

interface PluginSettingsDialogProps {
  onClose: () => void
  onSaved: () => void
}

export function PluginSettingsDialog({ onClose, onSaved }: PluginSettingsDialogProps) {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const [draft, setDraft] = useState<SourceSettings | null>(null)
  const sourceQuery = useQuery({
    queryKey: ['plugin-market-source'],
    queryFn: () => backendApi.get<SourceSettings>('/api/webui/plugins/marketplace/source', {
      errorMessage: '获取插件商店设置失败',
    }),
  })
  useEffect(() => {
    if (sourceQuery.data) setDraft(sourceQuery.data)
  }, [sourceQuery.data])

  // 两组设置一起保存，避免切换自定义源时提交尚未填写的地址。
  const sourceMutation = useMutation({
    mutationFn: (settings: SourceSettings) => backendApi.put<SourceSettings>(
      '/api/webui/plugins/marketplace/source', {
        body: settings, errorMessage: '保存插件商店设置失败',
      }
    ),
    meta: { errorTitle: '保存失败' },
    onSuccess: (data) => {
      setDraft(data)
      queryClient.setQueryData(['plugin-market-source'], data)
      invalidatePluginMarketCache()
      void queryClient.invalidateQueries({ queryKey: ['plugin-detail'] })
      toast({ title: '设置已保存' })
      onSaved()
    },
  })
  const dirty = draft && JSON.stringify(draft) !== JSON.stringify(sourceQuery.data)
  const disabled = sourceMutation.isPending

  return (
    <Dialog open onOpenChange={(open) => { if (!open && !disabled) onClose() }}>
      <DialogContent className="[--dialog-width:40rem]" preventOutsideClose={disabled}>
        <DialogHeader>
          <DialogTitle>插件商店设置</DialogTitle>
          <DialogDescription>分别选择插件信息和插件下载的来源</DialogDescription>
        </DialogHeader>
        <DialogBody>
          <div className="space-y-4">
            {sourceQuery.isPending && <Loader2 className="h-6 w-6 animate-spin" />}
            {sourceQuery.isError && (
              <Card className="p-6 space-y-3">
                <p className="text-destructive">{sourceQuery.error.message}</p>
                <Button variant="outline" onClick={() => sourceQuery.refetch()}>重试</Button>
              </Card>
            )}
            {draft && <>
              <Card className="p-6 space-y-4">
                <div className="space-y-1">
                  <Label htmlFor="plugin-information-source" className="text-base font-semibold">插件信息源</Label>
                  <p className="text-sm text-muted-foreground">获取插件列表、详情和版本信息，只请求所选来源，失败时直接报错。</p>
                </div>
                <Select value={draft.information_source} disabled={disabled}
                  onValueChange={(value: SourceSettings['information_source']) => setDraft({ ...draft, information_source: value })}>
                  <SelectTrigger id="plugin-information-source"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="official">麦麦官方</SelectItem>
                    <SelectItem value="cnb">CNB镜像</SelectItem>
                    <SelectItem value="github">Github源</SelectItem>
                  </SelectContent>
                </Select>
                <p className="text-sm text-muted-foreground">下载量、点赞、评分和评论仍由麦麦官方统计服务提供，统计加载失败不影响插件列表。</p>
              </Card>
              <Card className="p-6 space-y-4">
                <div className="space-y-1">
                  <Label htmlFor="plugin-download-source" className="text-base font-semibold">插件下载源</Label>
                  <p className="text-sm text-muted-foreground">安装和更新插件只使用一个下载源，失败时不自动切换。</p>
                </div>
                <Select value={draft.download_source} disabled={disabled}
                  onValueChange={(value: SourceSettings['download_source']) => setDraft({ ...draft, download_source: value })}>
                  <SelectTrigger id="plugin-download-source"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="cnb">CNB镜像</SelectItem>
                    <SelectItem value="github">Github源</SelectItem>
                    <SelectItem value="custom">自定义源</SelectItem>
                  </SelectContent>
                </Select>
                {draft.download_source === 'custom' && <div className="space-y-2">
                  <Label htmlFor="plugin-custom-download">Git 克隆前缀</Label>
                  <Input id="plugin-custom-download" disabled={disabled}
                    placeholder="https://example.com/https://github.com"
                    value={draft.custom_download_prefix ?? ''}
                    onChange={(event) => setDraft({ ...draft, custom_download_prefix: event.target.value })} />
                  <p className="text-xs text-muted-foreground">填写一个下载源前缀，下载地址会在其后追加 /作者/仓库.git。</p>
                </div>}
              </Card>
            </>}
          </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" disabled={disabled} onClick={onClose}>取消</Button>
          <Button disabled={!draft || !dirty || disabled || (draft.download_source === 'custom' && !draft.custom_download_prefix?.trim())}
            onClick={() => { if (draft) sourceMutation.mutate(draft) }}>
            {disabled && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}保存设置
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
