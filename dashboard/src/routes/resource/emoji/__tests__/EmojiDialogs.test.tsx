import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { EmojiDetailDialog, EmojiEditDialog, EmojiUploadDialog } from '../EmojiDialogs'
import type { Emoji } from '@/types/emoji'

// 稳定引用的 mock：toast / http.post / updateEmoji / 假 Uppy 类
const hoisted = vi.hoisted(() => {
  const toastMock = vi.fn()
  const postMock =
    vi.fn<
      (
        url: string,
        options: { body: FormData; parse?: string; errorMessage?: string }
      ) => Promise<unknown>
    >()
  const updateEmojiMock = vi.fn<(id: number, data: Record<string, unknown>) => Promise<unknown>>()

  interface FakeUppyFile {
    id: string
    name: string
    preview?: string
    data: File
  }

  // 假 Uppy：只实现组件用到的 on/off/getFiles/cancelAll，emit 用于测试驱动“下一步”
  class FakeUppy {
    static instances: FakeUppy[] = []
    files: FakeUppyFile[] = []
    private handlers = new Map<string, Set<() => void>>()

    constructor(_options?: unknown) {
      FakeUppy.instances.push(this)
    }

    on(event: string, handler: () => void) {
      if (!this.handlers.has(event)) this.handlers.set(event, new Set())
      this.handlers.get(event)!.add(handler)
      return this
    }

    off(event: string, handler: () => void) {
      this.handlers.get(event)?.delete(handler)
      return this
    }

    getFiles() {
      return this.files
    }

    cancelAll() {}

    emit(event: string) {
      this.handlers.get(event)?.forEach((handler) => handler())
    }
  }

  return { toastMock, postMock, updateEmojiMock, FakeUppy }
})

vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: hoisted.toastMock }) }))
vi.mock('@/lib/http', () => ({ backendApi: { post: hoisted.postMock } }))
vi.mock('@/lib/emoji-api', () => ({
  getEmojiOriginalUrl: (id: number) => `/mock-original/${id}`,
  getEmojiUploadUrl: () => '/mock-upload',
  updateEmoji: hoisted.updateEmojiMock,
}))
// Markdown 内部依赖 react-markdown，桩成透传 div
vi.mock('@/components/ui/markdown', () => ({
  Markdown: ({ children }: { children: string }) => <div data-testid="markdown">{children}</div>,
}))
vi.mock('@uppy/react/dashboard', () => ({
  default: () => <div data-testid="uppy-dashboard" />,
}))
vi.mock('@uppy/core', () => ({ default: hoisted.FakeUppy }))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

beforeEach(() => {
  hoisted.FakeUppy.instances.length = 0
})

function makeEmoji(overrides: Partial<Emoji> = {}): Emoji {
  return {
    id: 5,
    full_path: '/data/emoji/cat.png',
    format: 'png',
    emoji_hash: 'hash-abc',
    description: '一只开心的猫',
    query_count: 0,
    is_registered: true,
    is_banned: false,
    status: 'adopted',
    emotion: '开心',
    record_time: 1_710_000_000,
    register_time: null,
    usage_count: 42,
    last_used_time: null,
    ...overrides,
  }
}

