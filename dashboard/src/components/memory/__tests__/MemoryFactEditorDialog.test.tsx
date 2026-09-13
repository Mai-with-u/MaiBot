import { useState } from 'react'

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { MemoryFactEditorDialog } from '../MemoryFactEditorDialog'
import { useToast } from '@/hooks/use-toast'
import {
  createMemoryFact,
  updateMemoryFact,
  type MemoryFactActionPayload,
  type MemoryFactWritePayload,
  type MemoryRecordPayload,
} from '@/lib/memory-api'

const toastMock = vi.hoisted(() => vi.fn())

vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: toastMock }) }))

vi.mock('@/lib/memory-api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/memory-api')>()
  return {
    ...actual,
    createMemoryFact: vi.fn(),
    updateMemoryFact: vi.fn(),
  }
})

const createFactMock = vi.mocked(createMemoryFact)
const updateFactMock = vi.mocked(updateMemoryFact)

afterEach(() => {
  cleanup()
})

beforeEach(() => {
  if (!Element.prototype.hasPointerCapture) {
    Element.prototype.hasPointerCapture = () => false
  }
  if (!Element.prototype.setPointerCapture) {
    Element.prototype.setPointerCapture = () => {}
  }
  if (!Element.prototype.releasePointerCapture) {
    Element.prototype.releasePointerCapture = () => {}
  }

  createFactMock.mockResolvedValue({
    success: true,
    claim: { claim_id: 'fact-new' },
    refresh_queued: true,
  })
  updateFactMock.mockResolvedValue({
    success: true,
    claim: { claim_id: 'fact-1' },
    replaced: true,
    refresh_queued: true,
  })
})

/** 构造一条可编辑的事实记录 */
function makeFact(
  metadata: Record<string, unknown> = {},
  overrides: Partial<MemoryRecordPayload> = {},
): MemoryRecordPayload {
  return {
    type: 'fact',
    id: 'fact-1',
    title: '饮品偏好: 咖啡',
    summary: 'person · person-1',
    source: 'person-1',
    status: 'active',
    metadata: {
      scope_type: 'person',
      scope_id: 'person-1',
      fact_key: 'favorite_drink',
      value_text: '咖啡',
      polarity: 'positive',
      cardinality: 'single',
      stability: 'stable',
      profile_section: 'interaction_preferences',
      authority: 'manual',
      confidence: 0.9,
      ...metadata,
    },
    ...overrides,
  }
}

/** 渲染对话框，返回打开/提交回调桩 */
function renderDialog(props: Partial<Parameters<typeof MemoryFactEditorDialog>[0]> = {}) {
  const onOpenChange = vi.fn()
  const onSubmit = vi.fn()
  render(
    <MemoryFactEditorDialog
      open
      onOpenChange={onOpenChange}
      record={null}
      saving={false}
      onSubmit={onSubmit}
      {...props}
    />,
  )
  return { onOpenChange, onSubmit }
}

/** 对齐 MemoryRecordsTab 的保存链路：调用事实接口并弹出 toast */
function FactEditorHost({ record }: { record: MemoryRecordPayload | null }) {
  const { toast } = useToast()
  const [open, setOpen] = useState(true)
  const [saving, setSaving] = useState(false)

  const handleSubmit = async (payload: MemoryFactWritePayload) => {
    setSaving(true)
    try {
      const response = record
        ? await updateMemoryFact(record.id, payload)
        : await createMemoryFact(payload)
      if (!response.success) {
        throw new Error(response.error || '事实保存失败')
      }
      setOpen(false)
      toast({
        title: response.replaced ? '事实已修订' : '事实已保存',
        description: response.refresh_queued ? '相关人物画像已进入刷新队列。' : undefined,
      })
    } catch (error) {
      toast({
        title: '保存事实失败',
        description: error instanceof Error ? error.message : String(error),
        variant: 'destructive',
      })
    } finally {
      setSaving(false)
    }
  }

  return (
    <MemoryFactEditorDialog
      open={open}
      onOpenChange={setOpen}
      record={record}
      saving={saving}
      onSubmit={(payload) => {
        void handleSubmit(payload)
      }}
    />
  )
}

