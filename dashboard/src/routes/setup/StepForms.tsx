// 设置向导各步骤表单组件

import { useState } from 'react'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { Switch } from '@/components/ui/switch'
import { Separator } from '@/components/ui/separator'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { X, ExternalLink, Eye, EyeOff } from 'lucide-react'
import type {
  BotBasicConfig,
  PersonalityConfig,
  EmojiConfig,
  OtherBasicConfig,
  SiliconFlowConfig,
} from './types'

// ====== 步骤1：Bot基础配置 ======
interface BotBasicFormProps {
  config: BotBasicConfig
  onChange: (config: BotBasicConfig) => void
}

export function BotBasicForm({ config, onChange }: BotBasicFormProps) {
  const handleAddAlias = (alias: string) => {
    if (alias.trim() && !config.alias_names.includes(alias.trim())) {
      onChange({
        ...config,
        alias_names: [...config.alias_names, alias.trim()],
      })
    }
  }

  const handleRemoveAlias = (index: number) => {
    onChange({
      ...config,
      alias_names: config.alias_names.filter((_, i) => i !== index),
    })
  }

  return (
    <div className="space-y-6">
      <div className="space-y-3">
        <Label htmlFor="qq_account">QQ账号 *</Label>
        <Input
          id="qq_account"
          type="number"
          placeholder="请输入机器人的QQ账号"
          value={config.qq_account || ''}
          onChange={(e) =>
            onChange({ ...config, qq_account: Number(e.target.value) })
          }
        />
        <p className="text-xs text-muted-foreground">
          机器人登录使用的QQ账号
        </p>
      </div>

      <div className="space-y-3">
        <Label htmlFor="nickname">昵称 *</Label>
        <Input
          id="nickname"
          placeholder="请输入机器人的昵称"
          value={config.nickname}
          onChange={(e) => onChange({ ...config, nickname: e.target.value })}
        />
        <p className="text-xs text-muted-foreground">
          机器人的主要称呼名称
        </p>
      </div>

      <div className="space-y-3">
        <Label>别名</Label>
        <div className="flex flex-wrap gap-2 mb-2">
          {config.alias_names.map((alias, index) => (
            <Badge key={index} variant="secondary" className="gap-1">
              {alias}
              <button
                type="button"
                onClick={() => handleRemoveAlias(index)}
                className="ml-1 hover:text-destructive"
              >
                <X className="h-3 w-3" />
              </button>
            </Badge>
          ))}
        </div>
        <div className="flex gap-2">
          <Input
            id="alias_input"
            placeholder="输入别名后按回车添加"
            onKeyPress={(e) => {
              if (e.key === 'Enter') {
                handleAddAlias((e.target as HTMLInputElement).value)
                ;(e.target as HTMLInputElement).value = ''
              }
            }}
          />
          <Button
            type="button"
            variant="outline"
            onClick={() => {
              const input = document.getElementById(
                'alias_input'
              ) as HTMLInputElement
              if (input) {
                handleAddAlias(input.value)
                input.value = ''
              }
            }}
          >
            添加
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">
          机器人的其他称呼，可以添加多个
        </p>
      </div>
    </div>
  )
}

// ====== 步骤2：人格配置 ======
interface PersonalityFormProps {
  config: PersonalityConfig
  onChange: (config: PersonalityConfig) => void
}

export function PersonalityForm({ config, onChange }: PersonalityFormProps) {
  return (
    <div className="space-y-6">
      <div className="space-y-3">
        <Label htmlFor="personality">人格特征 *</Label>
        <Textarea
          id="personality"
          placeholder="描述机器人的人格特质和身份特征（建议120字以内）"
          value={config.personality}
          onChange={(e) => onChange({ ...config, personality: e.target.value })}
          rows={3}
        />
        <p className="text-xs text-muted-foreground">
          例如：是一个女大学生，现在在读大二，会刷贴吧
        </p>
      </div>

      <div className="space-y-3">
        <Label htmlFor="reply_style">表达风格 *</Label>
        <Textarea
          id="reply_style"
          placeholder="描述机器人说话的表达风格、表达习惯"
          value={config.reply_style}
          onChange={(e) => onChange({ ...config, reply_style: e.target.value })}
          rows={3}
        />
        <p className="text-xs text-muted-foreground">
          例如：回复平淡一些，简短一些，说中文，参考贴吧、知乎和微博的回复风格
        </p>
      </div>

      <div className="space-y-3">
        <Label htmlFor="interest">兴趣 *</Label>
        <Textarea
          id="interest"
          placeholder="描述机器人感兴趣的话题"
          value={config.interest}
          onChange={(e) => onChange({ ...config, interest: e.target.value })}
          rows={2}
        />
        <p className="text-xs text-muted-foreground">
          会影响机器人对什么话题进行回复
        </p>
      </div>

      <Separator />

      <div className="space-y-3">
        <Label htmlFor="plan_style">群聊说话规则 *</Label>
        <Textarea
          id="plan_style"
          placeholder="机器人在群聊中的行为风格和规则"
          value={config.plan_style}
          onChange={(e) => onChange({ ...config, plan_style: e.target.value })}
          rows={4}
        />
        <p className="text-xs text-muted-foreground">
          定义机器人在群聊中如何行动，例如回复频率、条件等
        </p>
      </div>

      <div className="space-y-3">
        <Label htmlFor="private_plan_style">私聊说话规则 *</Label>
        <Textarea
          id="private_plan_style"
          placeholder="机器人在私聊中的行为风格和规则"
          value={config.private_plan_style}
          onChange={(e) =>
            onChange({ ...config, private_plan_style: e.target.value })
          }
          rows={3}
        />
        <p className="text-xs text-muted-foreground">
          定义机器人在私聊中的行为方式
        </p>
      </div>
    </div>
  )
}

