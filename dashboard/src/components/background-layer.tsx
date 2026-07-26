import { useEffect, useRef, useState } from 'react'

import { useAssetStore } from '@/lib/asset-store-context'
import type { BackgroundConfig } from '@/lib/theme/tokens'

type BackgroundLayerProps = {
  config: BackgroundConfig
  layerId: string
}

type ResolvedAssetUrl = {
  assetId: string
  url?: string
}

function getAutoOverlayOpacity(layerId: string): number {
  switch (layerId) {
    case 'page':
      return 0.62
    case 'header':
      return 0.72
    case 'sidebar':
      return 0.78
    case 'card':
      return 0.82
    case 'dialog':
      return 0.88
    default:
      return 0.68
  }
}

function getAutoGradientOverlay(layerId: string): string | undefined {
  if (layerId !== 'page') {
    return undefined
  }

  return 'linear-gradient(to bottom, hsl(var(--background) / 0.82), hsl(var(--background) / 0.52) 28%, hsl(var(--background) / 0.7) 100%)'
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
  const [resolvedAssetUrl, setResolvedAssetUrl] = useState<ResolvedAssetUrl | undefined>()
  const videoRef = useRef<HTMLVideoElement>(null)

  useEffect(() => {
    const assetId = config.assetId
    if (!assetId) return

    let cancelled = false
    getAssetUrl(assetId).then((url) => {
      if (!cancelled) {
        setResolvedAssetUrl({ assetId, url })
      }
    })

    return () => {
      cancelled = true
    }
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

  const blobUrl =
    resolvedAssetUrl && resolvedAssetUrl.assetId === config.assetId ? resolvedAssetUrl.url : undefined
  const filterString = buildFilterString(config.effects)
  const { overlayColor, overlayOpacity, gradientOverlay } = config.effects
  const hasMediaAsset = (config.type === 'image' || config.type === 'video') && Boolean(blobUrl)
  const hasExplicitOverlay = overlayOpacity > 0
  const effectiveOverlayOpacity = hasExplicitOverlay
    ? overlayOpacity
    : hasMediaAsset
      ? getAutoOverlayOpacity(layerId)
      : 0
  const effectiveOverlayColor = hasExplicitOverlay
    ? `hsl(${overlayColor} / ${effectiveOverlayOpacity})`
    : `hsl(var(--background) / ${effectiveOverlayOpacity})`
  const effectiveGradientOverlay =
    gradientOverlay || (hasMediaAsset ? getAutoGradientOverlay(layerId) : undefined)

  return (
    <div
      key={layerId}
      data-background-layer={layerId}
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

      {effectiveOverlayOpacity > 0 && (
        <div
          style={{
            position: 'absolute',
            inset: 0,
            zIndex: 1,
            backgroundColor: effectiveOverlayColor,
            pointerEvents: 'none',
          }}
        />
      )}

      {effectiveGradientOverlay && (
        <div
          style={{
            position: 'absolute',
            inset: 0,
            zIndex: 2,
            background: effectiveGradientOverlay,
            pointerEvents: 'none',
          }}
        />
      )}
    </div>
  )
}
