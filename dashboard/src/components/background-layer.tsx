import { useEffect, useRef, useState } from 'react'

import { useAssetStore } from '@/components/asset-provider'
import type { BackgroundConfig } from '@/lib/theme/tokens'

type BackgroundLayerProps = {
  config: BackgroundConfig
  layerId: string
}

function buildFilterString(effects: BackgroundConfig['effects']): string {
  const parts: string[] = []
  if (effects.blur > 0) parts.push(`blur(${effects.blur}px)`)
  if (effects.brightness !== 100) parts.push(`brightness(${effects.brightness}%)`)
  if (effects.contrast !== 100) parts.push(`contrast(${effects.contrast}%)`)
  if (effects.saturate !== 100) parts.push(`saturate(${effects.saturate}%)`)
  return parts.join(' ')
}

function getBackgroundSize(position: BackgroundConfig['effects']['position']): string {
  switch (position) {
    case 'cover':
      return 'cover'
    case 'contain':
      return 'contain'
    case 'center':
      return 'auto'
    case 'stretch':
      return '100% 100%'
    default:
      return 'cover'
  }
}

function getObjectFit(position: BackgroundConfig['effects']['position']): React.CSSProperties['objectFit'] {
  switch (position) {
    case 'cover':
      return 'cover'
    case 'contain':
      return 'contain'
    case 'center':
      return 'none'
    case 'stretch':
      return 'fill'
    default:
      return 'cover'
  }
}

export function BackgroundLayer({ config, layerId }: BackgroundLayerProps) {
  const { getAssetUrl } = useAssetStore()
  const [blobUrl, setBlobUrl] = useState<string | undefined>()
  const videoRef = useRef<HTMLVideoElement>(null)

  useEffect(() => {
    if (!config.assetId) {
      setBlobUrl(undefined)
      return
    }
    getAssetUrl(config.assetId).then(setBlobUrl)
  }, [config.assetId, getAssetUrl])

  useEffect(() => {
    if (config.type !== 'video' || !videoRef.current) return

    const mq = window.matchMedia('(prefers-reduced-motion: reduce)')
    const apply = () => {
      if (videoRef.current) {
        if (mq.matches) {
          videoRef.current.pause()
        } else {
          videoRef.current.play().catch(() => {})
        }
      }
    }
    apply()
    mq.addEventListener('change', apply)
    return () => mq.removeEventListener('change', apply)
  }, [config.type])

  if (config.type === 'none') {
    return null
  }

  const filterString = buildFilterString(config.effects)
  const { overlayColor, overlayOpacity, gradientOverlay } = config.effects

  return (
    <div
      key={layerId}
      style={{
        position: 'absolute',
        inset: 0,
        zIndex: 0,
        overflow: 'hidden',
        pointerEvents: 'none',
      }}
    >
      {config.type === 'image' && (
        <div
          style={{
            position: 'absolute',
            inset: 0,
            zIndex: 0,
            backgroundImage: blobUrl ? `url(${blobUrl})` : undefined,
            backgroundSize: getBackgroundSize(config.effects.position),
            backgroundPosition: 'center',
            backgroundRepeat: 'no-repeat',
            filter: filterString || undefined,
          }}
        />
      )}

      {config.type === 'video' && blobUrl && (
        <video
          ref={videoRef}
          src={blobUrl}
          autoPlay
          muted
          loop
          playsInline
          style={{
            position: 'absolute',
            inset: 0,
            zIndex: 0,
            width: '100%',
            height: '100%',
            objectFit: getObjectFit(config.effects.position),
            filter: filterString || undefined,
          }}
          onError={() => {
            if (videoRef.current) {
              videoRef.current.pause()
            }
          }}
        />
      )}

      {overlayOpacity > 0 && (
        <div
          style={{
            position: 'absolute',
            inset: 0,
            zIndex: 1,
            backgroundColor: `hsl(${overlayColor} / ${overlayOpacity})`,
            pointerEvents: 'none',
          }}
        />
      )}

      {gradientOverlay && (
        <div
          style={{
            position: 'absolute',
            inset: 0,
            zIndex: 2,
            background: gradientOverlay,
            pointerEvents: 'none',
          }}
        />
      )}
    </div>
  )
}