// ====== 步骤3：表情包配置 ======
interface EmojiFormProps {
  config: EmojiConfig
  onChange: (config: EmojiConfig) => void
}

export function EmojiForm({ config, onChange }: EmojiFormProps) {
  return (
    <div className="space-y-6">
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <Label htmlFor="emoji_chance">表情包激活概率</Label>
          <span className="text-sm text-muted-foreground">
            {(config.emoji_chance * 100).toFixed(0)}%
          </span>
        </div>
        <Input
          id="emoji_chance"
          type="range"
          min="0"
          max="1"
          step="0.1"
          value={config.emoji_chance}
          onChange={(e) =>
            onChange({ ...config, emoji_chance: Number(e.target.value) })
          }
        />
        <p className="text-xs text-muted-foreground">
          机器人发送表情包的概率
        </p>
      </div>

      <div className="space-y-3">
        <Label htmlFor="max_reg_num">最大表情包数量</Label>
        <Input
          id="max_reg_num"
          type="number"
          min="1"
          max="200"
          value={config.max_reg_num}
          onChange={(e) =>
            onChange({ ...config, max_reg_num: Number(e.target.value) })
          }
        />
        <p className="text-xs text-muted-foreground">
          机器人最多保存的表情包数量
        </p>
      </div>

      <div className="flex items-center justify-between">
        <div className="space-y-1">
          <Label htmlFor="do_replace">达到最大数量时替换</Label>
          <p className="text-xs text-muted-foreground">
            开启后会删除旧表情包，关闭则不再收集新表情包
          </p>
        </div>
        <Switch
          id="do_replace"
          checked={config.do_replace}
          onCheckedChange={(checked) =>
            onChange({ ...config, do_replace: checked })
          }
        />
      </div>

      <div className="space-y-3">
        <Label htmlFor="check_interval">检查间隔（分钟）</Label>
        <Input
          id="check_interval"
          type="number"
          min="1"
          max="120"
          value={config.check_interval}
          onChange={(e) =>
            onChange({ ...config, check_interval: Number(e.target.value) })
          }
        />
        <p className="text-xs text-muted-foreground">
          检查表情包注册、破损、删除的时间间隔
        </p>
      </div>

      <Separator />

      <div className="flex items-center justify-between">
        <div className="space-y-1">
          <Label htmlFor="steal_emoji">偷取表情包</Label>
          <p className="text-xs text-muted-foreground">
            允许机器人将一些表情包据为己有
          </p>
        </div>
        <Switch
          id="steal_emoji"
          checked={config.steal_emoji}
          onCheckedChange={(checked) =>
            onChange({ ...config, steal_emoji: checked })
          }
        />
      </div>

      <div className="flex items-center justify-between">
        <div className="space-y-1">
          <Label htmlFor="content_filtration">启用表情包过滤</Label>
          <p className="text-xs text-muted-foreground">
            只保存符合要求的表情包
          </p>
        </div>
        <Switch
          id="content_filtration"
          checked={config.content_filtration}
          onCheckedChange={(checked) =>
            onChange({ ...config, content_filtration: checked })
          }
        />
      </div>

      {config.content_filtration && (
        <div className="space-y-3">
          <Label htmlFor="filtration_prompt">过滤要求</Label>
          <Input
            id="filtration_prompt"
            placeholder="例如：符合公序良俗"
            value={config.filtration_prompt}
            onChange={(e) =>
              onChange({ ...config, filtration_prompt: e.target.value })
            }
          />
          <p className="text-xs text-muted-foreground">
            描述表情包应该符合的要求
          </p>
        </div>
      )}
    </div>
  )
}

