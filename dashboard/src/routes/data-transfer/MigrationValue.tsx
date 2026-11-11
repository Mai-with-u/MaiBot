import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { DataPagination } from '@/components/ui/data-pagination'
import type { MigrationConflict } from '@/lib/chat-migration-api'

const labels: Record<string, string> = {
  scope_label: '作用域',
  situation: '适用情景',
  style: '表达方式',
  content: '黑话',
  meaning: '含义',
  content_list: '学习内容',
  evidence_messages: '证据消息',
  count: '使用次数',
  checked: '已审核',
  modified_by: '修改者',
  created_by: '创建来源',
  is_jargon: '确认为黑话',
  is_complete: '推断已完成',
  is_global: '全局共享',
  last_inference_count: '上次推断时的使用次数',
  session_id_dict: '聊天流使用次数',
  platform: '平台',
  item_id: '聊天对象',
  type: '类型',
  rule_type: '聊天类型',
  use: '使用已学内容',
  learn: '继续学习',
  enabled: '启用',
  paused: '暂停回复',
  prompt: '附加提示词',
  time: '生效时间段',
  value: '发言频率',
  group_id: '相通组',
  group_name: '相通组名称',
  session_id: '聊天流',
  command: '命令',
  target: '目标',
  description: '说明',
  name: '名称',
  message_id: '消息编号',
  text: '内容',
  timestamp: '时间',
  last_active_time: '最近使用',
  create_time: '创建时间',
  created_at: '创建时间',
  created_timestamp: '创建时间',
  updated_timestamp: '更新时间',
  updated_at: '更新时间',
  expression: '表达学习',
  jargon: '黑话学习',
  chat: '聊天',
  learning_list: '学习规则',
  talk_rules: '发言频率规则',
  talk_rule_groups: '发言频率分组',
  groups: '规则组',
  rules: '规则',
  extra_prompt: '附加提示词',
  extra_prompts: '附加提示词',
  private_reply_pause_rules: '私聊暂停规则',
  personality: '人格',
}
const enums: Record<string, string> = {
  group: '群聊',
  private: '私聊',
  AI: '自动学习',
  USER: '用户',
  MANUAL: '手动创建',
}

export function conflictTitle(item: MigrationConflict) {
  const kinds: Record<string, string> = {
    expressions: '表达',
    jargons: '黑话',
    learned_commands: '学习命令',
    config: '配置',
  }
  const kind = kinds[item.kind] ?? '配置'
  const title =
    item.kind === 'config'
      ? item.label
          .split('.')
          .map((part) => labels[part] ?? part)
          .join(' · ')
      : item.label
  return `${kind}：${title}`
}

const secondaryFields = new Set([
  'evidence_messages',
  'content_list',
  'session_id_dict',
  'last_inference_count',
  'created_timestamp',
  'updated_timestamp',
  'create_time',
  'created_at',
  'updated_at',
  'last_active_time',
  'modified_by',
  'created_by',
  'is_complete',
  'is_global',
])

function ValueList({
  items,
  fieldLabels,
  chatName,
}: {
  items: unknown[]
  fieldLabels: Record<string, string>
  chatName: (id: string) => string
}) {
  const [page, setPage] = useState(1)
  const current = Math.min(page, Math.max(1, Math.ceil(items.length / 5)))
  if (!items.length) return <span className="text-muted-foreground">无记录</span>
  return (
    <div className="space-y-2">
      <ol start={(current - 1) * 5 + 1} className="space-y-2">
        {items.slice((current - 1) * 5, current * 5).map((entry, index) => (
          <li key={`${current}-${index}`} className="rounded-md border p-3">
            {items.length > 1 && (
              <p className="text-muted-foreground mb-2 text-xs">
                第 {(current - 1) * 5 + index + 1} 条
              </p>
            )}
            <MigrationValue value={entry} labels={fieldLabels} chatName={chatName} />
          </li>
        ))}
      </ol>
      {items.length > 5 && (
        <DataPagination total={items.length} page={current} pageSize={5} onPageChange={setPage} />
      )}
    </div>
  )
}

