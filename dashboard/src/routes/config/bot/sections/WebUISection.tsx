import React, { useState } from 'react'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
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
import { X, Plus } from 'lucide-react'
import type { WebUIConfig } from '../types'

interface WebUISectionProps {
  config: WebUIConfig
  onChange: (config: WebUIConfig) => void
}

export const WebUISection = React.memo(function WebUISection({ config, onChange }: WebUISectionProps) {
  const [newAllowedIp, setNewAllowedIp] = useState('')
  const [newTrustedProxy, setNewTrustedProxy] = useState('')
  const [showDisableWarning, setShowDisableWarning] = useState(false)

  // 将逗号分隔的字符串转换为数组
  const allowedIpsList = config.allowed_ips
    ? config.allowed_ips.split(',').map(ip => ip.trim()).filter(ip => ip)
    : []
  
  const trustedProxiesList = config.trusted_proxies
    ? config.trusted_proxies.split(',').map(ip => ip.trim()).filter(ip => ip)
    : []

  // 处理添加IP白名单
  const handleAddAllowedIp = () => {
    if (!newAllowedIp.trim()) return
    const updatedList = [...allowedIpsList, newAllowedIp.trim()]
    onChange({ ...config, allowed_ips: updatedList.join(',') })
    setNewAllowedIp('')
  }

  // 处理删除IP白名单
  const handleRemoveAllowedIp = (index: number) => {
    const updatedList = allowedIpsList.filter((_, i) => i !== index)
    onChange({ ...config, allowed_ips: updatedList.join(',') })
  }

  // 处理添加信任代理
  const handleAddTrustedProxy = () => {
    if (!newTrustedProxy.trim()) return
    const updatedList = [...trustedProxiesList, newTrustedProxy.trim()]
    onChange({ ...config, trusted_proxies: updatedList.join(',') })
    setNewTrustedProxy('')
  }

  // 处理删除信任代理
  const handleRemoveTrustedProxy = (index: number) => {
    const updatedList = trustedProxiesList.filter((_, i) => i !== index)
    onChange({ ...config, trusted_proxies: updatedList.join(',') })
  }

  // 处理WebUI开关变更
  const handleEnabledChange = (checked: boolean) => {
    if (!checked && config.enabled) {
      // 用户尝试关闭WebUI，显示警告
      setShowDisableWarning(true)
    } else {
      // 用户开启WebUI，直接更新
      onChange({ ...config, enabled: checked })
    }
  }

  // 确认关闭WebUI
  const confirmDisableWebUI = () => {
    onChange({ ...config, enabled: false })
    setShowDisableWarning(false)
  }

  return (
    <div className="rounded-lg border bg-card p-4 sm:p-6 space-y-4">
      <h3 className="text-lg font-semibold">WebUI 服务配置</h3>
      <div className="grid gap-4">
        <div className="flex items-center space-x-2">
          <Switch
            checked={config.enabled}
            onCheckedChange={handleEnabledChange}
          />
          <Label className="cursor-pointer">启用 WebUI</Label>
        </div>

        {config.enabled && (
          <>
            <div className="grid gap-2">
              <Label>运行模式</Label>
              <Select
                value={config.mode}
                onValueChange={(value) => onChange({ ...config, mode: value })}
              >
                <SelectTrigger>
                  <SelectValue placeholder="选择运行模式" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="development">开发模式</SelectItem>
                  <SelectItem value="production">生产模式</SelectItem>
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground">
                注意: WebUI 的监听地址和端口请在 .env 文件中配置 WEBUI_HOST 和 WEBUI_PORT
              </p>
            </div>

            <div className="grid gap-2">
              <Label>防爬虫模式</Label>
              <Select
                value={config.anti_crawler_mode}
                onValueChange={(value) => onChange({ ...config, anti_crawler_mode: value })}
              >
                <SelectTrigger>
                  <SelectValue placeholder="选择防爬虫模式" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="false">禁用</SelectItem>
                  <SelectItem value="basic">基础（只记录不阻止）</SelectItem>
                  <SelectItem value="loose">宽松</SelectItem>
                  <SelectItem value="strict">严格</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div className="grid gap-2 sm:col-span-2">
                <Label>IP 白名单</Label>
                <div className="flex gap-2">
                  <Input
                    value={newAllowedIp}
                    onChange={(e) => setNewAllowedIp(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') {
                        e.preventDefault()
                        handleAddAllowedIp()
                      }
                    }}
                    placeholder="输入IP地址后按回车或点击添加"
                  />
                  <Button
                    type="button"
                    size="sm"
                    onClick={handleAddAllowedIp}
                    disabled={!newAllowedIp.trim()}
                  >
                    <Plus className="h-4 w-4" />
                  </Button>
                </div>
                {allowedIpsList.length > 0 && (
                  <div className="flex flex-wrap gap-2 mt-2">
                    {allowedIpsList.map((ip, index) => (
                      <Badge key={index} variant="secondary" className="flex items-center gap-1">
                        {ip}
                        <button
                          type="button"
                          onClick={() => handleRemoveAllowedIp(index)}
                          className="ml-1 hover:bg-destructive/20 rounded-full p-0.5"
                        >
                          <X className="h-3 w-3" />
                        </button>
                      </Badge>
                    ))}
                  </div>
                )}
                <p className="text-xs text-muted-foreground">
                  支持精确IP、CIDR格式和通配符（如：127.0.0.1、192.168.1.0/24）
                </p>
              </div>

              <div className="grid gap-2 sm:col-span-2">
                <Label>信任的代理 IP</Label>
                <div className="flex gap-2">
                  <Input
                    value={newTrustedProxy}
                    onChange={(e) => setNewTrustedProxy(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') {
                        e.preventDefault()
                        handleAddTrustedProxy()
                      }
                    }}
                    placeholder="输入代理IP后按回车或点击添加"
                  />
                  <Button
                    type="button"
                    size="sm"
                    onClick={handleAddTrustedProxy}
                    disabled={!newTrustedProxy.trim()}
                  >
                    <Plus className="h-4 w-4" />
                  </Button>
                </div>
                {trustedProxiesList.length > 0 && (
                  <div className="flex flex-wrap gap-2 mt-2">
                    {trustedProxiesList.map((ip, index) => (
                      <Badge key={index} variant="secondary" className="flex items-center gap-1">
                        {ip}
                        <button
                          type="button"
                          onClick={() => handleRemoveTrustedProxy(index)}
                          className="ml-1 hover:bg-destructive/20 rounded-full p-0.5"
                        >
                          <X className="h-3 w-3" />
                        </button>
                      </Badge>
                    ))}
                  </div>
                )}
                <p className="text-xs text-muted-foreground">
                  只有来自这些IP的X-Forwarded-For头才被信任
                </p>
              </div>

            <div className="flex items-center space-x-2">
              <Switch
                checked={config.trust_xff}
                onCheckedChange={(checked) => onChange({ ...config, trust_xff: checked })}
              />
              <Label className="cursor-pointer">启用 X-Forwarded-For 代理解析</Label>
            </div>

            <div className="flex items-center space-x-2">
              <Switch
                checked={config.secure_cookie}
                onCheckedChange={(checked) => onChange({ ...config, secure_cookie: checked })}
              />
              <Label className="cursor-pointer">启用安全 Cookie（仅 HTTPS）</Label>
            </div>

            <div className="grid gap-2">
              <div className="flex items-center space-x-2">
                <Switch
                  checked={config.enable_paragraph_content}
                  onCheckedChange={(checked) => onChange({ ...config, enable_paragraph_content: checked })}
                />
                <Label className="cursor-pointer">在知识图谱中加载段落完整内容</Label>
              </div>
              <p className="text-xs text-muted-foreground">
                启用后，知识图谱可视化界面会显示段落节点的完整内容。需要加载 embedding store，会占用额外内存（约数百MB）。
              </p>
            </div>
          </>
        )}
      </div>

      {/* 关闭WebUI警告对话框 */}
      <AlertDialog open={showDisableWarning} onOpenChange={setShowDisableWarning}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>警告：即将关闭 WebUI</AlertDialogTitle>
            <AlertDialogDescription>
              关闭 WebUI 后，在您下次重启麦麦之前，WebUI 界面将无法访问。
              <br />
              <br />
              您需要通过修改配置文件或命令行重新启用 WebUI 才能再次访问此界面。
              <br />
              <br />
              确定要关闭 WebUI 吗？
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={confirmDisableWebUI}>
              确认关闭
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
})
