import { beforeEach, describe, expect, it, vi } from 'vitest'

import { resolveApiPath } from '@/lib/api-base'
import { ApiError, backendApi } from '@/lib/http'

import {
  addUserEmoji,
  deleteUserEmoji,
  listUserEmojis,
  loadUserEmojiPayload,
  resolveUserEmojiUrl,
  type UserEmojiItem,
} from '../user-emoji-api'

vi.mock('@/lib/http', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/http')>()
  return {
    ...actual,
    backendApi: {
      request: vi.fn(),
      get: vi.fn(),
      post: vi.fn(),
      put: vi.fn(),
      patch: vi.fn(),
      delete: vi.fn(),
    },
  }
})

vi.mock('@/lib/api-base', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api-base')>()
  return {
    ...actual,
    resolveApiPath: vi.fn(),
  }
})

const getMock = vi.mocked(backendApi.get)
const postMock = vi.mocked(backendApi.post)
const deleteMock = vi.mocked(backendApi.delete)
const resolveApiPathMock = vi.mocked(resolveApiPath)

/** 构造一个用户表情包条目桩对象 */
function makeItem(overrides: Partial<UserEmojiItem> = {}): UserEmojiItem {
  return {
    id: 'emoji-1',
    content_type: 'image/png',
    content_url: '/api/webui/user-emojis/emoji-1/content',
    created_at: 1720000000,
    ...overrides,
  }
}

beforeEach(() => {
  getMock.mockReset()
  postMock.mockReset()
  deleteMock.mockReset()
  resolveApiPathMock.mockReset()
})

describe('listUserEmojis', () => {
  it('以 user_id 查询参数获取列表并原样返回响应', async () => {
    const response = { items: [makeItem()], limit: 20 }
    getMock.mockResolvedValue(response)

    await expect(listUserEmojis('u1')).resolves.toBe(response)
    expect(getMock).toHaveBeenCalledWith('/api/webui/user-emojis', {
      query: { user_id: 'u1' },
      errorMessage: '获取用户表情包失败',
    })
  })

  it('后端返回错误时向上抛出 ApiError', async () => {
    getMock.mockRejectedValue(new ApiError('获取用户表情包失败', { status: 500 }))

    await expect(listUserEmojis('u1')).rejects.toBeInstanceOf(ApiError)
  })
})

describe('addUserEmoji', () => {
  it('把 user_id 与文件封装进 FormData 提交并返回新条目', async () => {
    const item = makeItem()
    const file = new File(['bytes'], 'sticker.png', { type: 'image/png' })
    postMock.mockResolvedValue({ item })

    await expect(addUserEmoji('u1', file)).resolves.toBe(item)

    expect(postMock).toHaveBeenCalledTimes(1)
    const [url, options] = postMock.mock.calls[0]
    expect(url).toBe('/api/webui/user-emojis')
    expect(options).toMatchObject({ errorMessage: '添加用户表情包失败' })
    const body = (options as { body: FormData }).body
    expect(body).toBeInstanceOf(FormData)
    expect(body.get('user_id')).toBe('u1')
    expect((body.get('file') as File).name).toBe('sticker.png')
  })

  it('后端返回错误时向上抛出 ApiError', async () => {
    postMock.mockRejectedValue(new ApiError('添加用户表情包失败', { status: 413 }))

    await expect(
      addUserEmoji('u1', new File(['x'], 'big.png', { type: 'image/png' }))
    ).rejects.toMatchObject({ status: 413 })
  })
})

describe('deleteUserEmoji', () => {
  it('对表情包 ID 做 URL 编码并携带 user_id 查询参数', async () => {
    deleteMock.mockResolvedValue(undefined)

    await expect(deleteUserEmoji('u1', 'e/1')).resolves.toBeUndefined()
    expect(deleteMock).toHaveBeenCalledWith('/api/webui/user-emojis/e%2F1', {
      query: { user_id: 'u1' },
      errorMessage: '删除用户表情包失败',
    })
  })

  it('后端返回错误时向上抛出 ApiError', async () => {
    deleteMock.mockRejectedValue(new ApiError('删除用户表情包失败', { status: 404 }))

    await expect(deleteUserEmoji('u1', 'e1')).rejects.toMatchObject({ status: 404 })
  })
})

describe('resolveUserEmojiUrl', () => {
  it('委托 resolveApiPath 解析 content_url', async () => {
    resolveApiPathMock.mockResolvedValue('http://backend/api/webui/user-emojis/emoji-1/content')

    await expect(resolveUserEmojiUrl(makeItem())).resolves.toBe(
      'http://backend/api/webui/user-emojis/emoji-1/content'
    )
    expect(resolveApiPathMock).toHaveBeenCalledWith('/api/webui/user-emojis/emoji-1/content')
  })
})

describe('loadUserEmojiPayload', () => {
  it('以 blob 模式强缓存读取内容并转换为 base64 载荷', async () => {
    const item = makeItem()
    getMock.mockResolvedValue(new Blob(['hello'], { type: 'image/png' }))

    const payload = await loadUserEmojiPayload(item)

    expect(getMock).toHaveBeenCalledWith(item.content_url, {
      parse: 'blob',
      cache: 'force-cache',
      errorMessage: '读取用户表情包失败',
    })
    // 'hello' 的 base64 编码为 aGVsbG8=
    expect(payload.base64).toBe('aGVsbG8=')
    expect(payload.data_url).toBe(`data:image/png;base64,${payload.base64}`)
    expect(payload.mime_type).toBe('image/png')
    expect(payload.name).toBe('emoji-1.png')
  })

  it('blob 未携带类型时回退使用条目的 content_type 推断扩展名', async () => {
    const item = makeItem({ id: 'emoji-2', content_type: 'image/gif' })
    getMock.mockResolvedValue(new Blob(['gif-bytes']))

    const payload = await loadUserEmojiPayload(item)

    expect(payload.mime_type).toBe('image/gif')
    expect(payload.name).toBe('emoji-2.gif')
  })

  it('未知内容类型时扩展名回退为 png', async () => {
    const item = makeItem({ id: 'emoji-3', content_type: 'image/bmp' })
    getMock.mockResolvedValue(new Blob(['bmp-bytes'], { type: 'image/bmp' }))

    const payload = await loadUserEmojiPayload(item)

    expect(payload.name).toBe('emoji-3.png')
    expect(payload.mime_type).toBe('image/bmp')
  })

  it('内容为空导致 base64 缺失时抛出「用户表情包内容无效」', async () => {
    getMock.mockResolvedValue(new Blob([], { type: 'image/png' }))

    await expect(loadUserEmojiPayload(makeItem())).rejects.toThrow('用户表情包内容无效')
  })

  it('后端读取失败时向上抛出 ApiError', async () => {
    getMock.mockRejectedValue(new ApiError('读取用户表情包失败', { status: 502 }))

    await expect(loadUserEmojiPayload(makeItem())).rejects.toMatchObject({ status: 502 })
  })
})