function LongText({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(false)
  return (
    <div className="space-y-1">
      <p
        className={`break-words whitespace-pre-wrap ${expanded ? 'max-h-80 overflow-y-auto' : 'line-clamp-4'}`}
      >
        {text}
      </p>
      {(text.length > 180 || text.split('\n').length > 4) && (
        <Button
          variant="ghost"
          size="sm"
          aria-expanded={expanded}
          onClick={() => setExpanded(!expanded)}
        >
          {expanded ? '收起全文' : '展开全文'}
        </Button>
      )}
    </div>
  )
}

function DetailField({
  field,
  value,
  fieldLabels,
  chatName,
}: {
  field: string
  value: unknown
  fieldLabels: Record<string, string>
  chatName: (id: string) => string
}) {
  const [open, setOpen] = useState(false)
  let parsed = value
  if (
    typeof value === 'string' &&
    ['evidence_messages', 'content_list', 'session_id_dict'].includes(field)
  ) {
    try {
      parsed = JSON.parse(value)
    } catch {
      /* 历史纯文本保留原文。 */
    }
  }
  const items =
    Array.isArray(parsed) && field === 'evidence_messages' ? parsed.flat(Infinity) : parsed
  const count = Array.isArray(items) ? items.length : undefined
  return (
    <details
      onToggle={(event) => setOpen(event.currentTarget.open)}
      className="rounded-md border px-3 py-2"
    >
      <summary className="cursor-pointer text-sm">
        {fieldLabels[field] ?? labels[field] ?? field}
        {count !== undefined ? ` · ${count} 条` : ''}
      </summary>
      {open && (
        <div className="mt-3 max-h-96 overflow-y-auto pr-1">
          {Array.isArray(items) ? (
            <ValueList items={items} fieldLabels={fieldLabels} chatName={chatName} />
          ) : (
            <MigrationValue value={items} labels={fieldLabels} chatName={chatName} field={field} />
          )}
        </div>
      )}
    </details>
  )
}

export function MigrationValue({
  value,
  labels: fieldLabels = {},
  chatName,
  field = '',
}: {
  value: unknown
  labels?: Record<string, string>
  chatName: (id: string) => string
  field?: string
}) {
  if (
    typeof value === 'string' &&
    ['content_list', 'evidence_messages', 'session_id_dict'].includes(field)
  ) {
    try {
      value = JSON.parse(value)
    } catch {
      /* 旧记录的纯文本直接展示。 */
    }
  }
  if (value == null || value === '') return <span className="text-muted-foreground">未设置</span>
  if (typeof value === 'boolean') return <span>{value ? '是' : '否'}</span>
  if (Array.isArray(value))
    return <ValueList items={value} fieldLabels={fieldLabels} chatName={chatName} />
  if (typeof value === 'object') {
    const entries = Object.entries(value)
    const primary = entries.filter(([key]) => !secondaryFields.has(key))
    const secondary = entries.filter(([key]) => secondaryFields.has(key))
    return (
      <div className="space-y-3">
        <dl className="space-y-3 text-sm">
          {primary.map(([key, entry]) => (
            <div key={key} className="space-y-1">
              <dt className="text-muted-foreground text-xs">
                {field === 'session_id_dict'
                  ? chatName(key)
                  : (fieldLabels[key] ?? labels[key] ?? key.replaceAll('_', ' '))}
              </dt>
              <dd className="min-w-0 break-words">
                <MigrationValue
                  value={entry}
                  labels={fieldLabels}
                  chatName={chatName}
                  field={key}
                />
              </dd>
            </div>
          ))}
        </dl>
        {secondary
          .filter(([key]) => ['evidence_messages', 'content_list'].includes(key))
          .map(([key, entry]) => (
            <DetailField
              key={key}
              field={key}
              value={entry}
              fieldLabels={fieldLabels}
              chatName={chatName}
            />
          ))}
        {secondary.some(([key]) => !['evidence_messages', 'content_list'].includes(key)) && (
          <details className="text-sm">
            <summary className="text-muted-foreground cursor-pointer">使用记录与其他信息</summary>
            <dl className="mt-2 space-y-2">
              {secondary
                .filter(([key]) => !['evidence_messages', 'content_list'].includes(key))
                .map(([key, entry]) => (
                  <div key={key} className="space-y-1">
                    <dt className="text-muted-foreground text-xs">
                      {fieldLabels[key] ?? labels[key]}
                    </dt>
                    <dd>
                      <MigrationValue
                        value={entry}
                        labels={fieldLabels}
                        chatName={chatName}
                        field={key}
                      />
                    </dd>
                  </div>
                ))}
            </dl>
          </details>
        )}
      </div>
    )
  }
  if (typeof value === 'number' && /timestamp|_at$/.test(field))
    return <span>{new Date(value * 1000).toLocaleString()}</span>
  if (field === 'session_id') return <span>{chatName(String(value))}</span>
  if (typeof value === 'string' && /time|_at$/.test(field) && !Number.isNaN(Date.parse(value)))
    return <span>{new Date(value).toLocaleString()}</span>
  if (typeof value === 'string' && (value.length > 180 || value.split('\n').length > 4))
    return <LongText text={value} />
  return (
    <span className="break-words whitespace-pre-wrap">
      {['type', 'rule_type', 'modified_by', 'created_by'].includes(field)
        ? (enums[String(value)] ?? String(value))
        : String(value)}
    </span>
  )
}
