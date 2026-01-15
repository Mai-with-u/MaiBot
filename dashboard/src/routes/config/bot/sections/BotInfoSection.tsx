import React from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog'
import { Plus, Trash2 } from 'lucide-react'
import type { BotConfig } from '../types'

interface BotInfoSectionProps {
  config: BotConfig
  onChange: (config: BotConfig) => void
}

export const BotInfoSection = React.memo(function BotInfoSection({ config, onChange }: BotInfoSectionProps) {
  // 确保 platforms 和 alias_names 始终是数组
  const platforms = config.platforms || []
  const aliasNames = config.alias_names || []

  const addPlatform = () => {
    onChange({ ...config, platforms: [...platforms, ''] })
  }

  const removePlatform = (index: number) => {
    onChange({
      ...config,
      platforms: platforms.filter((_, i) => i !== index),
    })
  }

  const updatePlatform = (index: number, value: string) => {
    const newPlatforms = [...platforms]
    newPlatforms[index] = value
    onChange({ ...config, platforms: newPlatforms })
  }

  const addAlias = () => {
    onChange({ ...config, alias_names: [...aliasNames, ''] })
  }

  const removeAlias = (index: number) => {
    onChange({
      ...config,
      alias_names: aliasNames.filter((_, i) => i !== index),
    })
  }

  const updateAlias = (index: number, value: string) => {
    const newAliases = [...aliasNames]
    newAliases[index] = value
    onChange({ ...config, alias_names: newAliases })
  }

  return (
    <div className="rounded-lg border bg-card p-4 sm:p-6 space-y-6">
      <div>
        <h3 className="text-lg font-semibold mb-4">基本信息</h3>

        <div className="grid gap-4">
          <div className="grid gap-2">
            <Label htmlFor="platform">平台</Label>
            <Input
              id="platform"
              value={config.platform}
              onChange={(e) => onChange({ ...config, platform: e.target.value })}
              placeholder="qq"
            />
          </div>

          <div className="grid gap-2">
            <Label htmlFor="qq_account">QQ账号</Label>
            <Input
              id="qq_account"
              value={config.qq_account}
              onChange={(e) => onChange({ ...config, qq_account: e.target.value })}
              placeholder="123456789"
            />
          </div>

          <div className="grid gap-2">
            <Label htmlFor="nickname">昵称</Label>
            <Input
              id="nickname"
              value={config.nickname}
              onChange={(e) => onChange({ ...config, nickname: e.target.value })}
              placeholder="麦麦"
            />
          </div>

          <div className="grid gap-2">
            <div className="flex items-center justify-between">
              <Label>别名</Label>
              <Button onClick={addAlias} size="sm" variant="outline">
                <Plus className="h-4 w-4 mr-1" />
                添加
              </Button>
            </div>
            <div className="space-y-2">
              {aliasNames.map((alias, index) => (
                <div key={index} className="flex gap-2">
                  <Input
                    value={alias}
                    onChange={(e) => updateAlias(index, e.target.value)}
                    placeholder="小麦"
                  />
                  <AlertDialog>
                    <AlertDialogTrigger asChild>
                      <Button size="icon" variant="outline">
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </AlertDialogTrigger>
                    <AlertDialogContent>
                      <AlertDialogHeader>
                        <AlertDialogTitle>确认删除</AlertDialogTitle>
                        <AlertDialogDescription>
                          确定要删除别名 "{alias || '(空)'}" 吗？此操作无法撤销。
                        </AlertDialogDescription>
                      </AlertDialogHeader>
                      <AlertDialogFooter>
                        <AlertDialogCancel>取消</AlertDialogCancel>
                        <AlertDialogAction onClick={() => removeAlias(index)}>
                          删除
                        </AlertDialogAction>
                      </AlertDialogFooter>
                    </AlertDialogContent>
                  </AlertDialog>
                </div>
              ))}
              {aliasNames.length === 0 && (
                <p className="text-sm text-muted-foreground">暂无别名</p>
              )}
            </div>
          </div>

          <div className="grid gap-2">
            <div className="flex items-center justify-between">
              <Label>其他平台账号</Label>
              <Button onClick={addPlatform} size="sm" variant="outline">
                <Plus className="h-4 w-4 mr-1" />
                添加
              </Button>
            </div>
            <div className="space-y-2">
              {platforms.map((platform, index) => (
                <div key={index} className="flex gap-2">
                  <Input
                    value={platform}
                    onChange={(e) => updatePlatform(index, e.target.value)}
                    placeholder="wx:114514"
                  />
                  <AlertDialog>
                    <AlertDialogTrigger asChild>
                      <Button size="icon" variant="outline">
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </AlertDialogTrigger>
                    <AlertDialogContent>
                      <AlertDialogHeader>
                        <AlertDialogTitle>确认删除</AlertDialogTitle>
                        <AlertDialogDescription>
                          确定要删除平台账号 "{platform || '(空)'}" 吗？此操作无法撤销。
                        </AlertDialogDescription>
                      </AlertDialogHeader>
                      <AlertDialogFooter>
                        <AlertDialogCancel>取消</AlertDialogCancel>
                        <AlertDialogAction onClick={() => removePlatform(index)}>
                          删除
                        </AlertDialogAction>
                      </AlertDialogFooter>
                    </AlertDialogContent>
                  </AlertDialog>
                </div>
              ))}
              {platforms.length === 0 && (
                <p className="text-sm text-muted-foreground">暂无其他平台账号</p>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
})
