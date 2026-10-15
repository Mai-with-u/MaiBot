import { useState } from 'react'
import { Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useToast } from '@/hooks/use-toast'
import { backendApi } from '@/lib/http'
import { getInstalledPlugins } from '@/lib/plugin-api'

interface ZipInstallDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onInstalled: () => Promise<void>
}

export function ZipInstallDialog({ open, onOpenChange, onInstalled }: ZipInstallDialogProps) {
  const [file, setFile] = useState<File | null>(null)
  const [installing, setInstalling] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const { toast } = useToast()

  const install = async () => {
    if (!file) return
    setInstalling(true)
    setError(null)
    try {
      const body = new FormData()
      body.append('file', file)
      const result = await backendApi.post<{ message: string }>('/api/webui/plugins/install-zip', {
        body,
        errorMessage: '从 ZIP 安装插件失败',
      })
      toast({ title: '安装成功', description: result.message })
      onOpenChange(false)
      setFile(null)
      await getInstalledPlugins({ forceRefresh: true })
      await onInstalled()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '从 ZIP 安装插件失败')
    } finally {
      setInstalling(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(nextOpen) => {
      if (installing) return
      onOpenChange(nextOpen)
      setFile(null)
      setError(null)
    }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>从 ZIP 安装插件</DialogTitle>
          <DialogDescription>
            选择可信来源的插件 ZIP（最大 100 MB）。压缩包根目录或一层插件目录中需包含 _manifest.json 和 plugin.py，校验通过后安装到 plugins 文件夹。已安装的插件需先卸载。
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-2">
          <Label htmlFor="plugin-zip-file">插件 ZIP 文件</Label>
          <Input id="plugin-zip-file" type="file" accept=".zip,application/zip" disabled={installing}
            onChange={(event) => {
              const selected = event.target.files?.[0] ?? null
              setFile(null)
              setError(null)
              if (selected && (!selected.name.toLowerCase().endsWith('.zip') || selected.size > 100 * 1024 * 1024)) {
                setError('请选择不超过 100 MB 的 ZIP 文件')
                return
              }
              setFile(selected)
            }} />
          {error && <p role="alert" className="text-destructive whitespace-pre-wrap break-all text-sm">{error}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" disabled={installing} onClick={() => {
            onOpenChange(false)
            setFile(null)
            setError(null)
          }}>取消</Button>
          <Button disabled={!file || installing} onClick={install}>
            {installing && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            {installing ? '正在校验并安装...' : '安装插件'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
