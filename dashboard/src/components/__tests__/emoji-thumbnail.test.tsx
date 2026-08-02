import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { EmojiThumbnail } from '../emoji-thumbnail'

// 全局 fetch 桩：组件直接用 fetch 拉取缩略图，测试中不开真实网络
const fetchMock = vi.fn()

// 构造最小可用的 Response 桩
const okResponse = () =>
  ({
    ok: true,
    status: 200,
    blob: async () => new Blob(['img'], { type: 'image/png' }),
  }) as unknown as Response

const statusResponse = (status: number) =>
  ({ ok: status >= 200 && status < 300, status }) as unknown as Response

beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('EmojiThumbnail', () => {
  it('挂载后携带 Cookie 请求指定地址', async () => {
    fetchMock.mockResolvedValue(okResponse())

    render(<EmojiThumbnail src="/api/emoji/1/thumbnail" />)

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith('/api/emoji/1/thumbnail', { credentials: 'include' })
    })
  })

  it('请求未完成时显示 Skeleton 占位', () => {
    // 永不 resolve 的请求让组件停留在 loading 态
    fetchMock.mockReturnValue(new Promise(() => {}))

    const { container } = render(<EmojiThumbnail src="/api/emoji/2/thumbnail" />)

    expect(container.querySelector('.animate-pulse')).not.toBeNull()
    expect(container.querySelector('img')).toBeNull()
  })

  it('加载成功后渲染图片并使用默认 alt', async () => {
    fetchMock.mockResolvedValue(okResponse())
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:test/success')

    render(<EmojiThumbnail src="/api/emoji/3/thumbnail" />)

    const img = await screen.findByRole('img')
    expect(img).toHaveAttribute('src', 'blob:test/success')
    expect(img).toHaveAttribute('alt', '表情包')
  })

  it('传入自定义 alt 时优先使用', async () => {
    fetchMock.mockResolvedValue(okResponse())

    render(<EmojiThumbnail src="/api/emoji/4/thumbnail" alt="开心猫猫" />)

    const img = await screen.findByRole('img')
    expect(img).toHaveAttribute('alt', '开心猫猫')
  })

  it('响应非 2xx 时显示占位图标', async () => {
    fetchMock.mockResolvedValue(statusResponse(404))

    const { container } = render(<EmojiThumbnail src="/api/emoji/5/thumbnail" />)

    await waitFor(() => {
      expect(container.querySelector('svg')).not.toBeNull()
    })
    expect(container.querySelector('img')).toBeNull()
  })

  it('请求抛出异常时进入错误态并打印日志', async () => {
    const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const networkError = new Error('网络中断')
    fetchMock.mockRejectedValue(networkError)

    const { container } = render(<EmojiThumbnail src="/api/emoji/6/thumbnail" />)

    await waitFor(() => {
      expect(container.querySelector('svg')).not.toBeNull()
    })
    expect(consoleErrorSpy).toHaveBeenCalledWith('加载缩略图失败:', networkError)
  })

  it('202 响应超过最大重试次数后进入错误态且不再重试', async () => {
    fetchMock.mockResolvedValue(statusResponse(202))

    const { container } = render(<EmojiThumbnail src="/api/emoji/7/thumbnail" maxRetries={0} />)

    await waitFor(() => {
      expect(container.querySelector('svg')).not.toBeNull()
    })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('202 响应在重试间隔后重新请求并最终加载成功', async () => {
    fetchMock.mockResolvedValueOnce(statusResponse(202)).mockResolvedValueOnce(okResponse())

    render(<EmojiThumbnail src="/api/emoji/8/thumbnail" maxRetries={3} retryInterval={1} />)

    // 第一次 202 后按 retryInterval 调度重试，第二次成功渲染图片
    expect(await screen.findByRole('img')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('src 变化时重置状态并重新请求新地址', async () => {
    fetchMock.mockResolvedValue(okResponse())
    vi.spyOn(URL, 'createObjectURL')
      .mockReturnValueOnce('blob:test/first')
      .mockReturnValueOnce('blob:test/second')

    const { rerender } = render(<EmojiThumbnail src="/api/emoji/9/thumbnail" />)
    const firstImg = await screen.findByRole('img')
    expect(firstImg).toHaveAttribute('src', 'blob:test/first')

    rerender(<EmojiThumbnail src="/api/emoji/10/thumbnail" />)

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith('/api/emoji/10/thumbnail', { credentials: 'include' })
    })
    await waitFor(() => {
      expect(screen.getByRole('img')).toHaveAttribute('src', 'blob:test/second')
    })
  })

  it('卸载时释放已创建的 Object URL', async () => {
    fetchMock.mockResolvedValue(okResponse())
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:test/revoke-me')
    const revokeSpy = vi.spyOn(URL, 'revokeObjectURL')

    const { unmount } = render(<EmojiThumbnail src="/api/emoji/11/thumbnail" />)
    await screen.findByRole('img')

    unmount()

    expect(revokeSpy).toHaveBeenCalledWith('blob:test/revoke-me')
  })
})
