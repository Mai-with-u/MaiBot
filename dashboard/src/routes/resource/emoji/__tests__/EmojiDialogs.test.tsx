import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { EmojiDetailDialog, EmojiEditDialog, EmojiUploadDialog } from '../EmojiDialogs'
import { ApiError } from '@/lib/http/errors'
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

  return { toastMock, postMock, updateEmojiMock, recognize: vi.fn() }
})

vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: hoisted.toastMock }) }))
vi.mock('@/lib/http', () => ({ backendApi: { post: hoisted.postMock } }))
vi.mock('@/lib/emoji-api', () => ({
  getEmojiOriginalUrl: (id: number) => `/mock-original/${id}`,
  getEmojiUploadUrl: () => '/mock-upload',
  getEmojiRecognitionModels: async () => ['视觉 A', '视觉 B'],
  updateEmoji: hoisted.updateEmojiMock,
  recognizeEmoji: hoisted.recognize,
}))
// Markdown 内部依赖 react-markdown，桩成透传 div
vi.mock('@/components/ui/markdown', () => ({
  Markdown: ({ children }: { children: string }) => <div data-testid="markdown">{children}</div>,
}))
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
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
    is_locked: false,
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

// jsdom 未实现 Pointer Capture，Radix Select 打开下拉时会调用
function stubPointerCapture() {
  if (!Element.prototype.hasPointerCapture) {
    Element.prototype.hasPointerCapture = () => false
  }
  if (!Element.prototype.setPointerCapture) {
    Element.prototype.setPointerCapture = () => {}
  }
  if (!Element.prototype.releasePointerCapture) {
    Element.prototype.releasePointerCapture = () => {}
  }
}

describe('EmojiDetailDialog 覆盖补全', () => {
  it('discarded 状态展示丢弃徽章', () => {
    render(
      <EmojiDetailDialog emoji={makeEmoji({ status: 'discarded' })} open onOpenChange={vi.fn()} />
    )
    expect(screen.getByText('丢弃')).toBeInTheDocument()
  })

  it('unknown 状态展示不认识徽章', () => {
    render(
      <EmojiDetailDialog emoji={makeEmoji({ status: 'unknown' })} open onOpenChange={vi.fn()} />
    )
    expect(screen.getByText('不认识')).toBeInTheDocument()
  })

  it('known 状态展示认识徽章', () => {
    render(<EmojiDetailDialog emoji={makeEmoji({ status: 'known' })} open onOpenChange={vi.fn()} />)
    expect(screen.getByText('认识')).toBeInTheDocument()
  })

  it('描述为空时预览 alt 回退为“表情包”', () => {
    render(
      <EmojiDetailDialog emoji={makeEmoji({ description: '' })} open onOpenChange={vi.fn()} />
    )
    expect(screen.getByAltText('表情包')).toBeInTheDocument()
  })

  it('预览图 onError 时隐藏 img 并在父节点插入占位 svg', () => {
    render(<EmojiDetailDialog emoji={makeEmoji()} open onOpenChange={vi.fn()} />)
    const img = screen.getByAltText('一只开心的猫')
    const parent = img.parentElement!
    fireEvent.error(img)
    expect(parent.querySelector('svg')).not.toBeNull()
    expect(parent.querySelector('img')).toBeNull()
  })

  it('预览图 onError 且 parentElement 为空时只隐藏图片', () => {
    render(<EmojiDetailDialog emoji={makeEmoji()} open onOpenChange={vi.fn()} />)
    const img = screen.getByAltText('一只开心的猫') as HTMLImageElement
    Object.defineProperty(img, 'parentElement', { configurable: true, value: null })
    fireEvent.error(img)
    expect(img.style.display).toBe('none')
  })
})

