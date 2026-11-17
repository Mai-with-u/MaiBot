import { useQuery } from '@tanstack/react-query'
import { Volume2 } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import type { FieldHookComponentProps } from '@/lib/field-hooks'
import { backendApi } from '@/lib/http'

/** 保留配置表单原有下拉框，试听当前选项，不触发桌宠发言。 */
export function DesktopPetSoundField({ value, children }: FieldHookComponentProps) {
  const audio = useRef<HTMLAudioElement | null>(null)
  const [error, setError] = useState('')
  const selected = typeof value === 'string' ? value : 'wood'
  const preview = useQuery({
    queryKey: ['desktop-pet-sound', selected],
    queryFn: ({ signal }) => backendApi.get<{ src: string }>(
      `/api/webui/desktop-pet/sounds/${encodeURIComponent(selected)}`, { signal },
    ),
    enabled: selected !== 'off',
    staleTime: Infinity,
    retry: false,
  })
  useEffect(() => {
    setError('')
    return () => { audio.current?.pause(); audio.current = null }
  }, [selected])

  const play = async () => {
    if (!preview.data) return
    audio.current?.pause()
    const player = new Audio(preview.data.src)
    audio.current = player
    setError('')
    try {
      await player.play()
    } catch (cause) {
      if (audio.current === player) setError(`试听失败：${String(cause)}`)
    }
  }

  return <div className="space-y-1">
    <div className="flex items-end gap-2">
      <div className="min-w-0 flex-1">{children}</div>
      <Button type="button" variant="outline" className="shrink-0"
        disabled={selected === 'off' || !preview.data} onClick={() => void play()}
        aria-label="试听当前消息提示音">
        <Volume2 aria-hidden="true" />试听
      </Button>
    </div>
    {(error || preview.isError) && <p role="alert" className="text-destructive text-xs">
      {error || `加载音效失败：${preview.error?.message}`}
    </p>}
  </div>
}