/** 填写创建所需的三个必填字段 */
async function fillRequired(
  user: ReturnType<typeof userEvent.setup>,
  values: { scopeId?: string; factKey?: string; valueText?: string } = {},
) {
  await user.type(screen.getByLabelText('归属 ID'), values.scopeId ?? 'person-1')
  await user.type(screen.getByLabelText('事实键'), values.factKey ?? 'favorite_drink')
  await user.type(screen.getByLabelText('事实内容'), values.valueText ?? '咖啡')
}

/** 打开指定下拉并点选选项 */
async function chooseSelect(
  user: ReturnType<typeof userEvent.setup>,
  label: string,
  option: string,
) {
  await user.click(screen.getByLabelText(label))
  await user.click(await screen.findByRole('option', { name: option }))
}

describe('MemoryFactEditorDialog 打开与回填', () => {
  it('open 为 false 时不渲染对话框内容', () => {
    renderDialog({ open: false })
    expect(screen.queryByText('新增结构化事实')).not.toBeInTheDocument()
    expect(screen.queryByText('编辑结构化事实')).not.toBeInTheDocument()
  })

  it('打开创建：展示默认值，归属可编辑，保存禁用', () => {
    renderDialog()
    expect(screen.getByText('新增结构化事实')).toBeInTheDocument()
    expect(
      screen.getByText('内容变化会生成新版本并保留旧事实的状态记录，分类变化会直接更新当前 claim。'),
    ).toBeInTheDocument()
    expect(screen.getByLabelText('归属类型')).toBeEnabled()
    expect(screen.getByLabelText('归属 ID')).toBeEnabled()
    expect(screen.getByLabelText('归属 ID')).toHaveValue('')
    expect(screen.getByLabelText('事实键')).toHaveValue('')
    expect(screen.getByLabelText('事实内容')).toHaveValue('')
    expect(screen.getByLabelText('归属类型')).toHaveTextContent('人物')
    expect(screen.getByLabelText('画像分类')).toHaveTextContent('稳定了解')
    expect(screen.getByLabelText('稳定性')).toHaveTextContent('稳定')
    expect(screen.getByLabelText('取值方式')).toHaveTextContent('多值集合')
    expect(screen.getByLabelText('极性')).toHaveTextContent('肯定')
    expect(screen.getByLabelText('信息来源级别')).toHaveTextContent('人工维护')
    expect(screen.getByLabelText('置信度')).toHaveValue(1)
    expect(screen.getByLabelText('修改原因')).toHaveValue('knowledge_base_fact_create')
    expect(screen.getByRole('button', { name: '保存事实' })).toBeDisabled()
  })

  it('打开编辑：回填记录字段，归属类型和 ID 禁用', () => {
    renderDialog({ record: makeFact() })
    expect(screen.getByText('编辑结构化事实')).toBeInTheDocument()
    expect(screen.getByLabelText('归属类型')).toBeDisabled()
    expect(screen.getByLabelText('归属 ID')).toBeDisabled()
    expect(screen.getByLabelText('归属 ID')).toHaveValue('person-1')
    expect(screen.getByLabelText('事实键')).toHaveValue('favorite_drink')
    expect(screen.getByLabelText('事实内容')).toHaveValue('咖啡')
    expect(screen.getByLabelText('归属类型')).toHaveTextContent('人物')
    expect(screen.getByLabelText('画像分类')).toHaveTextContent('相处偏好')
    expect(screen.getByLabelText('稳定性')).toHaveTextContent('稳定')
    expect(screen.getByLabelText('取值方式')).toHaveTextContent('单值')
    expect(screen.getByLabelText('极性')).toHaveTextContent('肯定')
    expect(screen.getByLabelText('信息来源级别')).toHaveTextContent('人工维护')
    expect(screen.getByLabelText('置信度')).toHaveValue(0.9)
    expect(screen.getByLabelText('修改原因')).toHaveValue('knowledge_base_fact_update')
    expect(screen.getByRole('button', { name: '保存事实' })).toBeEnabled()
  })

  it('回填聊天流、否定、时效性和用户直接陈述', () => {
    renderDialog({
      record: makeFact({
        scope_type: 'chat',
        scope_id: 'chat-9',
        polarity: 'negative',
        cardinality: 'set',
        stability: 'temporal',
        profile_section: 'identity_settings',
        authority: 'direct_user',
        confidence: 0,
      }),
    })
    expect(screen.getByLabelText('归属类型')).toHaveTextContent('聊天流')
    expect(screen.getByLabelText('归属 ID')).toHaveValue('chat-9')
    expect(screen.getByLabelText('极性')).toHaveTextContent('否定')
    expect(screen.getByLabelText('取值方式')).toHaveTextContent('多值集合')
    expect(screen.getByLabelText('稳定性')).toHaveTextContent('时效性')
    expect(screen.getByLabelText('画像分类')).toHaveTextContent('身份设定')
    expect(screen.getByLabelText('信息来源级别')).toHaveTextContent('用户直接陈述')
    expect(screen.getByLabelText('置信度')).toHaveValue(0)
  })

  it('回填不确定稳定性与可信导入来源', () => {
    renderDialog({
      record: makeFact({
        stability: 'uncertain',
        authority: 'imported',
        profile_section: 'relationship_settings',
      }),
    })
    expect(screen.getByLabelText('稳定性')).toHaveTextContent('不确定')
    expect(screen.getByLabelText('信息来源级别')).toHaveTextContent('可信导入')
    expect(screen.getByLabelText('画像分类')).toHaveTextContent('关系设定')
  })

  it('回填摘要推导来源', () => {
    renderDialog({
      record: makeFact({
        authority: 'summary_derived',
        profile_section: 'recent_interactions',
      }),
    })
    expect(screen.getByLabelText('信息来源级别')).toHaveTextContent('摘要推导')
    expect(screen.getByLabelText('画像分类')).toHaveTextContent('近期互动')
  })

  it('未知枚举和缺失字段回退到创建默认值', () => {
    renderDialog({
      record: makeFact(
        {},
        {
          metadata: {
            scope_type: 'group',
            polarity: 'neutral',
            cardinality: 'multi',
            stability: 'volatile',
            authority: 'system',
          },
        },
      ),
    })
    expect(screen.getByLabelText('归属类型')).toHaveTextContent('人物')
    expect(screen.getByLabelText('归属 ID')).toHaveValue('')
    expect(screen.getByLabelText('事实键')).toHaveValue('')
    expect(screen.getByLabelText('事实内容')).toHaveValue('')
    expect(screen.getByLabelText('极性')).toHaveTextContent('肯定')
    expect(screen.getByLabelText('取值方式')).toHaveTextContent('多值集合')
    expect(screen.getByLabelText('稳定性')).toHaveTextContent('稳定')
    expect(screen.getByLabelText('画像分类')).toHaveTextContent('稳定了解')
    expect(screen.getByLabelText('信息来源级别')).toHaveTextContent('人工维护')
    expect(screen.getByLabelText('置信度')).toHaveValue(1)
    expect(screen.getByRole('button', { name: '保存事实' })).toBeDisabled()
  })

  it('记录缺少 metadata 时按空对象初始化', () => {
    renderDialog({
      record: makeFact({}, { metadata: undefined as unknown as Record<string, unknown> }),
    })
    expect(screen.getByText('编辑结构化事实')).toBeInTheDocument()
    expect(screen.getByLabelText('修改原因')).toHaveValue('knowledge_base_fact_update')
    expect(screen.getByLabelText('归属 ID')).toHaveValue('')
    expect(screen.getByRole('button', { name: '保存事实' })).toBeDisabled()
  })
})