describe('EmojiEditDialog 覆盖补全', () => {
  beforeEach(() => {
    stubPointerCapture()
  })

  it('emotion 为 null 时输入框预填空字符串', () => {
    render(
      <EmojiEditDialog
        emoji={makeEmoji({ emotion: null, status: 'known' })}
        open
        onOpenChange={vi.fn()}
        onSuccess={vi.fn()}
      />
    )
    expect(screen.getByPlaceholderText('输入情绪描述...')).toHaveValue('')
  })

  it('通过下拉将状态改为丢弃后保存 is_banned=true', async () => {
    const user = userEvent.setup()
    hoisted.updateEmojiMock.mockResolvedValue({ success: true, message: 'ok' })
    render(
      <EmojiEditDialog
        emoji={makeEmoji({ id: 11, emotion: '怒', status: 'known' })}
        open
        onOpenChange={vi.fn()}
        onSuccess={vi.fn()}
      />
    )
    await user.click(screen.getByRole('combobox'))
    await user.click(await screen.findByRole('option', { name: '丢弃' }))
    fireEvent.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() =>
      expect(hoisted.updateEmojiMock).toHaveBeenCalledWith(11, {
        emotion: '怒',
        is_registered: false,
        is_banned: true,
      })
    )
  })

  it('通过下拉将状态改为不认识后保存时清空情绪', async () => {
    const user = userEvent.setup()
    hoisted.updateEmojiMock.mockResolvedValue({ success: true, message: 'ok' })
    render(
      <EmojiEditDialog
        emoji={makeEmoji({ id: 12, emotion: '还在', status: 'known' })}
        open
        onOpenChange={vi.fn()}
        onSuccess={vi.fn()}
      />
    )
    await user.click(screen.getByRole('combobox'))
    await user.click(await screen.findByRole('option', { name: '不认识' }))
    fireEvent.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() =>
      expect(hoisted.updateEmojiMock).toHaveBeenCalledWith(12, {
        emotion: '',
        is_registered: false,
        is_banned: false,
      })
    )
  })

  it('通过下拉将状态改为据为己用后保存 is_registered=true', async () => {
    const user = userEvent.setup()
    hoisted.updateEmojiMock.mockResolvedValue({ success: true, message: 'ok' })
    render(
      <EmojiEditDialog
        emoji={makeEmoji({ id: 13, emotion: '喜', status: 'known' })}
        open
        onOpenChange={vi.fn()}
        onSuccess={vi.fn()}
      />
    )
    await user.click(screen.getByRole('combobox'))
    await user.click(await screen.findByRole('option', { name: '据为己用' }))
    fireEvent.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() =>
      expect(hoisted.updateEmojiMock).toHaveBeenCalledWith(13, {
        emotion: '喜',
        is_registered: true,
        is_banned: false,
      })
    )
  })

  it('保存抛出非 Error 时 toast 描述为“保存失败”', async () => {
    hoisted.updateEmojiMock.mockRejectedValue('boom')
    render(
      <EmojiEditDialog emoji={makeEmoji()} open onOpenChange={vi.fn()} onSuccess={vi.fn()} />
    )
    fireEvent.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() =>
      expect(hoisted.toastMock).toHaveBeenCalledWith(
        expect.objectContaining({ title: '错误', description: '保存失败', variant: 'destructive' })
      )
    )
  })
})

