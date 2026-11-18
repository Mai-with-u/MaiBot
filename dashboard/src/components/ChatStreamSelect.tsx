import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { MultiSelect } from '@/components/ui/multi-select'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { getAllChatStreams } from '@/lib/chat-management-api'

interface ChatStreamSelectProps {
  value: string[]
  onChange: (value: string[]) => void
  multiple?: boolean
  disabled?: boolean
  placeholder?: string
}

/** 显示真实会话名称，配置中只存储 Host 已有的 session_id。 */
export function ChatStreamSelect({ value, onChange, multiple = true, disabled, placeholder }: ChatStreamSelectProps) {
  const [manualId, setManualId] = useState('')
  const [inputError, setInputError] = useState('')
  const streams = useQuery({
    queryKey: ['chat-streams', 'all'],
    queryFn: getAllChatStreams,
    staleTime: 60_000,
  })
  const options = (streams.data ?? []).map(stream => ({
    value: stream.session_id,
    label: [stream.display_name, stream.platform, stream.target_id, stream.chat_type === 'group' ? '群聊' : '私聊', stream.account_id, stream.scope].filter(Boolean).join(' · '),
  }))

  function addManualId() {
    const input = manualId.trim()
    if (!input || disabled) return
    setInputError('')
    let id = input
    // 支持 platform:id:type、逗号分隔和空格分隔；始终解析已有真实会话。
    if (/[:：,，\s]/u.test(input)) {
      const parts = input.split(/[:：,，\s]+/u)
      if (parts.length !== 3 || !['group', 'private'].includes(parts[2])) {
        setInputError('请输入平台、目标 ID、类型，例如 qq:123456:group；类型为 group 或 private。')
        return
      }
      if (!streams.data || streams.isError) {
        setInputError('请等待聊天流列表加载成功后再解析。')
        return
      }
      const [platform, targetId, type] = parts
      const matches = streams.data.filter(stream =>
        stream.platform === platform && stream.target_id === targetId && stream.chat_type === type
      )
      if (matches.length !== 1) {
        setInputError(matches.length ? '匹配到多个账号的聊天流，请在下拉菜单中选择具体会话。' : '未找到该聊天流，请检查平台、目标 ID 和类型。')
        return
      }
      id = matches[0].session_id
    }
    onChange(multiple ? Array.from(new Set([...value, id])) : [id])
    setManualId('')
  }

  return (
    <div className="space-y-2">
      <MultiSelect
        options={options}
        selected={value}
        onChange={onChange}
        singleSelect={!multiple}
        disabled={disabled}
        placeholder={placeholder || '搜索并选择聊天流'}
        emptyText={streams.isPending ? '正在加载聊天流…' : '没有匹配的聊天流'}
      />
      <div className="flex gap-2">
        <Input
          aria-label="手动输入聊天流 ID 或 platform:id:type"
          placeholder="qq:123456:group 或聊天流 ID"
          value={manualId}
          disabled={disabled}
          onChange={event => {
            setManualId(event.target.value)
            setInputError('')
          }}
          onKeyDown={event => {
            if (event.key === 'Enter' && !event.nativeEvent.isComposing) {
              event.preventDefault()
              addManualId()
            }
          }}
        />
        <Button type="button" variant="outline" disabled={disabled || !manualId.trim()} onClick={addManualId}>
          {multiple ? '添加' : '使用'}
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">支持 platform:id:type，如 qq:123456:group（群聊）或 qq:123456:private（私聊），也可用空格或逗号分隔。</p>
      {inputError && <p role="alert" className="text-xs text-destructive">{inputError}</p>}
      {streams.isError && (
        <p role="alert" className="text-xs text-destructive">
          聊天流加载失败：{streams.error.message}
          <Button type="button" variant="link" size="sm" onClick={() => void streams.refetch()}>重试</Button>
        </p>
      )}
    </div>
  )
}