describe('EmojiDetailDialog', () => {
  it('emoji 为 null 时不渲染任何内容', () => {
    render(<EmojiDetailDialog emoji={null} open onOpenChange={vi.fn()} />)
    expect(screen.queryByText('表情包详情')).not.toBeInTheDocument()
  })

  it('渲染 ID/格式/哈希/描述/状态/使用次数与原图地址', () => {
    const emoji = makeEmoji()
    render(<EmojiDetailDialog emoji={emoji} open onOpenChange={vi.fn()} />)
    expect(screen.getByText('表情包详情')).toBeInTheDocument()
    expect(screen.getByText('5')).toBeInTheDocument()
    expect(screen.getByText('PNG')).toBeInTheDocument()
    expect(screen.getByText('hash-abc')).toBeInTheDocument()
    expect(screen.getByText('/data/emoji/cat.png')).toBeInTheDocument()
    expect(screen.getByTestId('markdown')).toHaveTextContent('一只开心的猫')
    expect(screen.getByText('据为己用')).toBeInTheDocument()
    expect(screen.getByText('42')).toBeInTheDocument()
    expect(screen.getByText('开心')).toBeInTheDocument()
    // 预览图使用原图 URL
    expect(screen.getByAltText('一只开心的猫')).toHaveAttribute('src', '/mock-original/5')
    // 注册时间与最后使用为 null 时显示 “-”
    expect(screen.getAllByText('-').length).toBeGreaterThanOrEqual(2)
  })

  it('描述与情绪为空时均以 “-” 兜底', () => {
    const emoji = makeEmoji({ description: '', emotion: null })
    render(<EmojiDetailDialog emoji={emoji} open onOpenChange={vi.fn()} />)
    expect(screen.queryByTestId('markdown')).not.toBeInTheDocument()
    // 描述、情绪、注册时间、最后使用共 4 个 “-”
    expect(screen.getAllByText('-')).toHaveLength(4)
  })
})

describe('EmojiEditDialog', () => {
  it('emoji 为 null 时不渲染', () => {
    render(<EmojiEditDialog emoji={null} open onOpenChange={vi.fn()} onSuccess={vi.fn()} />)
    expect(screen.queryByText('编辑表情包')).not.toBeInTheDocument()
  })

  it('保存时标准化情绪标签并按 adopted 状态映射注册标志', async () => {
    const onOpenChange = vi.fn()
    const onSuccess = vi.fn()
    hoisted.updateEmojiMock.mockResolvedValue({ success: true, message: 'ok' })
    render(
      <EmojiEditDialog
        emoji={makeEmoji({ id: 7, emotion: '开心', status: 'adopted' })}
        open
        onOpenChange={onOpenChange}
        onSuccess={onSuccess}
      />
    )
    const textarea = screen.getByPlaceholderText('输入情绪描述...')
    // 预填当前情绪
    expect(textarea).toHaveValue('开心')
    // 半角逗号分隔并带空白，保存时应标准化为半角逗号拼接（空段被过滤）
    // 注意：源码 split 正则为 /[,,]/（两个 ASCII 逗号），全角逗号“，”并不会被拆分，
    // 此处按现状特征化，只用半角逗号驱动
    fireEvent.change(textarea, { target: { value: ' 开心 , , 高兴 , ' } })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() =>
      expect(hoisted.updateEmojiMock).toHaveBeenCalledWith(7, {
        emotion: '开心,高兴',
        is_registered: true,
        is_banned: false,
      })
    )
    expect(hoisted.toastMock).toHaveBeenCalledWith(
      expect.objectContaining({ title: '成功', description: '表情包信息已更新' })
    )
    expect(onOpenChange).toHaveBeenCalledWith(false)
    expect(onSuccess).toHaveBeenCalledTimes(1)
  })

  it('现状特征化：全角逗号不被视为分隔符，整串作为单个标签保留', async () => {
    hoisted.updateEmojiMock.mockResolvedValue({ success: true, message: 'ok' })
    render(
      <EmojiEditDialog
        emoji={makeEmoji({ id: 9, emotion: '', status: 'known' })}
        open
        onOpenChange={vi.fn()}
        onSuccess={vi.fn()}
      />
    )
    fireEvent.change(screen.getByPlaceholderText('输入情绪描述...'), {
      target: { value: '开心，高兴' },
    })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() =>
      expect(hoisted.updateEmojiMock).toHaveBeenCalledWith(9, {
        emotion: '开心，高兴',
        is_registered: false,
        is_banned: false,
      })
    )
  })

  it('unknown 状态保存时清空情绪且两个标志均为 false', async () => {
    hoisted.updateEmojiMock.mockResolvedValue({ success: true, message: 'ok' })
    render(
      <EmojiEditDialog
        emoji={makeEmoji({ id: 8, emotion: '哈哈', status: 'unknown' })}
        open
        onOpenChange={vi.fn()}
        onSuccess={vi.fn()}
      />
    )
    fireEvent.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() =>
      expect(hoisted.updateEmojiMock).toHaveBeenCalledWith(8, {
        emotion: '',
        is_registered: false,
        is_banned: false,
      })
    )
  })

  it('保存失败时弹出错误 toast 且不关闭对话框', async () => {
    const onOpenChange = vi.fn()
    const onSuccess = vi.fn()
    hoisted.updateEmojiMock.mockRejectedValue(new Error('保存崩了'))
    render(
      <EmojiEditDialog
        emoji={makeEmoji()}
        open
        onOpenChange={onOpenChange}
        onSuccess={onSuccess}
      />
    )
    fireEvent.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() =>
      expect(hoisted.toastMock).toHaveBeenCalledWith(
        expect.objectContaining({ title: '错误', description: '保存崩了', variant: 'destructive' })
      )
    )
    expect(onOpenChange).not.toHaveBeenCalled()
    expect(onSuccess).not.toHaveBeenCalled()
    // saving 复位后按钮回到“保存”文案
    expect(screen.getByRole('button', { name: '保存' })).toBeEnabled()
  })

  it('点击取消仅关闭对话框，不触发保存', () => {
    const onOpenChange = vi.fn()
    render(
      <EmojiEditDialog emoji={makeEmoji()} open onOpenChange={onOpenChange} onSuccess={vi.fn()} />
    )
    fireEvent.click(screen.getByRole('button', { name: '取消' }))
    expect(onOpenChange).toHaveBeenCalledWith(false)
    expect(hoisted.updateEmojiMock).not.toHaveBeenCalled()
  })
})

