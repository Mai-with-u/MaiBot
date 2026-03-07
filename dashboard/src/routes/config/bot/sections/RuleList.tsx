
import { Button } from '@/components/ui/button'

import { Plus } from 'lucide-react'

import { RuleEditor } from './RuleEditor'

interface TalkValueRule {
  target: string
  time: string
  value: number
}

interface RuleListProps {
  rules: TalkValueRule[]
  onAdd: () => void
  onUpdate: (index: number, field: 'target' | 'time' | 'value', value: string | number) => void
  onRemove: (index: number) => void
}

// 规则列表组件
export function RuleList({ rules, onAdd, onUpdate, onRemove }: RuleListProps) {
  return (
    <div className="border-t pt-6">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h4 className="text-base font-semibold">动态发言频率规则</h4>
          <p className="text-xs text-muted-foreground mt-1">
            按时段或聊天流ID调整发言频率,优先匹配具体聊天,再匹配全局规则
          </p>
        </div>
        <Button onClick={onAdd} size="sm">
          <Plus className="h-4 w-4 mr-1" />
          添加规则
        </Button>
      </div>

      {rules && rules.length > 0 ? (
        <div className="space-y-4">
          {rules.map((rule, index) => (
            <RuleEditor
              key={index}
              rule={rule}
              index={index}
              onUpdate={onUpdate}
              onRemove={onRemove}
            />
          ))}
        </div>
      ) : (
        <div className="text-center py-8 text-muted-foreground">
          <p className="text-sm">暂无规则，点击"添加规则"按钮创建</p>
        </div>
      )}

      <div className="mt-4 p-4 bg-blue-50 dark:bg-blue-950/20 border border-blue-200 dark:border-blue-800 rounded-lg">
        <h5 className="text-sm font-semibold text-blue-900 dark:text-blue-100 mb-2">
          📝 规则说明
        </h5>
        <ul className="text-xs text-blue-800 dark:text-blue-200 space-y-1">
          <li>• <strong>Target 为空</strong>：全局规则，对所有聊天生效</li>
          <li>• <strong>Target 指定</strong>：仅对特定聊天流生效（格式：platform:id:type）</li>
          <li>• <strong>优先级</strong>：先匹配具体聊天流规则，再匹配全局规则</li>
          <li>• <strong>时间支持跨夜</strong>：例如 23:00-02:00 表示晚上11点到次日凌晨2点</li>
          <li>• <strong>数值范围</strong>：建议 0-1，0 表示完全沉默，1 表示正常发言</li>
        </ul>
      </div>
    </div>
  )
}