describe('MemoryFactEditorDialog 校验与取消', () => {
  it('只填写部分字段时保存保持禁用，且点击不会提交', async () => {
    const user = userEvent.setup()
    const { onSubmit } = renderDialog()

    await user.type(screen.getByLabelText('归属 ID'), 'person-1')
    expect(screen.getByRole('button', { name: '保存事实' })).toBeDisabled()

    await user.type(screen.getByLabelText('事实键'), 'favorite_drink')
    expect(screen.getByRole('button', { name: '保存事实' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: '保存事实' }))
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('空白字符不能通过必填校验', async () => {
    const user = userEvent.setup()
    const { onSubmit } = renderDialog()
    await fillRequired(user, { scopeId: '   ', factKey: '   ', valueText: '   ' })
    expect(screen.getByRole('button', { name: '保存事实' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: '保存事实' }))
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('三个必填字段都有内容后允许保存', async () => {
    const user = userEvent.setup()
    renderDialog()
    expect(screen.getByRole('button', { name: '保存事实' })).toBeDisabled()
    await fillRequired(user)
    expect(screen.getByRole('button', { name: '保存事实' })).toBeEnabled()
  })

  it('点击取消调用 onOpenChange(false)', async () => {
    const user = userEvent.setup()
    const { onOpenChange } = renderDialog()
    await user.click(screen.getByRole('button', { name: '取消' }))
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it('保存中禁用取消和保存，并显示加载图标', () => {
    renderDialog({ record: makeFact(), saving: true })
    const saveButton = screen.getByRole('button', { name: '保存事实' })
    expect(screen.getByRole('button', { name: '取消' })).toBeDisabled()
    expect(saveButton).toBeDisabled()
    expect(saveButton.querySelector('.animate-spin')).not.toBeNull()
  })
})

describe('MemoryFactEditorDialog 提交', () => {
  it('创建时提交默认分类字段', async () => {
    const user = userEvent.setup()
    const { onSubmit } = renderDialog()
    await fillRequired(user)
    await user.click(screen.getByRole('button', { name: '保存事实' }))
    expect(onSubmit).toHaveBeenCalledWith({
      scope_type: 'person',
      scope_id: 'person-1',
      fact_key: 'favorite_drink',
      value_text: '咖啡',
      polarity: 'positive',
      cardinality: 'set',
      stability: 'stable',
      profile_section: 'stable_facts',
      authority: 'manual',
      confidence: 1,
      reason: 'knowledge_base_fact_create',
      updated_by: 'knowledge_base',
    })
  })

  it('修改全部分类字段后把最新表单交给 onSubmit', async () => {
    const user = userEvent.setup()
    const { onSubmit } = renderDialog()
    await fillRequired(user, { scopeId: 'chat-2', factKey: 'mood', valueText: '开心' })
    await chooseSelect(user, '归属类型', '聊天流')
    await chooseSelect(user, '画像分类', '不确定信息')
    await chooseSelect(user, '稳定性', '不确定')
    await chooseSelect(user, '取值方式', '单值')
    await chooseSelect(user, '极性', '否定')
    await chooseSelect(user, '信息来源级别', '摘要推导')
    fireEvent.change(screen.getByLabelText('置信度'), { target: { value: '0.55' } })
    const reasonInput = screen.getByLabelText('修改原因')
    await user.clear(reasonInput)
    await user.type(reasonInput, 'manual_fix')

    await user.click(screen.getByRole('button', { name: '保存事实' }))
    expect(onSubmit).toHaveBeenCalledWith({
      scope_type: 'chat',
      scope_id: 'chat-2',
      fact_key: 'mood',
      value_text: '开心',
      polarity: 'negative',
      cardinality: 'single',
      stability: 'uncertain',
      profile_section: 'uncertain_notes',
      authority: 'summary_derived',
      confidence: 0.55,
      reason: 'manual_fix',
      updated_by: 'knowledge_base',
    })
  })

  it('编辑时提交回填后的事实内容', async () => {
    const user = userEvent.setup()
    const { onSubmit } = renderDialog({ record: makeFact() })
    const valueInput = screen.getByLabelText('事实内容')
    await user.clear(valueInput)
    await user.type(valueInput, '绿茶')
    await user.click(screen.getByRole('button', { name: '保存事实' }))
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        scope_type: 'person',
        scope_id: 'person-1',
        fact_key: 'favorite_drink',
        value_text: '绿茶',
        profile_section: 'interaction_preferences',
        reason: 'knowledge_base_fact_update',
        updated_by: 'knowledge_base',
      }),
    )
  })

  it('创建成功：调用 createMemoryFact 并弹出保存 toast，随后关闭', async () => {
    const user = userEvent.setup()
    render(<FactEditorHost record={null} />)
    await fillRequired(user)
    await user.click(screen.getByRole('button', { name: '保存事实' }))

    await waitFor(() => {
      expect(createFactMock).toHaveBeenCalledWith(
        expect.objectContaining({
          scope_id: 'person-1',
          fact_key: 'favorite_drink',
          value_text: '咖啡',
        }),
      )
    })
    expect(toastMock).toHaveBeenCalledWith({
      title: '事实已保存',
      description: '相关人物画像已进入刷新队列。',
    })
    expect(updateFactMock).not.toHaveBeenCalled()
    await waitFor(() => {
      expect(screen.queryByText('新增结构化事实')).not.toBeInTheDocument()
    })
  })

  it('创建失败：接口返回错误时弹出失败 toast 并保持打开', async () => {
    const user = userEvent.setup()
    createFactMock.mockResolvedValue({ success: false, error: '范围不存在' })
    render(<FactEditorHost record={null} />)
    await fillRequired(user)
    await user.click(screen.getByRole('button', { name: '保存事实' }))

    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '保存事实失败',
        description: '范围不存在',
        variant: 'destructive',
      })
    })
    expect(screen.getByText('新增结构化事实')).toBeInTheDocument()
  })

  it('创建失败：接口未给 error 时使用默认失败文案', async () => {
    const user = userEvent.setup()
    createFactMock.mockResolvedValue({ success: false })
    render(<FactEditorHost record={null} />)
    await fillRequired(user)
    await user.click(screen.getByRole('button', { name: '保存事实' }))

    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '保存事实失败',
        description: '事实保存失败',
        variant: 'destructive',
      })
    })
  })

  it('编辑成功：调用 updateMemoryFact 并弹出修订 toast', async () => {
    const user = userEvent.setup()
    render(<FactEditorHost record={makeFact()} />)
    const valueInput = screen.getByLabelText('事实内容')
    await user.clear(valueInput)
    await user.type(valueInput, '绿茶')
    await user.click(screen.getByRole('button', { name: '保存事实' }))

    await waitFor(() => {
      expect(updateFactMock).toHaveBeenCalledWith(
        'fact-1',
        expect.objectContaining({ value_text: '绿茶', profile_section: 'interaction_preferences' }),
      )
    })
    expect(toastMock).toHaveBeenCalledWith({
      title: '事实已修订',
      description: '相关人物画像已进入刷新队列。',
    })
    expect(createFactMock).not.toHaveBeenCalled()
  })

  it('编辑失败：接口抛错时弹出失败 toast', async () => {
    const user = userEvent.setup()
    updateFactMock.mockRejectedValue(new Error('网络超时'))
    render(<FactEditorHost record={makeFact()} />)
    await user.click(screen.getByRole('button', { name: '保存事实' }))

    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '保存事实失败',
        description: '网络超时',
        variant: 'destructive',
      })
    })
    expect(screen.getByText('编辑结构化事实')).toBeInTheDocument()
  })

  it('提交进行中禁用按钮，完成后恢复或关闭', async () => {
    const user = userEvent.setup()
    let resolveCreate: (value: MemoryFactActionPayload) => void = () => {}
    createFactMock.mockReturnValue(
      new Promise((resolve) => {
        resolveCreate = resolve
      }),
    )
    render(<FactEditorHost record={null} />)
    await fillRequired(user)
    await user.click(screen.getByRole('button', { name: '保存事实' }))

    expect(screen.getByRole('button', { name: '取消' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '保存事实' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '保存事实' }).querySelector('.animate-spin')).not.toBeNull()

    resolveCreate({ success: true, claim: { claim_id: 'fact-new' }, refresh_queued: false })
    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith({
        title: '事实已保存',
        description: undefined,
      })
    })
  })
})