// 拿到组件在 useMemo 中创建的最后一个假 Uppy 实例
function latestUppy() {
  const instance = hoisted.FakeUppy.instances.at(-1)
  expect(instance).toBeDefined()
  return instance!
}

function makeUppyFile(id: string, name: string) {
  return { id, name, data: new File(['x'], name, { type: 'image/png' }) }
}

describe('EmojiUploadDialog', () => {
  it('初始处于选择步骤并渲染 Uppy Dashboard', () => {
    render(<EmojiUploadDialog open onOpenChange={vi.fn()} onSuccess={vi.fn()} />)
    expect(screen.getByText('上传表情包 - 选择文件')).toBeInTheDocument()
    expect(screen.getByTestId('uppy-dashboard')).toBeInTheDocument()
  })

  it('单文件：填写标签后上传成功并关闭对话框', async () => {
    const user = userEvent.setup()
    const onOpenChange = vi.fn()
    const onSuccess = vi.fn()
    hoisted.postMock.mockResolvedValue(new Response())
    render(<EmojiUploadDialog open onOpenChange={onOpenChange} onSuccess={onSuccess} />)

    const uppy = latestUppy()
    uppy.files = [makeUppyFile('f1', 'a.png')]
    act(() => uppy.emit('upload'))

    expect(await screen.findByText('上传表情包 - 填写信息')).toBeInTheDocument()
    const uploadButton = screen.getByRole('button', { name: '上传' })
    // 情感标签必填，未填写时上传按钮禁用
    expect(uploadButton).toBeDisabled()

    await user.type(screen.getByPlaceholderText('输入一个标签'), 'happy')
    expect(uploadButton).toBeEnabled()
    await user.click(uploadButton)

    await waitFor(() => expect(hoisted.postMock).toHaveBeenCalledTimes(1))
    const [url, options] = hoisted.postMock.mock.calls[0]
    expect(url).toBe('/mock-upload')
    expect(options.parse).toBe('response')
    expect(options.body.get('description')).toBe('happy')
    expect((options.body.get('file') as File).name).toBe('a.png')

    await waitFor(() =>
      expect(hoisted.toastMock).toHaveBeenCalledWith(
        expect.objectContaining({ title: '上传成功', description: '成功上传 1 个表情包' })
      )
    )
    expect(onOpenChange).toHaveBeenCalledWith(false)
    expect(onSuccess).toHaveBeenCalledTimes(1)
  })

  it('多文件：逐个填写完成度更新，部分失败时提示且不关闭', async () => {
    const user = userEvent.setup()
    const onOpenChange = vi.fn()
    const onSuccess = vi.fn()
    render(<EmojiUploadDialog open onOpenChange={onOpenChange} onSuccess={onSuccess} />)

    const uppy = latestUppy()
    uppy.files = [makeUppyFile('f1', 'a.png'), makeUppyFile('f2', 'b.png')]
    act(() => uppy.emit('upload'))

    expect(await screen.findByText('上传表情包 - 批量编辑')).toBeInTheDocument()
    const progressText = (text: string) =>
      screen.getByText((_, element) => element?.tagName === 'SPAN' && element.textContent === text)
    expect(progressText('编辑表情包信息(0/2 已完成)')).toBeInTheDocument()
    expect(screen.getByText('未完成')).toBeInTheDocument()

    // 选中第一个文件并填写标签
    await user.click(screen.getByText('a.png'))
    await user.type(screen.getByPlaceholderText('输入一个标签'), 'x')
    expect(progressText('编辑表情包信息(1/2 已完成)')).toBeInTheDocument()

    // 选中第二个文件并填写标签，全部完成
    await user.click(screen.getByText('b.png'))
    const inputs = screen.getAllByPlaceholderText('输入一个标签')
    await user.type(inputs[inputs.length - 1], 'y')
    expect(screen.getByText('全部完成')).toBeInTheDocument()

    // 第一个上传成功、第二个失败 → 部分失败提示，onSuccess 仍触发但不关闭
    hoisted.postMock.mockResolvedValueOnce(new Response())
    hoisted.postMock.mockRejectedValueOnce(new Error('网络炸了'))
    await user.click(screen.getByRole('button', { name: '上传全部 (2)' }))

    await waitFor(() =>
      expect(hoisted.toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '部分上传失败',
          description: '成功 1 个,失败 1 个',
          variant: 'destructive',
        })
      )
    )
    expect(onSuccess).toHaveBeenCalledTimes(1)
    expect(onOpenChange).not.toHaveBeenCalled()
  })

  it('编辑步骤点击返回回到文件选择步骤', async () => {
    render(<EmojiUploadDialog open onOpenChange={vi.fn()} onSuccess={vi.fn()} />)
    const uppy = latestUppy()
    uppy.files = [makeUppyFile('f1', 'a.png')]
    act(() => uppy.emit('upload'))
    expect(await screen.findByText('上传表情包 - 填写信息')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '返回' }))
    expect(await screen.findByText('上传表情包 - 选择文件')).toBeInTheDocument()
  })

  it('多个标签合并为逗号分隔的 description，空标签被过滤', async () => {
    const user = userEvent.setup()
    hoisted.postMock.mockResolvedValue(new Response())
    render(<EmojiUploadDialog open onOpenChange={vi.fn()} onSuccess={vi.fn()} />)
    const uppy = latestUppy()
    uppy.files = [makeUppyFile('f1', 'a.png')]
    act(() => uppy.emit('upload'))
    await screen.findByText('上传表情包 - 填写信息')

    await user.type(screen.getByPlaceholderText('输入一个标签'), '开心')
    // 添加第二个标签并填写，第三个留空
    await user.click(screen.getByRole('button', { name: '添加标签' }))
    await user.click(screen.getByRole('button', { name: '添加标签' }))
    const inputs = screen.getAllByPlaceholderText('输入一个标签')
    expect(inputs).toHaveLength(3)
    await user.type(inputs[1], '高兴')

    await user.click(screen.getByRole('button', { name: '上传' }))
    await waitFor(() => expect(hoisted.postMock).toHaveBeenCalledTimes(1))
    expect(hoisted.postMock.mock.calls[0][1].body.get('description')).toBe('开心,高兴')
  })
})
