import { useEffect, useId, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Plus, X } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ComboboxInput } from '@/components/ui/combobox-input'
import { backendApi, requireSuccess } from '@/lib/http'

interface PersonOption {
  user_id: string
  display_name: string
  nickname: string | null
}

interface Props {
  label: string
  selected: string[]
  onChange: (users: string[]) => void
}

/** 按平台搜索用户，配置中仍保存 platform:user_id。 */
export function ScopedUserSelect({ label, selected, onChange }: Props) {
  const id = useId()
  const [adding, setAdding] = useState(false)
  const [platform, setPlatform] = useState('')
  const [userId, setUserId] = useState('')
  const [search, setSearch] = useState('')

  useEffect(() => {
    const timer = setTimeout(() => setSearch(userId.trim()), 250)
    return () => clearTimeout(timer)
  }, [userId])

  const platforms = useQuery({
    queryKey: ['permission-user-platforms'],
    queryFn: async () => {
      const result = await backendApi.get<{
        success: boolean
        error?: string
        platforms: Array<{ platform: string }>
      }>('/api/chat/platforms')
      return requireSuccess(result, result.error || '加载平台失败')
    },
    enabled: adding,
    staleTime: 60_000,
  })
  const persons = useQuery({
    queryKey: ['permission-users', platform, search],
    queryFn: async ({ signal }) => {
      const result = await backendApi.get<{
        success: boolean
        error?: string
        persons: PersonOption[]
      }>('/api/chat/persons', {
        query: { platform, search, limit: 50 },
        signal,
      })
      return requireSuccess(result, result.error || '加载用户失败')
    },
    enabled: adding && Boolean(platform.trim()),
    staleTime: 30_000,
  })

  const platformOptions = Array.from(
    new Set([
      'internal',
      ...(platforms.data?.platforms.map((item) => item.platform) ?? []),
      ...selected.map((user) => user.split(':')[0]),
    ])
  ).map((value) => ({ value, description: value === 'internal' ? '内置聊天 / 桌宠' : undefined }))
  const userOptions = new Map<string, { value: string; description: string }>()
  if (platform === 'internal') {
    userOptions.set('owner', { value: 'owner', description: '主人（内置聊天 / 桌宠）' })
  }
  for (const person of persons.data?.persons ?? []) {
    userOptions.set(person.user_id, {
      value: person.user_id,
      description: Array.from(new Set([person.display_name, person.nickname].filter(Boolean))).join(' · '),
    })
  }
  const normalizedPlatform = platform.trim().toLowerCase()
  const normalizedUserId = userId.trim()
  const scopedUser = `${normalizedPlatform}:${normalizedUserId}`
  const duplicate = selected.includes(scopedUser)
  const canAdd =
    Boolean(normalizedPlatform && normalizedUserId) &&
    !/[\s:]/.test(normalizedPlatform) &&
    !/\s/.test(normalizedUserId) &&
    !duplicate

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <div className="text-sm font-medium">{label}</div>
        <Button type="button" variant="outline" size="sm" onClick={() => setAdding(true)}>
          <Plus className="mr-1 h-4 w-4" />
          新增
        </Button>
      </div>
      <div className="flex flex-wrap gap-2">
        {selected.map((user) => (
          <Badge key={user} variant="secondary" className="max-w-full gap-2 py-1">
            <span className="truncate" title={user}>
              {user}
            </span>
            <button
              type="button"
              className="shrink-0 rounded-sm focus-visible:outline-2"
              aria-label={`从${label}移除 ${user}`}
              onClick={() => onChange(selected.filter((item) => item !== user))}
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </Badge>
        ))}
        {selected.length === 0 && <p className="text-muted-foreground text-xs">尚未添加用户</p>}
      </div>
      {adding && (
        <div className="space-y-3 rounded-lg border p-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <label htmlFor={`${id}-platform`} className="text-xs">
                平台
              </label>
              <ComboboxInput
                id={`${id}-platform`}
                value={platform}
                onValueChange={(value) => {
                  setPlatform(value.trim().toLowerCase())
                  setUserId('')
                  setSearch('')
                }}
                options={platformOptions}
                placeholder="搜索或输入平台"
                loading={platforms.isFetching}
                error={platforms.error?.message}
                onRefresh={() => {
                  void platforms.refetch()
                }}
              />
            </div>
            <div className="space-y-1.5">
              <label htmlFor={`${id}-user`} className="text-xs">
                用户
              </label>
              <ComboboxInput
                id={`${id}-user`}
                value={userId}
                onValueChange={setUserId}
                options={Array.from(userOptions.values())}
                placeholder={platform ? '搜索昵称或用户 ID' : '请先选择平台'}
                loading={persons.isFetching}
                error={persons.error?.message}
                emptyText="没有匹配的用户，可直接输入用户 ID"
                onRefresh={
                  platform
                    ? () => {
                        void persons.refetch()
                      }
                    : undefined
                }
              />
            </div>
          </div>
          {duplicate && <p className="text-muted-foreground text-xs">该用户已添加</p>}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" size="sm" onClick={() => setAdding(false)}>
              取消
            </Button>
            <Button
              type="button"
              size="sm"
              disabled={!canAdd}
              onClick={() => {
                onChange([...selected, scopedUser])
                setUserId('')
                setSearch('')
                setAdding(false)
              }}
            >
              添加用户
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}
