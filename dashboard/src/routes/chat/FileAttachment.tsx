import { Download, FileText } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { useFeatureFlags } from '@/hooks/use-feature-flags'
import { downloadFile } from '@/lib/files-api'

export function FileAttachment({ data }: { data: unknown }) {
  const { t } = useTranslation()
  const { flags } = useFeatureFlags()
  const enabled = flags?.files === true
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const file = typeof data === 'object' && data !== null ? data as Record<string, unknown> : {}
  const name = String(file.name || data || t('files.title'))
  const ref = typeof file.file_ref === 'string' ? file.file_ref : ''
  async function download() {
    if (!enabled) return
    setBusy(true); setError('')
    try { await downloadFile({ file_ref: ref, name }) } catch (e) { setError(String(e)) } finally { setBusy(false) }
  }
  return <span className="inline-flex max-w-full flex-col rounded border p-2">
    <button type="button" className="flex items-center gap-2 text-left" disabled={!enabled || !ref || busy} onClick={download} title={t(enabled ? 'files.download' : 'files.disabled')}>
      <FileText className="size-4 shrink-0" /><span className="break-all">{name}</span>{enabled && ref && <Download className="size-4 shrink-0" />}
    </button>
    {error && <span role="alert" className="text-xs text-destructive">{error}</span>}
  </span>
}