describe('EmojiUploadDialog', () => {
  const image = (name: string) => new File(['image'], name, { type: 'image/png' })
  const choose = (...files: File[]) => fireEvent.change(screen.getByLabelText('选择表情包文件'), { target: { files } })
  const submit = () => screen.getByRole('button', { name: /确认上传/ })

  it('手动只填内容即可上传，并保留确认的标签', async () => {
    render(<EmojiUploadDialog open onOpenChange={vi.fn()} onSuccess={vi.fn()} />)
    expect(screen.getByRole('button', { name: '浏览文件' })).toBeVisible()
    choose(image('cat.png'))
    expect(submit()).toBeDisabled()
    fireEvent.change(screen.getByLabelText('内容标签'), { target: { value: '橙猫、举爪' } })
    fireEvent.click(submit())
    await waitFor(() => expect(hoisted.postMock).toHaveBeenCalledOnce())
    expect(JSON.parse(String(hoisted.postMock.mock.calls[0][1].body.get('tags_json')))).toEqual({
      emotion_tags: [], content_tags: ['橙猫', '举爪'],
    })
    expect(hoisted.recognize).not.toHaveBeenCalled()
  })

  it('AI 返回草稿后可修改，点击确认才上传', async () => {
    hoisted.recognize.mockResolvedValue({ emotion_tags: ['开心'], content_tags: ['猫'] })
    render(<EmojiUploadDialog open onOpenChange={vi.fn()} onSuccess={vi.fn()} />)
    choose(image('cat.png'))
    fireEvent.click(screen.getByRole('button', { name: '自动识别' }))
    await screen.findByRole('radio', { name: '视觉 A' })
    fireEvent.click(screen.getByRole('button', { name: '开始识别' }))
    await screen.findByDisplayValue('猫')
    expect(screen.getByLabelText('情绪标签')).toHaveValue('开心')
    expect(hoisted.postMock).not.toHaveBeenCalled()
    fireEvent.change(screen.getByLabelText('内容标签'), { target: { value: '橙猫' } })
    fireEvent.click(submit())
    await waitFor(() => expect(hoisted.postMock).toHaveBeenCalledOnce())
    expect(String(hoisted.postMock.mock.calls[0][1].body.get('tags_json'))).toContain('橙猫')
  })

  it.each([true, false])('重复图片仅在确认后覆盖：%s', async (overwrite) => {
    hoisted.postMock.mockRejectedValueOnce(new ApiError('重复图片', { status: 409 })).mockResolvedValue({})
    const onSuccess = vi.fn()
    render(<EmojiUploadDialog open onOpenChange={vi.fn()} onSuccess={onSuccess} />)
    choose(image('cat.png'))
    fireEvent.change(screen.getByLabelText('内容标签'), { target: { value: '猫' } })
    fireEvent.click(submit())
    await screen.findByRole('alertdialog')
    expect(hoisted.postMock.mock.calls[0][1].body.has('overwrite')).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: overwrite ? '确认覆盖' : '跳过这张' }))
    await waitFor(() => expect(hoisted.toastMock).toHaveBeenCalled())
    expect(hoisted.postMock).toHaveBeenCalledTimes(overwrite ? 2 : 1)
    expect(onSuccess).toHaveBeenCalledTimes(overwrite ? 1 : 0)
    if (overwrite) expect(hoisted.postMock.mock.calls[1][1].body.get('overwrite')).toBe('true')
  })

  it.each([true, false])('同批后续重复表情沿用勾选的处理方式：覆盖=%s', async (overwrite) => {
    hoisted.postMock.mockImplementation(async (_, { body }) => {
      if (!body.has('overwrite')) throw new ApiError('重复图片', { status: 409 })
      return {}
    })
    render(<EmojiUploadDialog open onOpenChange={vi.fn()} onSuccess={vi.fn()} />)
    choose(image('one.png'), image('two.png'))
    fireEvent.click(screen.getByRole('button', { name: '追加内容标签' }))
    fireEvent.change(screen.getByLabelText('追加的内容标签'), { target: { value: '猫' } })
    fireEvent.click(screen.getByRole('button', { name: '追加到全部' }))
    fireEvent.click(submit())
    fireEvent.click(await screen.findByRole('checkbox', { name: '应用到全部重复表情' }))
    fireEvent.click(screen.getByRole('button', { name: overwrite ? '确认覆盖' : '跳过这张' }))
    await waitFor(() => expect(hoisted.toastMock).toHaveBeenCalled())
    expect(hoisted.postMock).toHaveBeenCalledTimes(overwrite ? 4 : 2)
    hoisted.postMock.mockResolvedValue({})
  })

  it.each(['内容标签', '情绪标签'])('整批追加%s时保留原标签并去重', async (label) => {
    render(<EmojiUploadDialog open onOpenChange={vi.fn()} onSuccess={vi.fn()} />)
    choose(image('one.png'), image('two.png'))
    fireEvent.change(screen.getByLabelText(label), { target: { value: '原有' } })
    fireEvent.click(screen.getByRole('button', { name: `追加${label}` }))
    const input = screen.getByLabelText(`追加的${label}`)
    fireEvent.change(input, { target: { value: '一、二、三、四、五' } })
    expect(screen.getByRole('button', { name: '追加到全部' })).toBeEnabled()
    fireEvent.change(input, { target: { value: '原有、共同、共同' } })
    fireEvent.click(screen.getByRole('button', { name: '追加到全部' }))
    await waitFor(() => expect(screen.getByLabelText(label)).toHaveValue('原有、共同'))
    fireEvent.click(screen.getByRole('button', { name: /编辑第 2 张/ }))
    expect(screen.getByLabelText(label)).toHaveValue('原有、共同')
    expect(screen.getByLabelText(label === '内容标签' ? '情绪标签' : '内容标签')).toHaveValue('')
  })

  it('可给已有五个标签的表情继续追加长标签', async () => {
    render(<EmojiUploadDialog open onOpenChange={vi.fn()} onSuccess={vi.fn()} />)
    choose(image('one.png'), image('two.png'))
    const original = '一、二、三、四、五'
    fireEvent.change(screen.getByLabelText('内容标签'), { target: { value: original } })
    const longTag = '长'.repeat(21)
    fireEvent.click(screen.getByRole('button', { name: '追加内容标签' }))
    fireEvent.change(screen.getByLabelText('追加的内容标签'), { target: { value: longTag } })
    fireEvent.click(screen.getByRole('button', { name: '追加到全部' }))
    fireEvent.click(screen.getByRole('button', { name: /编辑第 1 张/ }))
    expect(screen.getByLabelText('内容标签')).toHaveValue(original + '、' + longTag)
    expect(submit()).toBeEnabled()
  })

  it('只锁定正在识别的图片，排队时手改的字段优先于模型结果', async () => {
    let finish!: () => void
    hoisted.recognize.mockImplementation(() => new Promise((resolve) => {
      finish = () => resolve({ emotion_tags: ['开心'], content_tags: ['猫'] })
    }))
    render(<EmojiUploadDialog open onOpenChange={vi.fn()} onSuccess={vi.fn()} />)
    choose(image('one.png'), image('two.png'))
    fireEvent.click(screen.getByRole('button', { name: '识别全部' }))
    await screen.findByRole('radio', { name: '视觉 A' })
    fireEvent.click(screen.getByRole('button', { name: '开始识别' }))
    expect(screen.getByLabelText('内容标签')).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: /编辑第 2 张/ }))
    expect(screen.getByLabelText('内容标签')).toBeEnabled()
    fireEvent.change(screen.getByLabelText('内容标签'), { target: { value: '橙猫' } })
    finish()
    await waitFor(() => expect(screen.getByLabelText('内容标签')).toBeDisabled())
    fireEvent.click(screen.getByRole('button', { name: /编辑第 1 张/ }))
    expect(screen.getByLabelText('内容标签')).toBeEnabled()
    finish()
    await waitFor(() => expect(screen.getByRole('button', { name: '识别全部' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: /编辑第 2 张/ }))
    expect(screen.getByLabelText('内容标签')).toHaveValue('橙猫')
    expect(screen.getByLabelText('情绪标签')).toHaveValue('开心')
  })

  it('单张可记住指定模型，后续整批识别复用该选择', async () => {
    hoisted.recognize.mockResolvedValue({ emotion_tags: ['开心'], content_tags: ['猫'] })
    render(<EmojiUploadDialog open onOpenChange={vi.fn()} onSuccess={vi.fn()} />)
    choose(image('one.png'), image('two.png'))
    fireEvent.click(screen.getByRole('button', { name: '自动识别' }))
    fireEvent.click(await screen.findByRole('radio', { name: '视觉 B' }))
    fireEvent.click(screen.getByRole('checkbox', { name: '本批后续识别均用此模型' }))
    fireEvent.click(screen.getByRole('button', { name: '开始识别' }))
    await screen.findByDisplayValue('猫')
    fireEvent.click(screen.getByRole('button', { name: '识别全部' }))
    await waitFor(() => expect(hoisted.recognize).toHaveBeenCalledTimes(3))
    expect(hoisted.recognize.mock.calls.map(([, , model]) => model)).toEqual(['视觉 B', '视觉 B', '视觉 B'])
  })

  it('索引请求完成后推进进度，整批结束刷新表情列表', async () => {
    let finish!: () => void
    hoisted.postMock.mockImplementation(() => new Promise<void>((resolve) => { finish = resolve }))
    const refresh = vi.fn()
    render(<EmojiUploadDialog open onOpenChange={vi.fn()} onSuccess={refresh} />)
    choose(image('one.png'), image('two.png'))
    fireEvent.click(screen.getByRole('button', { name: '追加内容标签' }))
    fireEvent.change(screen.getByLabelText('追加的内容标签'), { target: { value: '麦麦' } })
    fireEvent.click(screen.getByRole('button', { name: '追加到全部' }))
    fireEvent.click(submit())
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '0')
    finish()
    await waitFor(() => expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '1'))
    expect(refresh).not.toHaveBeenCalled()
    finish()
    await waitFor(() => expect(refresh).toHaveBeenCalledOnce())
    hoisted.postMock.mockResolvedValue({})
  })

  it('同批文件独立编辑，重试只提交失败文件', async () => {
    hoisted.postMock.mockResolvedValueOnce({}).mockRejectedValueOnce(new Error('上传失败')).mockResolvedValueOnce({})
    render(<EmojiUploadDialog open onOpenChange={vi.fn()} onSuccess={vi.fn()} />)
    choose(image('one.png'), image('two.png'))
    fireEvent.change(screen.getByLabelText('情绪标签'), { target: { value: '开心' } })
    fireEvent.click(screen.getByRole('button', { name: '编辑第 2 张：two.png' }))
    fireEvent.change(screen.getByLabelText('内容标签'), { target: { value: '猫' } })
    fireEvent.click(submit())
    await screen.findByText('已上传 1 张，还有 1 张需要重试')
    expect(screen.getByLabelText('内容标签')).toHaveValue('猫')
    fireEvent.click(submit())
    await waitFor(() => expect(hoisted.postMock).toHaveBeenCalledTimes(3))
    expect((hoisted.postMock.mock.calls[2][1].body.get('file') as File).name).toBe('two.png')
  })

  it('拒绝超量文件，识别出错保留已填写内容', async () => {
    render(<EmojiUploadDialog open onOpenChange={vi.fn()} onSuccess={vi.fn()} />)
    choose(...Array.from({ length: 21 }, (_, i) => image(i + '.png')))
    expect(screen.getByRole('alert')).toHaveTextContent('最多选择 20 张')
    choose(image('one.png'))
    fireEvent.change(screen.getByLabelText('情绪标签'), { target: { value: '惊讶' } })
    hoisted.recognize.mockRejectedValue(new Error('视觉模型不可用'))
    fireEvent.click(screen.getByRole('button', { name: '自动识别' }))
    await screen.findByRole('radio', { name: '视觉 A' })
    fireEvent.click(screen.getByRole('button', { name: '开始识别' }))
    await screen.findByText('视觉模型不可用')
    expect(screen.getByLabelText('情绪标签')).toHaveValue('惊讶')
  })
})
