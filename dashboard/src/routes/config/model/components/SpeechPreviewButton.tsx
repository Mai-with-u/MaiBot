import { useEffect, useRef, useState } from 'react'
import { Loader2, Square, Volume2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { previewSpeech } from '@/lib/config-api'

interface Props {
  model: string
  voice: string
  provider: string
  draftKey?: string
}

export function SpeechPreviewButton({ model, voice, provider, draftKey }: Props) {
  const [status, setStatus] = useState<'idle' | 'loading' | 'playing'>('idle')
  const [error, setError] = useState('')
  const audio = useRef<HTMLAudioElement>(null)
  const request = useRef<AbortController | null>(null)
  const source = useRef('')

  useEffect(() => {
    const player = audio.current
    setStatus('idle')
    setError('')
    return () => {
      request.current?.abort()
      request.current = null
      if (source.current) {
        player?.pause()
        player?.removeAttribute('src')
        URL.revokeObjectURL(source.current)
        source.current = ''
      }
    }
  }, [model, voice, provider, draftKey])

  async function play() {
    const player = audio.current
    if (!player || request.current) return
    if (status === 'playing') {
      player.pause()
      setStatus('idle')
      return
    }
    const controller = new AbortController()
    request.current = controller
    setStatus('loading')
    setError('')
    try {
      if (!source.current) {
        const connection = draftKey === undefined ? { api_provider: provider } : { draft_api_key: draftKey }
        const blob = await previewSpeech(model, voice, connection, controller.signal)
        if (controller.signal.aborted) return
        source.current = URL.createObjectURL(blob)
        player.src = source.current
      }
      player.currentTime = 0
      await player.play()
      if (!controller.signal.aborted) setStatus('playing')
    } catch (cause) {
      if (controller.signal.aborted) return
      setStatus('idle')
      setError(cause instanceof Error && cause.name === 'NotAllowedError'
        ? '音频已就绪，请再点一次试听。' : cause instanceof Error ? cause.message : '语音试听失败')
    } finally {
      if (request.current === controller) request.current = null
    }
  }

  const Icon = status === 'loading' ? Loader2 : status === 'playing' ? Square : Volume2
  return (
    <div className="shrink-0">
      <Button type="button" variant="outline" size="sm" onClick={() => void play()}
        disabled={!model || !(draftKey === undefined ? provider : draftKey.trim()) || status === 'loading'}>
        <Icon className={status === 'loading' ? 'animate-spin' : ''} />
        {status === 'loading' ? '生成中' : status === 'playing' ? '停止' : '试听'}
      </Button>
      <audio ref={audio} onEnded={() => setStatus('idle')}>
        <track kind="captions" />
      </audio>
      {error && <p role="alert" className="mt-1 max-w-52 text-xs text-destructive">{error}</p>}
    </div>
  )
}
