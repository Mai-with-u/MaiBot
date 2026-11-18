import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Plus, Trash2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from '@/components/ui/command'
import { Input } from '@/components/ui/input'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import type { FieldHookComponent } from '@/lib/field-hooks'
import { getPersonList } from '@/lib/person-api'

interface BlacklistedUser {
  platform: string
  user_id: string
}

export const LearningBlacklistHook: FieldHookComponent = ({ value, onChange }) => {
  const users = (Array.isArray(value) ? value : []) as BlacklistedUser[]
  const [platform, setPlatform] = useState('')
  const [userId, setUserId] = useState('')
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(1)
  useEffect(() => {
    const timer = setTimeout(() => { setQuery(search.trim()); setPage(1) }, 250)
    return () => clearTimeout(timer)
  }, [search])
  const people = useQuery({
    queryKey: ['learning-blacklist-users', query, page],
    queryFn: () => getPersonList({ search: query, page, page_size: 30 }),
    enabled: open,
  })
  const addUser = (user: BlacklistedUser) => {
    if (!users.some((entry) => entry.platform === user.platform && entry.user_id === user.user_id)) {
      onChange?.([...users, user])
    }
    setOpen(false)
  }
  const valid = /^\S+$/.test(platform.trim()) && /^\S+$/.test(userId.trim())

  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">学习黑名单：来源消息属于名单中的用户时，丢弃该学习结果。仅影响当前类别的后续学习，不删除已有内容，不影响正常聊天。</p>
      <div className="flex flex-wrap items-center gap-2">
        <Input className="w-32" aria-label="黑名单平台" placeholder="平台，如 qq" value={platform} onChange={(event) => setPlatform(event.target.value)} />
        <Input className="w-48" aria-label="黑名单用户 ID" placeholder="用户 ID" value={userId} onChange={(event) => setUserId(event.target.value)} />
        <Button type="button" variant="outline" disabled={!valid} onClick={() => {
          addUser({ platform: platform.trim(), user_id: userId.trim() })
          setUserId('')
        }}><Plus className="mr-1 h-4 w-4" />添加用户</Button>
        <Popover open={open} onOpenChange={setOpen}>
          <PopoverTrigger asChild><Button type="button" variant="outline">搜索选择用户</Button></PopoverTrigger>
          <PopoverContent className="w-80 p-0" align="start">
            <Command shouldFilter={false}>
              <CommandInput placeholder="搜索名称、昵称或用户 ID" value={search} onValueChange={setSearch} />
              <CommandList>
                {people.isFetching ? <p className="p-3 text-sm">加载中…</p> : people.isError ? (
                  <div className="p-3 text-sm text-destructive">{people.error.message}<Button type="button" variant="ghost" onClick={() => void people.refetch()}>重试</Button></div>
                ) : <>
                  <CommandEmpty>没有匹配的用户，可手动填写平台和用户 ID。</CommandEmpty>
                  <CommandGroup>
                    {people.data?.data.map((person) => (
                      <CommandItem key={person.person_id} value={person.person_id} onSelect={() => addUser({ platform: person.platform, user_id: person.user_id })}>
                        <div><div>{person.person_name || person.nickname || person.user_id}</div><div className="text-xs text-muted-foreground">{person.platform} · {person.user_id}{users.some((user) => user.platform === person.platform && user.user_id === person.user_id) ? ' · 已添加' : ''}</div></div>
                      </CommandItem>
                    ))}
                  </CommandGroup>
                </>}
              </CommandList>
            </Command>
            <div className="flex items-center justify-between border-t p-2">
              <Button type="button" size="sm" variant="ghost" disabled={page === 1 || people.isFetching} onClick={() => setPage(page - 1)}>上一页</Button>
              <span className="text-xs">第 {page} 页</span>
              <Button type="button" size="sm" variant="ghost" disabled={people.isFetching || page * 30 >= (people.data?.total ?? 0)} onClick={() => setPage(page + 1)}>下一页</Button>
            </div>
          </PopoverContent>
        </Popover>
      </div>
      {users.length === 0 && <p className="text-sm text-muted-foreground">尚未设置学习黑名单。</p>}
      {users.map((user, index) => (
        <div key={`${user.platform}:${user.user_id}`} className="flex items-center justify-between gap-3 rounded-md border p-3">
          <span>{user.platform} · {user.user_id}</span>
          <Button type="button" size="icon" variant="ghost" aria-label={`移除 ${user.platform}:${user.user_id}`} onClick={() => onChange?.(users.filter((_, itemIndex) => itemIndex !== index))}><Trash2 className="h-4 w-4" /></Button>
        </div>
      ))}
    </div>
  )
}
