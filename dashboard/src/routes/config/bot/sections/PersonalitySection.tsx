import React from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
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
import type { PersonalityConfig } from '../types'

interface PersonalitySectionProps {
  config: PersonalityConfig
  onChange: (config: PersonalityConfig) => void
}

export const PersonalitySection = React.memo(function PersonalitySection({ config, onChange }: PersonalitySectionProps) {
  const addState = () => {
    onChange({ ...config, states: [...config.states, ''] })
  }

  const removeState = (index: number) => {
    onChange({
      ...config,
      states: config.states.filter((_, i) => i !== index),
    })
  }

  const updateState = (index: number, value: string) => {
    const newStates = [...config.states]
    newStates[index] = value
    onChange({ ...config, states: newStates })
  }

  return (
    <div className="rounded-lg border bg-card p-4 sm:p-6 space-y-6">
      <div>
        <h3 className="text-lg font-semibold mb-4">人格设置</h3>

        <div className="grid gap-4">
          <div className="grid gap-2">
            <Label htmlFor="personality">人格特质</Label>
            <Textarea
              id="personality"
              value={config.personality}
              onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => onChange({ ...config, personality: e.target.value })}
              placeholder="描述人格特质和身份特征（建议120字以内）"
              rows={3}
            />
            <p className="text-xs text-muted-foreground">
              建议120字以内，描述人格特质和身份特征
            </p>
          </div>

          {/* 多重人格配置 - 移到人格特质下方 */}
          <div className="grid gap-2">
            <div className="flex items-center justify-between">
              <Label>多重人格</Label>
              <Button onClick={addState} size="sm" variant="outline">
                <Plus className="h-4 w-4 mr-1" />
                添加人格
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">
              可以定义多个不同的人格状态，麦麦会随机切换
            </p>
            <div className="space-y-2">
              {config.states.map((state, index) => (
                <div key={index} className="flex gap-2">
                  <Textarea
                    value={state}
                    onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => updateState(index, e.target.value)}
                    placeholder="描述一个人格状态"
                    rows={2}
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
                          确定要删除这个人格状态吗？此操作无法撤销。
                        </AlertDialogDescription>
                      </AlertDialogHeader>
                      <AlertDialogFooter>
                        <AlertDialogCancel>取消</AlertDialogCancel>
                        <AlertDialogAction onClick={() => removeState(index)}>
                          删除
                        </AlertDialogAction>
                      </AlertDialogFooter>
                    </AlertDialogContent>
                  </AlertDialog>
                </div>
              ))}
            </div>
          </div>

          <div className="grid gap-2">
            <Label htmlFor="state_probability">替换为多重人格概率</Label>
            <Input
              id="state_probability"
              type="number"
              step="0.1"
              min="0"
              max="1"
              value={config.state_probability}
              onChange={(e) =>
                onChange({ ...config, state_probability: parseFloat(e.target.value) })
              }
            />
            <p className="text-xs text-muted-foreground">
              每次构建人格时，用多重人格替换主人格的概率（0.0-1.0）
            </p>
          </div>

          <div className="grid gap-2">
            <Label htmlFor="reply_style">表达风格</Label>
            <Textarea
              id="reply_style"
              value={config.reply_style}
              onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => onChange({ ...config, reply_style: e.target.value })}
              placeholder="描述说话的表达风格和习惯"
              rows={3}
            />
          </div>

          <div className="grid gap-2">
            <Label htmlFor="plan_style">说话规则与行为风格</Label>
            <Textarea
              id="plan_style"
              value={config.plan_style}
              onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => onChange({ ...config, plan_style: e.target.value })}
              placeholder="麦麦的说话规则和行为风格"
              rows={5}
            />
          </div>

          <div className="grid gap-2">
            <Label htmlFor="visual_style">识图规则</Label>
            <Textarea
              id="visual_style"
              value={config.visual_style}
              onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => onChange({ ...config, visual_style: e.target.value })}
              placeholder="识图时的处理规则"
              rows={3}
            />
          </div>
        </div>
      </div>
    </div>
  )
})
