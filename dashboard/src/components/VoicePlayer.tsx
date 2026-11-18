import { Download, Ellipsis, Pause, Play } from 'lucide-react'
import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Slider } from '@/components/ui/slider'

function formatTime(seconds: number) {
  return `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`
}

export function VoicePlayer({ src }: { src: string }) {
  const { t } = useTranslation()
  const audioRef = useRef<HTMLAudioElement>(null)
  const [playing, setPlaying] = useState(false)
  const [duration, setDuration] = useState(0)
  const [position, setPosition] = useState(0)
  const [rate, setRate] = useState(1)
  const [failed, setFailed] = useState(false)
  const playLabel = t(playing ? 'chat.media.pauseVoice' : 'chat.media.playVoice')
  const durationLabel = duration ? formatTime(Math.ceil(duration)) : '–:––'

  async function togglePlayback() {
    const audio = audioRef.current
    if (!audio) return
    if (!audio.paused) {
      audio.pause()
      return
    }
    try {
      await audio.play()
    } catch (error) {
      if (!(error instanceof DOMException && error.name === 'AbortError')) setFailed(true)
    }
  }

  return (
    <div className="@container w-80 max-w-full min-w-0">
      {/* eslint-disable-next-line jsx-a11y/media-has-caption -- Chat voice segments carry audio only, without timed captions. */}
      <audio
        ref={audioRef}
        src={src}
        preload="metadata"
        data-chat-voice
        onDurationChange={({ currentTarget }) => {
          setDuration(Number.isFinite(currentTarget.duration) ? currentTarget.duration : 0)
        }}
        onTimeUpdate={({ currentTarget }) => setPosition(currentTarget.currentTime)}
        onRateChange={({ currentTarget }) => setRate(currentTarget.playbackRate)}
        onPlay={({ currentTarget }) => {
          if (currentTarget.paused) return
          for (const other of document.querySelectorAll<HTMLAudioElement>(
            'audio[data-chat-voice]'
          )) {
            if (other !== currentTarget && !other.paused) other.pause()
          }
          setPlaying(true)
          setFailed(false)
        }}
        onPause={() => setPlaying(false)}
        onEnded={() => setPlaying(false)}
        onError={() => {
          setPlaying(false)
          setFailed(true)
        }}
      />
      <div className="flex items-center gap-1.5">
        <Button
          type="button"
          variant="ghost"
          size="icon-xs"
          className="shrink-0"
          aria-label={playLabel}
          title={playLabel}
          onClick={togglePlayback}
        >
          {playing ? <Pause /> : <Play />}
        </Button>
        <Slider
          aria-label={t('chat.media.voiceProgress')}
          aria-valuetext={`${formatTime(position)} / ${durationLabel}`}
          className="h-8 min-w-0 flex-1 [&_[data-dashboard-slider-range]]:bg-current [&_[data-dashboard-slider-thumb]]:size-3 [&_[data-dashboard-slider-thumb]]:bg-current [&_[data-dashboard-slider-track]]:bg-current/25"
          min={0}
          max={duration || 1}
          step={0.1}
          value={[position]}
          disabled={!duration}
          onValueChange={([seconds]) => {
            if (audioRef.current) audioRef.current.currentTime = seconds
            setPosition(seconds)
          }}
        />
        <span className="shrink-0 text-xs whitespace-nowrap tabular-nums">
          <span className="hidden @[220px]:inline">{formatTime(position)} / </span>
          {durationLabel}
        </span>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              type="button"
              variant="ghost"
              size="icon-xs"
              className="shrink-0"
              aria-label={t('chat.media.voiceOptions')}
              title={t('chat.media.voiceOptions')}
            >
              <Ellipsis />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem asChild>
              <a href={src} download>
                <Download className="mr-2 size-4" />
                {t('files.download')}
              </a>
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuLabel>{t('chat.media.playbackSpeed')}</DropdownMenuLabel>
            <DropdownMenuRadioGroup
              value={String(rate)}
              onValueChange={(value) => {
                if (audioRef.current) audioRef.current.playbackRate = Number(value)
              }}
            >
              {[0.5, 0.75, 1, 1.25, 1.5, 2].map((speed) => (
                <DropdownMenuRadioItem key={speed} value={String(speed)}>
                  {speed}×
                </DropdownMenuRadioItem>
              ))}
            </DropdownMenuRadioGroup>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
      {failed && (
        <span role="status" className="text-xs">
          {t('chat.media.voicePlaybackFailed')}
        </span>
      )}
    </div>
  )
}
