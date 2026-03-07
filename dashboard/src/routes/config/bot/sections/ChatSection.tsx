import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'

import type { ChatConfig } from '../types'
import { RuleList } from './RuleList'
interface ChatSectionProps {
  config: ChatConfig
  onChange: (config: ChatConfig) => void
}

export function ChatSection({ config, onChange }: ChatSectionProps) {
  // 添加发言频率规则
  const addTalkValueRule = () => {
    onChange({
      ...config,
      talk_value_rules: [
        ...config.talk_value_rules,
        { target: '', time: '00:00-23:59', value: 1.0 },
      ],
    })
  }

  // 删除发言频率规则
  const removeTalkValueRule = (index: number) => {
    onChange({
      ...config,
      talk_value_rules: config.talk_value_rules.filter((_, i) => i !== index),
    })
  }

  // 更新发言频率规则
  const updateTalkValueRule = (
    index: number,
    field: 'target' | 'time' | 'value',
    value: string | number
  ) => {
    const newRules = [...config.talk_value_rules]
    newRules[index] = {
      ...newRules[index],
      [field]: value,
    }
    onChange({
      ...config,
      talk_value_rules: newRules,
    })
  }

  return (
    <div className="rounded-lg border bg-card p-4 sm:p-6 space-y-6">
      <div>
        <h3 className="text-lg font-semibold mb-4">聊天设置</h3>
        <div className="grid gap-4">
          <div className="grid gap-2">
            <Label htmlFor="talk_value">聊天频率（基础值）</Label>
            <Input
              id="talk_value"
              type="number"
              step="0.1"
              min="0"
              max="1"
              value={config.talk_value}
              onChange={(e) => onChange({ ...config, talk_value: parseFloat(e.target.value) })}
            />
            <p className="text-xs text-muted-foreground">越小越沉默，范围 0-1</p>
          </div>

          <div className="grid gap-2">
            <Label htmlFor="think_mode">思考模式</Label>
            <Select
              value={config.think_mode || 'classic'}
              onValueChange={(value) => onChange({ ...config, think_mode: value as 'classic' | 'deep' | 'dynamic' })}
            >
              <SelectTrigger id="think_mode">
                <SelectValue placeholder="选择思考模式" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="classic">经典模式 - 浅度思考和回复</SelectItem>
                <SelectItem value="deep">深度模式 - 进行深度思考和回复</SelectItem>
                <SelectItem value="dynamic">动态模式 - 自动选择思考深度</SelectItem>
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              控制麦麦的思考深度。经典模式回复快但简单；深度模式更深入但较慢；动态模式根据情况自动选择
            </p>
          </div>

          <div className="flex items-center space-x-2">
            <Switch
              id="mentioned_bot_reply"
              checked={config.mentioned_bot_reply}
              onCheckedChange={(checked) =>
                onChange({ ...config, mentioned_bot_reply: checked })
              }
            />
            <Label htmlFor="mentioned_bot_reply" className="cursor-pointer">
              启用提及必回复
            </Label>
          </div>

          <div className="grid gap-2">
            <Label htmlFor="max_context_size">上下文长度</Label>
            <Input
              id="max_context_size"
              type="number"
              min="1"
              value={config.max_context_size}
              onChange={(e) =>
                onChange({ ...config, max_context_size: parseInt(e.target.value) })
              }
            />
          </div>

          <div className="grid gap-2">
            <Label htmlFor="planner_smooth">规划器平滑</Label>
            <Input
              id="planner_smooth"
              type="number"
              step="1"
              min="0"
              value={config.planner_smooth}
              onChange={(e) =>
                onChange({ ...config, planner_smooth: parseFloat(e.target.value) })
              }
            />
            <p className="text-xs text-muted-foreground">
              增大数值会减小 planner 负荷，推荐 1-5，0 为关闭
            </p>
          </div>

          <div className="grid gap-2">
            <Label htmlFor="plan_reply_log_max_per_chat">每个聊天流最大日志数量</Label>
            <Input
              id="plan_reply_log_max_per_chat"
              type="number"
              step="1"
              min="100"
              value={config.plan_reply_log_max_per_chat ?? 1024}
              onChange={(e) =>
                onChange({ ...config, plan_reply_log_max_per_chat: parseInt(e.target.value) })
              }
            />
            <p className="text-xs text-muted-foreground">
              每个聊天流保存的 Plan/Reply 日志最大数量，超过此数量时会自动删除最老的日志
            </p>
          </div>

          <div className="flex items-center space-x-2">
            <Switch
              id="llm_quote"
              checked={config.llm_quote ?? false}
              onCheckedChange={(checked) =>
                onChange({ ...config, llm_quote: checked })
              }
            />
            <Label htmlFor="llm_quote" className="cursor-pointer">
              启用 LLM 控制引用
            </Label>
          </div>
          <p className="text-xs text-muted-foreground -mt-2 ml-10">
            启用后，LLM 可以决定是否在回复时引用消息
          </p>

          <div className="flex items-center space-x-2">
            <Switch
              id="enable_talk_value_rules"
              checked={config.enable_talk_value_rules}
              onCheckedChange={(checked) =>
                onChange({ ...config, enable_talk_value_rules: checked })
              }
            />
            <Label htmlFor="enable_talk_value_rules" className="cursor-pointer">
              启用动态发言频率规则
            </Label>
          </div>
        </div>
      </div>

      {/* 动态发言频率规则配置 */}
      {config.enable_talk_value_rules && (
        <RuleList
          rules={config.talk_value_rules}
          onAdd={addTalkValueRule}
          onUpdate={updateTalkValueRule}
          onRemove={removeTalkValueRule}
        />
      )}
    </div>
  )
}
