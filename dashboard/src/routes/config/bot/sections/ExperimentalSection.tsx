import React from 'react'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
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
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog'
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover'
import { Plus, Trash2, AlertTriangle, Eye, Code2 } from 'lucide-react'
import type { ExperimentalConfig } from '../types'

interface ChatPromptData {
  platform: string
  id: string
  type: 'group' | 'private'
  prompt: string
}

interface ExperimentalSectionProps {
  config: ExperimentalConfig
  onChange: (config: ExperimentalConfig) => void
}

export const ExperimentalSection = React.memo(function ExperimentalSection({ config, onChange }: ExperimentalSectionProps) {
  // 解析 chat_prompt 字符串为结构化数据
  const parseChatPrompt = (promptStr: string): ChatPromptData => {
    const parts = promptStr.split(':')
    if (parts.length >= 4) {
      const platform = parts[0]
      const id = parts[1]
      const type = parts[2] as 'group' | 'private'
      const prompt = parts.slice(3).join(':') // 处理 prompt 中可能包含的冒号
      return { platform, id, type, prompt }
    }
    return { platform: 'qq', id: '', type: 'group', prompt: '' }
  }

  // 将结构化数据转换为字符串
  const stringifyChatPrompt = (data: ChatPromptData): string => {
    return `${data.platform}:${data.id}:${data.type}:${data.prompt}`
  }

  const addChatPrompt = () => {
    onChange({ ...config, chat_prompts: [...config.chat_prompts, 'qq::group:'] })
  }

  const removeChatPrompt = (index: number) => {
    onChange({
      ...config,
      chat_prompts: config.chat_prompts.filter((_, i) => i !== index),
    })
  }

  const updateChatPrompt = (index: number, data: Partial<ChatPromptData>) => {
    const currentData = parseChatPrompt(config.chat_prompts[index])
    const newData = { ...currentData, ...data }
    const newPrompts = [...config.chat_prompts]
    newPrompts[index] = stringifyChatPrompt(newData)
    onChange({ ...config, chat_prompts: newPrompts })
  }

  // 预览组件
  const ChatPromptPreview = ({ promptStr }: { promptStr: string }) => {
    return (
      <Popover>
        <PopoverTrigger asChild>
          <Button variant="outline" size="sm">
            <Eye className="h-4 w-4 mr-1" />
            预览
          </Button>
        </PopoverTrigger>
        <PopoverContent className="w-80 sm:w-96">
          <div className="space-y-2">
            <h4 className="font-medium text-sm">配置预览</h4>
            <div className="rounded-md bg-muted p-3 font-mono text-xs break-all">
              "{promptStr}"
            </div>
            <p className="text-xs text-muted-foreground">
              这是保存到 bot_config.toml 文件中的格式
            </p>
          </div>
        </PopoverContent>
      </Popover>
    )
  }

  return (
    <div className="rounded-lg border bg-card p-4 sm:p-6 space-y-6">
      <div className="flex items-start gap-3 p-3 rounded-lg bg-orange-500/10 border border-orange-500/20">
        <AlertTriangle className="h-5 w-5 text-orange-500 shrink-0 mt-0.5" />
        <div className="space-y-1">
          <h4 className="font-medium text-orange-500">实验性功能</h4>
          <p className="text-sm text-muted-foreground">
            此部分包含实验性功能，可能不稳定或在未来版本中发生变化。请谨慎使用，并注意不推荐在生产环境中修改私聊规则。
          </p>
        </div>
      </div>

      <div>
        <h3 className="text-lg font-semibold mb-4">实验性设置</h3>

        <div className="grid gap-6">
          <div className="flex items-center space-x-2">
            <Switch
              id="lpmm_memory"
              checked={config.lpmm_memory ?? false}
              onCheckedChange={(checked) =>
                onChange({ ...config, lpmm_memory: checked })
              }
            />
            <Label htmlFor="lpmm_memory" className="cursor-pointer">
              将聊天历史总结导入到 LPMM 知识库
            </Label>
          </div>
          <p className="text-xs text-muted-foreground -mt-4">
            开启后，chat_history_summarizer 总结出的历史记录会同时导入到知识库
          </p>

          <div className="grid gap-2">
            <Label htmlFor="private_plan_style">私聊规则（实验性）</Label>
            <Textarea
              id="private_plan_style"
              value={config.private_plan_style}
              onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => onChange({ ...config, private_plan_style: e.target.value })}
              placeholder="私聊的说话规则和行为风格（不推荐修改）"
              rows={4}
            />
            <p className="text-xs text-muted-foreground">
              ⚠️ 不推荐修改此项，可能会影响私聊对话的稳定性
            </p>
          </div>

          <div className="grid gap-4">
            <div className="flex items-center justify-between">
              <div>
                <Label>特定聊天 Prompt 配置</Label>
                <p className="text-xs text-muted-foreground mt-1">
                  为指定聊天添加额外的 prompt，用于定制特定场景的对话行为
                </p>
              </div>
              <Button onClick={addChatPrompt} size="sm" variant="outline">
                <Plus className="h-4 w-4 mr-1" />
                添加配置
              </Button>
            </div>

            <div className="space-y-4">
              {config.chat_prompts.map((promptStr, index) => {
                const data = parseChatPrompt(promptStr)
                
                return (
                  <div key={index} className="rounded-lg border p-4 space-y-4 bg-card">
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-medium">
                        Prompt 配置 {index + 1}
                      </span>
                      <div className="flex items-center gap-2">
                        <ChatPromptPreview promptStr={promptStr} />
                        <AlertDialog>
                          <AlertDialogTrigger asChild>
                            <Button size="sm" variant="ghost">
                              <Trash2 className="h-4 w-4" />
                            </Button>
                          </AlertDialogTrigger>
                          <AlertDialogContent>
                            <AlertDialogHeader>
                              <AlertDialogTitle>确认删除</AlertDialogTitle>
                              <AlertDialogDescription>
                                确定要删除这个 prompt 配置吗？此操作无法撤销。
                              </AlertDialogDescription>
                            </AlertDialogHeader>
                            <AlertDialogFooter>
                              <AlertDialogCancel>取消</AlertDialogCancel>
                              <AlertDialogAction onClick={() => removeChatPrompt(index)}>
                                删除
                              </AlertDialogAction>
                            </AlertDialogFooter>
                          </AlertDialogContent>
                        </AlertDialog>
                      </div>
                    </div>

                    <div className="grid gap-4">
                      {/* 平台选择 */}
                      <div className="grid gap-2">
                        <Label className="text-xs font-medium">平台</Label>
                        <Select
                          value={data.platform}
                          onValueChange={(value) => updateChatPrompt(index, { platform: value })}
                        >
                          <SelectTrigger>
                            <SelectValue placeholder="选择平台" />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value="qq">QQ</SelectItem>
                            <SelectItem value="wx">微信</SelectItem>
                            <SelectItem value="webui">WebUI</SelectItem>
                          </SelectContent>
                        </Select>
                      </div>

                      {/* ID 输入 */}
                      <div className="grid gap-2">
                        <Label className="text-xs font-medium">
                          {data.type === 'group' ? '群号' : '用户ID'}
                        </Label>
                        <Input
                          value={data.id}
                          onChange={(e) => updateChatPrompt(index, { id: e.target.value })}
                          placeholder={data.type === 'group' ? '输入群号' : '输入用户ID'}
                          className="font-mono"
                        />
                      </div>

                      {/* 类型选择 */}
                      <div className="grid gap-2">
                        <Label className="text-xs font-medium">类型</Label>
                        <Select
                          value={data.type}
                          onValueChange={(value: 'group' | 'private') => updateChatPrompt(index, { type: value })}
                        >
                          <SelectTrigger>
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value="group">群聊 (group)</SelectItem>
                            <SelectItem value="private">私聊 (private)</SelectItem>
                          </SelectContent>
                        </Select>
                      </div>

                      {/* Prompt 内容 */}
                      <div className="grid gap-2">
                        <Label className="text-xs font-medium">Prompt 内容</Label>
                        <Textarea
                          value={data.prompt}
                          onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => updateChatPrompt(index, { prompt: e.target.value })}
                          placeholder="输入额外的 prompt 内容，例如：这是一个摄影群，你精通摄影知识"
                          rows={3}
                        />
                        <p className="text-xs text-muted-foreground">
                          这段文本会作为系统提示添加到该聊天的上下文中
                        </p>
                      </div>

                      {/* 原始格式显示 */}
                      <div className="rounded-md bg-muted/50 p-3">
                        <div className="flex items-center gap-2 mb-2">
                          <Code2 className="h-3 w-3 text-muted-foreground" />
                          <span className="text-xs font-medium text-muted-foreground">原始格式</span>
                        </div>
                        <code className="text-xs font-mono text-muted-foreground break-all">
                          {promptStr || '(未配置)'}
                        </code>
                      </div>
                    </div>
                  </div>
                )
              })}

              {config.chat_prompts.length === 0 && (
                <div className="text-center py-8 text-muted-foreground">
                  <p className="text-sm">暂无特定聊天 prompt 配置</p>
                  <p className="text-xs mt-1">点击上方"添加配置"按钮创建新配置</p>
                </div>
              )}
            </div>

            {/* 使用说明 */}
            <div className="text-xs text-muted-foreground space-y-2 p-4 rounded-lg bg-muted/30 border">
              <p className="font-medium text-foreground">💡 使用说明</p>
              <ul className="list-disc list-inside space-y-1 pl-2">
                <li>为不同的聊天环境配置专属的行为提示</li>
                <li>支持多个平台：QQ、微信、WebUI</li>
                <li>可为群聊或私聊分别配置</li>
                <li>Prompt 会自动注入到该聊天的上下文中</li>
              </ul>
              <p className="font-medium text-foreground mt-3">📝 配置示例</p>
              <ul className="list-disc list-inside space-y-1 pl-2">
                <li>摄影群：<code className="text-xs bg-muted px-1 py-0.5 rounded">这是一个摄影群，你精通摄影知识</code></li>
                <li>二次元群：<code className="text-xs bg-muted px-1 py-0.5 rounded">这是一个二次元交流群</code></li>
                <li>好友私聊：<code className="text-xs bg-muted px-1 py-0.5 rounded">这是你与好朋友的私聊</code></li>
              </ul>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
})
