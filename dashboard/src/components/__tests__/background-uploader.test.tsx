import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { BackgroundUploader } from '@/components/background-uploader'

const mocks = vi.hoisted(() => ({
  addAsset: vi.fn(),
  getAsset: vi.fn(),
  getAssetUrl: vi.fn(),
}))

vi.mock('@/lib/asset-store', () => ({
  addAsset: mocks.addAsset,
  getAsset: mocks.getAsset,
}))

vi.mock('@/lib/asset-store-context', () => ({
  useAssetStore: () => ({ getAssetUrl: mocks.getAssetUrl }),
}))

function getFileInput(): HTMLInputElement {
  const input = document.querySelector('input[type="file"]')
  if (!(input instanceof HTMLInputElement)) {
    throw new Error('未找到背景文件输入框')
  }
  return input
}

describe('BackgroundUploader', () => {
  const fetchMock = vi.fn()

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock)
    mocks.getAssetUrl.mockResolvedValue('blob:background')
    mocks.getAsset.mockResolvedValue({
      id: 'asset-1',
      filename: 'background.png',
      type: 'image',
      mimeType: 'image/png',
      size: 128,
    })
    mocks.addAsset.mockResolvedValue('created-asset')
    vi.spyOn(console, 'error').mockImplementation(() => undefined)
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('加载既有图片预览并允许清除选择', async () => {
    const onAssetSelect = vi.fn()
    render(<BackgroundUploader assetId="asset-1" onAssetSelect={onAssetSelect} />)

    const preview = await screen.findByAltText('Background preview')
    expect(preview).toHaveAttribute('src', 'blob:background')
    expect(screen.getByText('图片')).toBeInTheDocument()
    expect(mocks.getAssetUrl).toHaveBeenCalledWith('asset-1')
    expect(mocks.getAsset).toHaveBeenCalledWith('asset-1')

    const previewRoot = preview.parentElement
    if (!(previewRoot instanceof HTMLElement)) {
      throw new Error('图片预览结构不完整')
    }
    fireEvent.click(within(previewRoot).getByRole('button'))

    expect(onAssetSelect).toHaveBeenCalledWith(undefined)
    expect(screen.getByText('点击或拖拽上传')).toBeInTheDocument()
  })

  it('加载视频资源时渲染静音视频预览', async () => {
    mocks.getAsset.mockResolvedValue({
      id: 'asset-video',
      filename: 'background.mp4',
      type: 'video',
      mimeType: 'video/mp4',
      size: 256,
    })
    const { container } = render(
      <BackgroundUploader assetId="asset-video" onAssetSelect={vi.fn()} />
    )

    await waitFor(() => expect(container.querySelector('video')).toBeInTheDocument())
    expect(container.querySelector('video')).toHaveAttribute('src', 'blob:background')
    expect(screen.getByText('视频')).toBeInTheDocument()
  })

  it('资源记录缺失时主动清除失效 assetId', async () => {
    mocks.getAsset.mockResolvedValue(undefined)
    const onAssetSelect = vi.fn()
    render(<BackgroundUploader assetId="missing" onAssetSelect={onAssetSelect} />)

    await waitFor(() => expect(onAssetSelect).toHaveBeenCalledWith(undefined))
    expect(screen.queryByAltText('Background preview')).not.toBeInTheDocument()
  })

  it('上传合法图片并拒绝不支持类型和超大文件', async () => {
    const onAssetSelect = vi.fn()
    render(<BackgroundUploader onAssetSelect={onAssetSelect} />)
    const input = getFileInput()

    const image = new File(['image'], 'background.png', { type: 'image/png' })
    fireEvent.change(input, { target: { files: [image] } })
    await waitFor(() => expect(mocks.addAsset).toHaveBeenCalledWith(image))
    expect(onAssetSelect).toHaveBeenCalledWith('created-asset')
    expect(input.value).toBe('')

    const textFile = new File(['text'], 'notes.txt', { type: 'text/plain' })
    fireEvent.change(input, { target: { files: [textFile] } })
    expect(await screen.findByText('不支持的文件类型。请上传图片或视频。')).toBeInTheDocument()

    const largeFile = new File(['x'], 'large.mp4', { type: 'video/mp4' })
    Object.defineProperty(largeFile, 'size', { configurable: true, value: 51 * 1024 * 1024 })
    fireEvent.change(input, { target: { files: [largeFile] } })
    expect(await screen.findByText('文件过大。请上传小于 50MB 的文件。')).toBeInTheDocument()
  })

  it('支持拖拽文件，并在拖入和离开时更新高亮状态', async () => {
    const onAssetSelect = vi.fn()
    const { container } = render(<BackgroundUploader onAssetSelect={onAssetSelect} />)
    const dropZone = container.querySelector('.border-dashed')
    if (!(dropZone instanceof HTMLElement)) {
      throw new Error('未找到拖拽区域')
    }
    const file = new File(['video'], 'clip.webm', { type: 'video/webm' })

    fireEvent.dragEnter(dropZone)
    expect(dropZone).toHaveClass('border-primary')
    fireEvent.dragLeave(dropZone)
    expect(dropZone).not.toHaveClass('border-primary')

    fireEvent.drop(dropZone, { dataTransfer: { files: [file] } })
    await waitFor(() => expect(mocks.addAsset).toHaveBeenCalledWith(file))
    expect(onAssetSelect).toHaveBeenCalledWith('created-asset')
  })

  it('从 URL 下载资源，依据响应类型补全文件名后入库', async () => {
    const user = userEvent.setup()
    fetchMock.mockResolvedValue({
      ok: true,
      statusText: 'OK',
      headers: { get: vi.fn(() => 'image/webp') },
      blob: vi.fn().mockResolvedValue(new Blob(['image'], { type: 'image/webp' })),
    })
    const onAssetSelect = vi.fn()
    render(<BackgroundUploader onAssetSelect={onAssetSelect} />)

    await user.type(
      screen.getByPlaceholderText('https://example.com/image.jpg'),
      'https://example.com/avatar'
    )
    await user.click(screen.getByRole('button', { name: '获取' }))

    await waitFor(() => expect(mocks.addAsset).toHaveBeenCalled())
    const downloaded = mocks.addAsset.mock.calls[0][0]
    expect(downloaded).toBeInstanceOf(File)
    expect(downloaded).toMatchObject({
      name: 'avatar.webp',
      type: 'image/webp',
    })
    expect(onAssetSelect).toHaveBeenCalledWith('created-asset')
  })

  it('URL 下载失败时显示响应状态文本，禁用态不触发上传', async () => {
    const user = userEvent.setup()
    fetchMock.mockResolvedValue({
      ok: false,
      statusText: 'Forbidden',
      headers: { get: vi.fn() },
    })
    const onAssetSelect = vi.fn()
    const { rerender } = render(<BackgroundUploader onAssetSelect={onAssetSelect} />)

    await user.type(
      screen.getByPlaceholderText('https://example.com/image.jpg'),
      'https://example.com/forbidden.png'
    )
    await user.click(screen.getByRole('button', { name: '获取' }))
    expect(await screen.findByText('下载失败: Forbidden')).toBeInTheDocument()

    rerender(<BackgroundUploader onAssetSelect={onAssetSelect} disabled />)
    fireEvent.change(getFileInput(), {
      target: {
        files: [new File(['image'], 'disabled.png', { type: 'image/png' })],
      },
    })
    expect(mocks.addAsset).not.toHaveBeenCalled()
  })
})
