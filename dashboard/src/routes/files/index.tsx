import { useCallback, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import { useFeatureFlags } from '@/hooks/use-feature-flags'
import { Link } from '@tanstack/react-router'

import { Button } from '@/components/ui/button'
import { PageFrame } from '@/components/ui/surface'
import { downloadFile, listFileChats, listFiles, readFileText, removeFile, sendFile, uploadFile } from '@/lib/files-api'
import type { FileChat, FileList, FileText, StoredFile } from '@/lib/files-api'

const bytes = (size: number) => `${(size / 1024 ** 2).toFixed(2)} MiB`

export function FilesPage({ headerActions }: { headerActions?: ReactNode }) {
  const { t } = useTranslation()
  const { flags, error: configError } = useFeatureFlags()
  const filesEnabled = flags?.files === true
  const [data, setData] = useState<FileList>()
  const [chats, setChats] = useState<FileChat[]>([])
  const [chat, setChat] = useState('')
  const [offset, setOffset] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [preview, setPreview] = useState<{ file: StoredFile; content: FileText }>()
  const [encoding, setEncoding] = useState('utf-8-sig')
  const refresh = useCallback(async () => {
    if (!filesEnabled) return
    const [listing, sessions] = await Promise.all([listFiles(chat || undefined, offset), listFileChats()])
    setData(listing); setChats(sessions)
  }, [chat, offset, filesEnabled])
  useEffect(() => { refresh().catch(e => setError(String(e))) }, [refresh])
  async function run(action: () => Promise<unknown>) {
    setBusy(true); setError(''); setNotice('')
    try { await action(); await refresh() } catch (e) { setError(String(e)) } finally { setBusy(false) }
  }
  async function showText(file: StoredFile, start = 0) {
    setPreview({ file, content: await readFileText(file.file_ref, start, encoding) })
  }
  const pageHeader = (
    <div className="flex items-center justify-between gap-4">
      <h1 className="min-w-0 text-xl font-semibold">{t('files.title')}</h1>
      {headerActions}
    </div>
  )
  if (!filesEnabled) return <PageFrame className="p-4 space-y-3">
    {pageHeader}
    <p role={configError ? 'alert' : 'status'}>{configError || t(flags ? 'files.disabled' : 'common.loading')}</p>
    {flags && <Link to="/config/bot" className="underline">{t('files.openSettings')}</Link>}
  </PageFrame>
  return <PageFrame className="p-4 space-y-4">
    {pageHeader}
    <p className="text-sm text-muted-foreground">{t('files.description')}</p>
    {data && <p className="text-sm">{t('files.storage')}: {bytes(data.usage.used_bytes)} / {bytes(data.usage.max_bytes)} · {t('files.reserved')}: {bytes(data.usage.reserved_bytes)} · {t('files.maximum')}: {bytes(data.usage.max_file_bytes)}</p>}
    <div className="flex flex-wrap gap-2 items-center">
      <select aria-label={t('files.chat')} className="rounded border bg-background p-2 max-w-full" value={chat}
        onChange={e => { setChat(e.target.value); setOffset(0) }}>
        <option value="">{t('files.allChats')}</option>
        {chats.map(item => <option key={item.session_id} value={item.session_id}>{item.name}</option>)}
      </select>
      <Button disabled={busy} variant="outline" onClick={() => run(refresh)}>{t('files.refresh')}</Button>
      <label className="text-sm">{t('files.encoding')}
        <select className="ml-2 border rounded bg-background p-2" value={encoding} onChange={e => setEncoding(e.target.value)}>
          {['utf-8-sig', 'utf-8', 'utf-16', 'gb18030'].map(value => <option key={value}>{value}</option>)}
        </select>
      </label>
      <label className="text-sm">{t('files.upload')}
        <input className="block max-w-full" type="file" disabled={busy || !chat} onChange={e => {
          const file = e.target.files?.[0]; e.target.value = ''
          if (file) void run(() => uploadFile(file, chat))
        }} />
      </label>
    </div>
    {!chat && <p className="text-sm text-muted-foreground">{t('files.chooseChat')}</p>}
    {error && <p role="alert" className="text-destructive whitespace-pre-wrap">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    <div className="space-y-2">{data?.items.map(file => <article key={file.file_ref} className="rounded border p-3 space-y-2">
      <div className="font-medium break-all">{file.name} <span className="font-normal text-muted-foreground">{bytes(file.size)}</span></div>
      <p className="text-sm text-muted-foreground">{t(`files.state.${file.state}`, { defaultValue: file.state })} · {t(file.origin === 'remote' ? 'files.remote' : 'files.original')} · {file.chats?.map(c => c.name).join('、')}</p>
      {file.error && <p className="text-sm text-destructive">{file.error}</p>}
      <div className="flex gap-2 flex-wrap">
        <Button size="sm" variant="outline" disabled={busy} onClick={() => run(() => downloadFile(file))}>{t('files.download')}</Button>
        {/\.(txt|md|markdown|csv|json)$/i.test(file.name) && <Button size="sm" variant="outline" disabled={busy} onClick={() => run(() => showText(file))}>{t('files.read')}</Button>}
        <select aria-label={t('files.sendTo')} className="border rounded bg-background max-w-full text-sm" disabled={busy} value="" onChange={e => {
          const target = e.target.value
          if (target && window.confirm(t('files.confirmSend', { name: chats.find(c => c.session_id === target)?.name })))
            void run(async () => { const result = await sendFile(file.file_ref, target); setNotice(t(`files.delivery.${result.status}`)) })
        }}>
          <option value="">{t('files.sendTo')}</option>
          {chats.map(c => <option key={c.session_id} value={c.session_id}>{c.name}</option>)}
        </select>
        {file.origin === 'remote' && file.state === 'ready' && <Button size="sm" variant="outline" disabled={busy} onClick={() => run(() => removeFile(file.file_ref, true))}>{t('files.clearCache')}</Button>}
        <Button size="sm" variant="destructive" disabled={busy} onClick={() => {
          if (window.confirm(t('files.confirmDelete', { name: file.name }))) void run(() => removeFile(file.file_ref, false))
        }}>{t('files.delete')}</Button>
      </div>
    </article>)}</div>
    {data?.items.length === 0 && <p>{t('files.empty')}</p>}
    <div className="flex gap-2">
      <Button disabled={busy || offset === 0} variant="outline" onClick={() => setOffset(Math.max(0, offset - 50))}>{t('files.previous')}</Button>
      <Button disabled={busy || (data?.items.length ?? 0) < 50} variant="outline" onClick={() => setOffset(offset + 50)}>{t('files.next')}</Button>
    </div>
    {preview && <section className="rounded border p-3 space-y-2">
      <h2 className="font-semibold break-all">{preview.file.name}</h2>
      <p className="text-sm">{preview.content.offset}–{preview.content.next_offset} / {preview.content.total_characters}</p>
      <Button variant="outline" disabled={busy} onClick={() => run(() => showText(preview.file))}>{t('files.refresh')}</Button>
      <pre className="whitespace-pre-wrap break-all max-h-96 overflow-auto bg-muted p-3 text-sm">{preview.content.text}</pre>
      <Button variant="outline" disabled={busy || preview.content.next_offset >= preview.content.total_characters}
        onClick={() => run(() => showText(preview.file, preview.content.next_offset))}>{t('files.next')}</Button>
      <Button variant="ghost" onClick={() => setPreview(undefined)}>{t('files.close')}</Button>
    </section>}
  </PageFrame>
}