// ====== 步骤4：其他基础配置 ======
interface OtherBasicFormProps {
  config: OtherBasicConfig
  onChange: (config: OtherBasicConfig) => void
}

export function OtherBasicForm({ config, onChange }: OtherBasicFormProps) {
  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div className="space-y-1">
          <Label htmlFor="enable_tool">启用工具系统</Label>
          <p className="text-xs text-muted-foreground">
            允许机器人使用各种工具增强功能
          </p>
        </div>
        <Switch
          id="enable_tool"
          checked={config.enable_tool}
          onCheckedChange={(checked) =>
            onChange({ ...config, enable_tool: checked })
          }
        />
      </div>

      <Separator />

      <div className="flex items-center justify-between">
        <div className="space-y-1">
          <Label htmlFor="all_global">启用全局黑话模式</Label>
          <p className="text-xs text-muted-foreground">
            允许机器人学习和使用群组黑话
          </p>
        </div>
        <Switch
          id="all_global"
          checked={config.all_global}
          onCheckedChange={(checked) =>
            onChange({ ...config, all_global: checked })
          }
        />
      </div>
    </div>
  )
}

// ====== 步骤5：硅基流动API配置 ======
interface SiliconFlowFormProps {
  config: SiliconFlowConfig
  onChange: (config: SiliconFlowConfig) => void
}

export function SiliconFlowForm({ config, onChange }: SiliconFlowFormProps) {
  const [showApiKey, setShowApiKey] = useState(false)

  return (
    <div className="space-y-6">
      <div className="rounded-lg bg-blue-50 dark:bg-blue-950/30 border border-blue-200 dark:border-blue-800 p-4">
        <div className="flex items-start gap-3">
          <div className="mt-0.5">
            <svg className="h-5 w-5 text-blue-600 dark:text-blue-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
          </div>
          <div className="flex-1 text-sm">
            <p className="font-medium text-blue-900 dark:text-blue-100 mb-1">
              关于硅基流动 (SiliconFlow)
            </p>
            <p className="text-blue-700 dark:text-blue-300 mb-2">
              硅基流动提供了完整的模型覆盖，包括 DeepSeek V3、Qwen、视觉模型、语音识别和嵌入模型。
              只需一个 API Key 即可使用麦麦的所有功能！
            </p>
            <a
              href="https://cloud.siliconflow.cn"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-blue-600 dark:text-blue-400 hover:underline font-medium"
            >
              前往硅基流动获取 API Key
              <ExternalLink className="h-3 w-3" />
            </a>
          </div>
        </div>
      </div>

      <div className="space-y-3">
        <Label htmlFor="siliconflow_api_key">SiliconFlow API Key *</Label>
        <div className="relative">
          <Input
            id="siliconflow_api_key"
            type={showApiKey ? 'text' : 'password'}
            placeholder="sk-..."
            value={config.api_key}
            onChange={(e) => onChange({ api_key: e.target.value })}
            className="font-mono pr-10"
          />
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="absolute right-0 top-0 h-full px-3 hover:bg-transparent"
            onClick={() => setShowApiKey(!showApiKey)}
          >
            {showApiKey ? (
              <EyeOff className="h-4 w-4 text-muted-foreground" />
            ) : (
              <Eye className="h-4 w-4 text-muted-foreground" />
            )}
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">
          请输入您的硅基流动 API 密钥。获取后，麦麦将自动配置所有必需的模型。
        </p>
      </div>

      <div className="rounded-lg bg-muted/50 p-4 text-sm space-y-2">
        <p className="font-medium">将自动配置以下模型：</p>
        <ul className="list-disc list-inside space-y-1 text-muted-foreground ml-2">
          <li>DeepSeek V3 - 主要对话和工具模型</li>
          <li>Qwen3 30B - 高频小任务和工具调用</li>
          <li>Qwen3 VL 30B - 图像识别</li>
          <li>SenseVoice - 语音识别</li>
          <li>BGE-M3 - 文本嵌入</li>
          <li>知识库相关模型 (LPMM)</li>
        </ul>
      </div>

      <div className="rounded-lg border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/30 p-4">
        <p className="text-sm text-amber-900 dark:text-amber-100">
          <span className="font-medium">💡 提示：</span>
          完成向导后，您可以在"系统设置 → 模型配置"中添加更多 API 提供商和模型。
        </p>
      </div>
    </div>
  )
}

