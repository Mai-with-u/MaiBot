import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { useToast } from '@/hooks/use-toast'
import { updateBotConfigSection } from '@/lib/config-api'

interface Props {
  maxCount: number
  autoRegister: boolean
  replaceWhenFull: boolean
  onSaved: () => Promise<unknown>
}

export function EmojiQuickSettings({ maxCount, autoRegister, replaceWhenFull, onSaved }: Props) {
  const [limit, setLimit] = useState<string>()
  const [register, setRegister] = useState<boolean>()
  const [replace, setReplace] = useState<boolean>()
  const { toast } = useToast()
  const number = limit === undefined ? maxCount : Number(limit)
  const valid = limit !== '' && Number.isSafeInteger(number) && number >= 0
  const patch: { max_reg_num?: number; steal_emoji?: boolean; do_replace?: boolean } = {}
  if (limit !== undefined && number !== maxCount) patch.max_reg_num = number
  if (register !== undefined && register !== autoRegister) patch.steal_emoji = register
  if (replace !== undefined && replace !== replaceWhenFull) patch.do_replace = replace
  const dirty = Object.keys(patch).length > 0
  const save = useMutation({
    mutationFn: () => updateBotConfigSection('emoji', patch),
    onSuccess: async () => {
      await onSaved()
      setLimit(undefined)
      setRegister(undefined)
      setReplace(undefined)
      toast({ title: '表情包设置已保存' })
    },
    meta: { suppressErrorToast: true },
  })

  return (
    <form aria-label="表情包快速设置" className="space-y-2 border-t pt-3" onSubmit={(event) => {
      event.preventDefault()
      if (dirty && valid && !save.isPending) save.mutate()
    }}>
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-xs text-muted-foreground">快速设置</h3>
        {dirty && <Button type="submit" size="xs" variant="outline" disabled={!valid || save.isPending}>
          {save.isPending ? '保存中…' : '保存'}
        </Button>}
      </div>
      <fieldset disabled={save.isPending} className="min-w-0 space-y-2">
        <div className="flex items-center justify-between gap-2">
          <Label htmlFor="emoji-max-registered">最大注册数量</Label>
          <Input id="emoji-max-registered" controlSize="sm" type="number" min={0} step={1}
            className="w-20" value={limit ?? maxCount} onChange={(event) => setLimit(event.target.value)} />
        </div>
        <div className="flex items-center justify-between gap-2">
          <Label htmlFor="emoji-auto-register">自动注册</Label>
          <Switch id="emoji-auto-register" checked={register ?? autoRegister} onCheckedChange={setRegister} />
        </div>
        <div className="flex items-center justify-between gap-2">
          <Label htmlFor="emoji-replace-when-full">满额后替换旧表情</Label>
          <Switch id="emoji-replace-when-full" checked={replace ?? replaceWhenFull} onCheckedChange={setReplace} />
        </div>
      </fieldset>
      {!valid && <p className="text-xs text-destructive">请输入非负整数</p>}
      {save.error && <p role="alert" className="text-xs text-destructive">{save.error.message}</p>}
    </form>
  )
}
