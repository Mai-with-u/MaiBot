import { useEffect, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import { MarkdownRenderer } from '@/components/markdown-renderer'
import { Button } from '@/components/ui/button'
import { AI_HELP_IMAGE_PATH, getAIHelpImage, type AISearchArtifact } from '@/lib/ai-search-api'
import { isElectron } from '@/lib/runtime'

type ArtifactImageSource = { kind: 'local'; path: string } | { kind: 'remote'; url: URL }
type LocalImagePreview = { path: string } & (
  | { src: string }
  | { error: string }
  | { invalid: true }
)

const publicImageHost = /^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,62}$/
const privateImageHost = /\.(?:localhost|local|internal|lan|home\.arpa)$/

function resolveImageSource(content: string): ArtifactImageSource | null {
  if (content.length > 2048 || /[\s\\\p{Cc}]/u.test(content)) return null
  const isLocalPath = content.startsWith(`${AI_HELP_IMAGE_PATH}?`)
  if (!isLocalPath && !/^https:\/\//i.test(content)) return null

  try {
    const url = new URL(content, 'https://webui.invalid')
    if (url.username || url.password) return null

    if (isLocalPath || url.origin === window.location.origin) {
      const entries = [...url.searchParams]
      if (url.pathname !== AI_HELP_IMAGE_PATH || content.includes('#') || entries.length !== 1) {
        return null
      }
      const [[key, value]] = entries
      return key === 'path' && value.trim() && !/\p{Cc}/u.test(value)
        ? { kind: 'local', path: value }
        : null
    }

    return url.protocol === 'https:' &&
      !url.port &&
      publicImageHost.test(url.hostname) &&
      !privateImageHost.test(url.hostname)
      ? { kind: 'remote', url }
      : null
  } catch {
    return null
  }
}

function LocalArtifactImage({ path, title }: { path: string; title: string }) {
  const { t } = useTranslation()
  const [preview, setPreview] = useState<LocalImagePreview>()

  useEffect(() => {
    const controller = new AbortController()
    let objectUrl: string | undefined
    void getAIHelpImage(path, controller.signal)
      .then((blob) => {
        if (controller.signal.aborted) return
        if (!blob.type.startsWith('image/')) {
          setPreview({ path, invalid: true })
          return
        }
        objectUrl = URL.createObjectURL(blob)
        setPreview({ path, src: objectUrl })
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setPreview({ path, error: error instanceof Error ? error.message : String(error) })
        }
      })

    return () => {
      controller.abort()
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [path])

  if (preview?.path !== path) return <p role="status">{t('common.loading')}</p>
  if ('invalid' in preview) return <p role="alert">{t('search.invalidArtifactImage')}</p>
  if ('error' in preview) return <p role="alert">{preview.error}</p>
  return <ArtifactImage src={preview.src} title={title} />
}

function ArtifactImage({ src, title }: { src: string; title: string }) {
  return (
    <img
      src={src}
      alt={title}
      loading="lazy"
      referrerPolicy="no-referrer"
      className="max-h-96 max-w-full rounded-md object-contain"
    />
  )
}

function RemoteArtifactImage({ url, title }: { url: URL; title: string }) {
  const { t } = useTranslation()
  const [preview, setPreview] = useState(false)

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        {!isElectron() && !preview && (
          <Button type="button" variant="outline" size="sm" onClick={() => setPreview(true)}>
            {t('search.previewArtifactImage')}
          </Button>
        )}
        <a
          href={url.href}
          target="_blank"
          rel="noopener noreferrer"
          referrerPolicy="no-referrer"
          className="text-primary break-all underline"
        >
          {t('home.news.openLink')} ({url.host})
        </a>
      </div>
      {preview && <ArtifactImage src={url.href} title={title} />}
    </div>
  )
}

function SearchArtifact({ artifact }: { artifact: AISearchArtifact }) {
  const { kind, title, content } = artifact
  let body: ReactNode

  switch (kind) {
    case 'markdown':
      body = <MarkdownRenderer content={content} untrusted />
      break
    case 'svg':
      // SVG 只进入图片解码上下文，避免把生成的节点或事件处理程序挂入 WebUI。
      body = (
        <ArtifactImage
          src={`data:image/svg+xml;charset=utf-8,${encodeURIComponent(content)}`}
          title={title}
        />
      )
      break
    case 'image': {
      const source = resolveImageSource(content)
      if (!source) return null
      body =
        source.kind === 'local' ? (
          <LocalArtifactImage path={source.path} title={title} />
        ) : (
          <RemoteArtifactImage key={source.url.href} url={source.url} title={title} />
        )
      break
    }
    default:
      return null
  }

  return (
    <figure className="border-border space-y-2 rounded-md border p-3">
      <figcaption className="text-sm font-medium break-words">{title}</figcaption>
      {body}
    </figure>
  )
}

export function SearchArtifacts({ artifacts }: { artifacts?: AISearchArtifact[] }) {
  if (!artifacts?.length) return null

  return (
    <div className="space-y-2">
      {artifacts.map((artifact, index) => (
        <SearchArtifact key={`${artifact.kind}:${index}`} artifact={artifact} />
      ))}
    </div>
  )
}
