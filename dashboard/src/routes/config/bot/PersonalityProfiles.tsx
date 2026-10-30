import { useEffect, useMemo, useState } from 'react'
import { Download, Plus, Wrench } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { useToast } from '@/hooks/use-toast'
import {
  clearPersonalityImport, parsePersonalityImport, PERSONALITY_IMPORT_EVENT, PERSONALITY_IMPORT_EXAMPLE,
  readPersonalityImport, type PersonalityProfile as Profile,
} from '@/lib/personality-import'

function snapshot(values: Record<string, unknown>, name: string): Profile {
  return {
    name,
    personality: values.personality as string,
    behavior_style: values.behavior_style as string,
    reply_style: values.reply_style as string,
    group_chat_prompt: values.group_chat_prompt as string,
    private_chat_prompts: values.private_chat_prompts as string,
    multiple_reply_style: values.multiple_reply_style as string[],
    multiple_probability: values.multiple_probability as number,
  }
}

export function PersonalityProfiles({ values, onChange }: {
  values: Record<string, unknown>
  onChange: (values: Record<string, unknown>) => void
}) {
  const [panel, setPanel] = useState<'create' | 'manage' | 'import' | null>(() => readPersonalityImport() !== null ? 'import' : null)
  const [importText, setImportText] = useState(() => readPersonalityImport() ?? '')
  const [draft, setDraft] = useState<Profile | null>(null)
  const [originalName, setOriginalName] = useState<string | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const { toast } = useToast()
  const profiles = (values.profiles ?? []) as Profile[]
  const selected = profiles.find((profile) => profile.name === values.selected_profile)

  const imported = useMemo(() => {
    if (!importText.trim()) return { profile: null, error: '' }
    try { return { profile: parsePersonalityImport(importText), error: '' } }
    catch (error) { return { profile: null, error: (error as Error).message } }
  }, [importText])

  useEffect(() => {
    const receive = () => {
      setImportText(readPersonalityImport() ?? '')
      setDraft(null)
      setOriginalName(null)
      setConfirmDelete(false)
      setPanel('import')
    }
    window.addEventListener(PERSONALITY_IMPORT_EVENT, receive)
    return () => window.removeEventListener(PERSONALITY_IMPORT_EVENT, receive)
  }, [])

  const closePanel = () => {
    clearPersonalityImport()
    setPanel(null)
  }

  const editProfile = (profile: Profile) => {
    // 当前人设可能在主表单中有新编辑，管理卡片应展示这份最新内容。
    setDraft(profile.name === selected?.name ? snapshot(values, profile.name) : { ...profile })
    setOriginalName(profile.name)
    setConfirmDelete(false)
  }

  const createProfile = () => {
    setDraft(snapshot(values, ''))
    setOriginalName(null)
    setConfirmDelete(false)
    setPanel('create')
  }

  const switchTo = (nextName: string) => {
    const target = profiles.find((profile) => profile.name === nextName)
    if (!target || nextName === selected?.name) return
    // 切换前把当前编辑写回原人设；未入库的内容自动留一份，避免第一次切换丢失。
    let nextProfiles: Profile[]
    if (selected) {
      nextProfiles = profiles.map((profile) => profile.name === selected.name ? snapshot(values, selected.name) : profile)
    } else {
      let backupName = '切换前人设'
      let suffix = 2
      while (profiles.some((profile) => profile.name === backupName)) backupName = '切换前人设 ' + suffix++
      nextProfiles = [...profiles, snapshot(values, backupName)]
    }
    const { name: targetName, ...settings } = target
    onChange({ ...values, ...settings, profiles: nextProfiles, selected_profile: targetName })
  }

  const saveProfile = () => {
    if (!draft) return
    const name = draft.name.trim()
    if (!name || profiles.some((profile) => profile.name === name && profile.name !== originalName)) {
      toast({ title: '请输入未使用的人设名称', variant: 'destructive' })
      return
    }
    if (!Number.isFinite(draft.multiple_probability) || draft.multiple_probability < 0 || draft.multiple_probability > 1) {
      toast({ title: '备用表达风格概率应在 0 到 1 之间', variant: 'destructive' })
      return
    }
    const saved = { ...draft, name, multiple_reply_style: draft.multiple_reply_style.filter((style) => style.trim()) }
    const nextProfiles = originalName === null
      ? [...profiles, saved]
      : profiles.map((profile) => profile.name === originalName ? saved : profile)
    const { name: savedName, ...settings } = saved
    // 修改正在使用的人设时同步主表单；编辑其他备选人设不改变当前生效内容。
    onChange(originalName !== null && originalName === selected?.name
      ? { ...values, ...settings, profiles: nextProfiles, selected_profile: savedName }
      : { ...values, profiles: nextProfiles })
    closePanel()
  }

  const deleteProfile = () => {
    onChange({
      ...values,
      profiles: profiles.filter((profile) => profile.name !== originalName),
      selected_profile: originalName === selected?.name ? '' : values.selected_profile,
    })
    setDraft(null)
    setOriginalName(null)
    setConfirmDelete(false)
  }

  return (
    <>
      <div className="flex min-w-0 items-center gap-2">
        <Label htmlFor="personality-profile" className="shrink-0">当前人设</Label>
        <Select value={selected?.name ?? ''} onValueChange={switchTo}>
          <SelectTrigger id="personality-profile" className="min-w-0 flex-1">
            <SelectValue placeholder="当前人设（未命名）" />
          </SelectTrigger>
          <SelectContent>
            {profiles.length === 0 && <div className="px-2 py-3 text-sm text-muted-foreground">暂无备选人设，点击“新建”添加</div>}
            {profiles.map((profile) => <SelectItem key={profile.name} value={profile.name}>{profile.name}</SelectItem>)}
          </SelectContent>
        </Select>
        <Button type="button" size="sm" variant="outline" className="shrink-0" onClick={() => {
          setDraft(null); setOriginalName(null); setConfirmDelete(false); setImportText(''); setPanel('import')
        }}><Download className="mr-1 h-4 w-4" />导入</Button>
        <Button type="button" size="sm" className="shrink-0" onClick={createProfile}><Plus className="mr-1 h-4 w-4" />新建</Button>
        <Button type="button" size="icon" variant="outline" className="shrink-0" aria-label="管理人设" title="管理人设" onClick={() => {
          const first = selected ?? profiles[0]
          if (first) editProfile(first)
          else { setDraft(null); setOriginalName(null); setConfirmDelete(false) }
          setPanel('manage')
        }}><Wrench className="h-4 w-4" /></Button>
      </div>

      <Dialog open={panel !== null} onOpenChange={(open) => { if (!open) closePanel() }}>
        <DialogContent className="[--dialog-width:44rem]">
          <DialogHeader>
            <DialogTitle>{panel === 'import' ? '导入人设' : panel === 'create' ? '新建人设' : '管理人设'}</DialogTitle>
            <DialogDescription>{panel === 'import' ? '粘贴 maibot.personality v1 JSON，解析后可继续编辑并确认保存。' : panel === 'create' ? '编辑完成后保存到人设库，可在下拉框中切换。' : '选择人设查看或修改，保存当前使用的人设会同步生效。'}</DialogDescription>
          </DialogHeader>
          <DialogBody>
            <div className="space-y-4">
              {panel === 'import' && <div className="space-y-3">
                <div className="flex items-center justify-between gap-2">
                  <Label htmlFor="personality-import-text">人设 JSON</Label>
                  <Button type="button" size="sm" variant="outline" onClick={() => setImportText(PERSONALITY_IMPORT_EXAMPLE)}>填入格式示例</Button>
                </div>
                <Textarea id="personality-import-text" rows={12} value={importText} onChange={(event) => setImportText(event.target.value)} placeholder={PERSONALITY_IMPORT_EXAMPLE} />
                {imported.error && <p role="alert" className="text-sm text-destructive">{imported.error}</p>}
                {imported.profile && <div className="space-y-2 rounded-md border p-3">
                  <p className="font-medium">已解析：{imported.profile.name}</p>
                  <p className="whitespace-pre-wrap break-words text-sm text-muted-foreground">{imported.profile.personality}</p>
                  {profiles.some((profile) => profile.name === imported.profile?.name) && <p className="text-sm text-destructive">已有同名人设，请在下一步修改名称。</p>}
                </div>}
              </div>}
              {panel === 'manage' && <div className="flex flex-wrap gap-2">
                {profiles.map((profile) => <Button type="button" key={profile.name} size="sm" variant={originalName === profile.name ? 'default' : 'outline'} onClick={() => editProfile(profile)}>
                  {profile.name}{selected?.name === profile.name ? ' · 使用中' : ''}
                </Button>)}
                <Button type="button" size="sm" variant="outline" onClick={createProfile}><Plus className="mr-1 h-4 w-4" />新建</Button>
              </div>}
              {!draft && panel !== 'import' && <p className="py-6 text-center text-sm text-muted-foreground">暂无选中的人设，选择已有的人设或新建一套。</p>}
              {draft && <>
                <div className="space-y-2">
                  <Label htmlFor="profile-name">人设名称</Label>
                  <Input id="profile-name" placeholder="为这套人设起个名字" value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} />
                </div>
                {([
                  ['personality', '人格设定'], ['behavior_style', '行为风格'], ['reply_style', '表达风格'],
                  ['group_chat_prompt', '群聊提示词'], ['private_chat_prompts', '私聊提示词'],
                ] as const).map(([field, label]) => <div key={field} className="space-y-2">
                  <Label htmlFor={'profile-' + field}>{label}</Label>
                  <Textarea id={'profile-' + field} rows={3} value={draft[field]} onChange={(event) => setDraft({ ...draft, [field]: event.target.value })} />
                </div>)}
                <div className="space-y-2">
                  <Label htmlFor="profile-alternate">备用表达风格（每行一条）</Label>
                  <Textarea id="profile-alternate" rows={3} value={draft.multiple_reply_style.join('\n')} onChange={(event) => setDraft({ ...draft, multiple_reply_style: event.target.value.split('\n') })} />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="profile-probability">备用表达风格触发概率</Label>
                  <Input id="profile-probability" type="number" min={0} max={1} step={0.1} value={Number.isNaN(draft.multiple_probability) ? '' : draft.multiple_probability} onChange={(event) => setDraft({ ...draft, multiple_probability: event.target.valueAsNumber })} />
                </div>
              </>}
            </div>
          </DialogBody>
          {confirmDelete ? <div className="flex flex-wrap items-center justify-end gap-2">
            <p className="mr-auto text-sm">删除“{originalName}”？当前生效的设定会保留。</p>
            <Button type="button" variant="outline" onClick={() => setConfirmDelete(false)}>取消</Button>
            <Button type="button" variant="destructive" onClick={deleteProfile}>确认删除</Button>
          </div> : <div className="flex items-center justify-end gap-2">
            {originalName !== null && <Button type="button" variant="destructive" className="mr-auto" onClick={() => setConfirmDelete(true)}>删除人设</Button>}
            <Button type="button" variant="outline" onClick={closePanel}>取消</Button>
            {panel === 'import' && <Button type="button" disabled={!imported.profile} onClick={() => {
              if (!imported.profile) return
              setDraft(imported.profile); setOriginalName(null); setConfirmDelete(false); setPanel('create')
            }}>继续编辑</Button>}
            {draft && <Button type="button" onClick={saveProfile}>{panel === 'create' ? '创建人设' : '保存修改'}</Button>}
          </div>}
        </DialogContent>
      </Dialog>
    </>
  )
}
