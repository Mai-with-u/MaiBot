import { useContext, useState } from 'react'

import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { AnimationProvider } from '../animation-provider'
import { AssetStoreProvider } from '../asset-provider'
import { EmbedPageShell } from '../embed-page-shell'
import { HttpWarningBanner } from '../http-warning-banner'
import { AnimationContext } from '@/lib/animation-context'
import { useAssetStore } from '@/lib/asset-store-context'
import * as assetStore from '@/lib/asset-store'

const { authState } = vi.hoisted(() => ({
  authState: { checking: false },
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

vi.mock('@/hooks/use-auth', () => ({
  useAuthGuard: () => ({ checking: authState.checking }),
}))

vi.mock('@/hooks/use-background', () => ({
  useBackground: () => ({ config: { type: 'none' } }),
}))

vi.mock('@/components/background-layer', () => ({
  BackgroundLayer: ({ layerId }: { layerId: string }) => (
    <div data-testid="background" data-layer-id={layerId} />
  ),
}))

vi.mock('@/lib/asset-store', () => ({
  getAsset: vi.fn(),
}))

function AnimationConsumer() {
  const context = useContext(AnimationContext)
  if (!context) return null
  return (
    <button type="button" onClick={() => context.setEnableAnimations(!context.enableAnimations)}>
      {String(context.enableAnimations)}
    </button>
  )
}

function AssetConsumer({ assetId }: { assetId: string }) {
  const { getAssetUrl } = useAssetStore()
  const [url, setUrl] = useState('未加载')
  return (
    <>
      <button
        type="button"
        onClick={() => void getAssetUrl(assetId).then((value) => setUrl(value ?? '缺失'))}
      >
        加载资源
      </button>
      <span>{url}</span>
    </>
  )
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  document.documentElement.classList.remove('no-animations')
  localStorage.clear()
  sessionStorage.clear()
})

beforeEach(() => {
  authState.checking = false
})

describe('EmbedPageShell', () => {
  it('认证检查期间只显示加载状态并同步标题', () => {
    authState.checking = true
    render(
      <EmbedPageShell shellId="test-shell" title="测试标题">
        <div>内部内容</div>
      </EmbedPageShell>
    )

    expect(document.title).toBe('测试标题')
    expect(screen.getByText('麦麦正在啃食服务器...')).toBeInTheDocument()
    expect(screen.queryByText('内部内容')).not.toBeInTheDocument()
  })

  it('认证完成后渲染背景、主区域与内容', () => {
    render(
      <EmbedPageShell shellId="test-shell" title="测试标题">
        <div>内部内容</div>
      </EmbedPageShell>
    )

    expect(screen.getByText('内部内容')).toBeInTheDocument()
    expect(screen.getByRole('main')).toHaveAttribute('id', 'main-content')
    expect(screen.getByRole('main').parentElement).toHaveAttribute(
      'data-dashboard-shell',
      'test-shell'
    )
    expect(screen.getByTestId('background')).toHaveAttribute('data-layer-id', 'page')
  })
})

describe('AnimationProvider', () => {
  it('从 storageKey 恢复状态并同步根节点类名', async () => {
    localStorage.setItem('custom-animation-key', 'false')
    render(
      <AnimationProvider storageKey="custom-animation-key">
        <AnimationConsumer />
      </AnimationProvider>
    )

    expect(screen.getByRole('button')).toHaveTextContent('false')
    await waitFor(() => expect(document.documentElement).toHaveClass('no-animations'))
    expect(localStorage.getItem('custom-animation-key')).toBe('false')
  })

  it('消费方切换状态后更新类名和持久化值', async () => {
    const user = userEvent.setup()
    render(
      <AnimationProvider defaultEnabled={false}>
        <AnimationConsumer />
      </AnimationProvider>
    )

    await user.click(screen.getByRole('button'))
    expect(screen.getByRole('button')).toHaveTextContent('true')
    await waitFor(() => expect(document.documentElement).not.toHaveClass('no-animations'))
    expect(localStorage.getItem('enable-animations')).toBe('true')
  })
})

describe('AssetStoreProvider', () => {
  it('读取资源后创建并缓存 Blob URL，卸载时统一释放', async () => {
    const user = userEvent.setup()
    const blob = new Blob(['asset'])
    vi.mocked(assetStore.getAsset).mockResolvedValue({
      id: 'asset-a',
      filename: 'asset.png',
      type: 'image',
      mimeType: 'image/png',
      blob,
      size: blob.size,
      createdAt: Date.now(),
    })
    const createSpy = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:asset-a')
    const revokeSpy = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
    const { unmount } = render(
      <AssetStoreProvider>
        <AssetConsumer assetId="asset-a" />
      </AssetStoreProvider>
    )

    await user.click(screen.getByRole('button', { name: '加载资源' }))
    expect(await screen.findByText('blob:asset-a')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '加载资源' }))

    expect(assetStore.getAsset).toHaveBeenCalledOnce()
    expect(createSpy).toHaveBeenCalledWith(blob)
    unmount()
    expect(revokeSpy).toHaveBeenCalledWith('blob:asset-a')
  })

  it('资源不存在时返回缺失且不创建 URL', async () => {
    const user = userEvent.setup()
    vi.mocked(assetStore.getAsset).mockResolvedValue(undefined)
    const createSpy = vi.spyOn(URL, 'createObjectURL')
    render(
      <AssetStoreProvider>
        <AssetConsumer assetId="missing" />
      </AssetStoreProvider>
    )

    await user.click(screen.getByRole('button', { name: '加载资源' }))
    expect(await screen.findByText('缺失')).toBeInTheDocument()
    expect(createSpy).not.toHaveBeenCalled()
  })
})

describe('HttpWarningBanner', () => {
  it('非本地 HTTP 访问显示警告，关闭后写入会话存储', async () => {
    const user = userEvent.setup()
    vi.stubGlobal('location', { protocol: 'http:', hostname: 'example.com' })
    render(<HttpWarningBanner />)

    expect(screen.getByText('httpWarning.title')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'httpWarning.dismiss' }))
    expect(screen.queryByText('httpWarning.title')).not.toBeInTheDocument()
    expect(sessionStorage.getItem('http-warning-dismissed')).toBe('true')
  })

  it.each(['localhost', '127.0.0.1', '::1'])('本地地址 %s 不显示警告', (hostname) => {
    vi.stubGlobal('location', { protocol: 'http:', hostname })
    render(<HttpWarningBanner />)
    expect(screen.queryByText('httpWarning.title')).not.toBeInTheDocument()
  })

  it('HTTPS 或已关闭状态不显示警告', () => {
    sessionStorage.setItem('http-warning-dismissed', 'true')
    vi.stubGlobal('location', { protocol: 'https:', hostname: 'example.com' })
    render(<HttpWarningBanner />)
    expect(screen.queryByText('httpWarning.title')).not.toBeInTheDocument()
  })
})
