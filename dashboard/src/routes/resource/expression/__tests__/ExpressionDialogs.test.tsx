/**
 * ExpressionDialogs 行为测试：
 * 覆盖详情对话框渲染 / 创建对话框校验与提交 / 编辑对话框预填与保存 /
 * 三个确认对话框回调 / 旧版导入对话框的预览、映射与导入链路。
 */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  createExpression,
  getExpressionChatTargets,
  importLegacyExpressions,
  previewLegacyExpressionImport,
  previewLegacyExpressionImportFile,
  updateExpression,
} from '@/lib/expression-api'

import {
  BatchDeleteConfirmDialog,
  ClearChatExpressionsConfirmDialog,
  DeleteConfirmDialog,
  ExpressionCreateDialog,
  ExpressionDetailDialog,
  ExpressionEditDialog,
  LegacyExpressionImportDialog,
} from '../ExpressionDialogs'

import type {
  ChatInfo,
  Expression,
  LegacyExpressionGroupPreview,
  LegacyExpressionImportPreviewResponse,
} from '@/types/expression'

const toastMock = vi.hoisted(() => vi.fn())

vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: toastMock }) }))

vi.mock('@/lib/expression-api', () => ({
  createExpression: vi.fn(),
  getExpressionChatTargets: vi.fn(),
  importLegacyExpressions: vi.fn(),
  previewLegacyExpressionImport: vi.fn(),
  previewLegacyExpressionImportFile: vi.fn(),
  updateExpression: vi.fn(),
}))

afterEach(() => {
  cleanup()
})

beforeEach(() => {
  toastMock.mockClear()
  // Radix Select 在 jsdom 下需要 pointer-capture 相关桩
  if (!Element.prototype.hasPointerCapture) {
    Element.prototype.hasPointerCapture = () => false
  }
  if (!Element.prototype.setPointerCapture) {
    Element.prototype.setPointerCapture = () => {}
  }
  if (!Element.prototype.releasePointerCapture) {
    Element.prototype.releasePointerCapture = () => {}
  }
})

function makeExpression(overrides: Partial<Expression> = {}): Expression {
  return {
    id: 7,
    situation: '被夸奖时',
    style: '害羞回应',
    last_active_time: 1_710_000_000,
    chat_id: 'chat-1',
    chat_name: null,
    create_date: null,
    checked: false,
    modified_by: null,
    ...overrides,
  }
}

function makeChat(id: string, name: string): ChatInfo {
  return {
    chat_id: id,
    chat_name: name,
    platform: 'qq',
    account_id: null,
    is_group: false,
    use_expression: true,
    enable_learning: true,
  }
}

describe('ExpressionDetailDialog', () => {
  it('expression 为 null 时不渲染对话框', () => {
    render(
      <ExpressionDetailDialog
        expression={null}
        open
        onOpenChange={vi.fn()}
        chatNameMap={new Map()}
      />
    )
    expect(screen.queryByText('表达方式详情')).not.toBeInTheDocument()
  })

  it('渲染情境、风格、记录ID、chatNameMap 回退名称与未审核状态', () => {
    render(
      <ExpressionDetailDialog
        expression={makeExpression()}
        open
        onOpenChange={vi.fn()}
        chatNameMap={new Map([['chat-1', '测试群']])}
      />
    )
    expect(screen.getByText('被夸奖时')).toBeInTheDocument()
    expect(screen.getByText('害羞回应')).toBeInTheDocument()
    expect(screen.getByText('7')).toBeInTheDocument()
    expect(screen.getByText('测试群')).toBeInTheDocument()
    expect(screen.getByText('未审核')).toBeInTheDocument()
    // create_date 为 null 时创建时间显示 '-'
    expect(screen.getByText('-')).toBeInTheDocument()
  })

  it('checked=true 时显示已通过审核', () => {
    render(
      <ExpressionDetailDialog
        expression={makeExpression({ checked: true })}
        open
        onOpenChange={vi.fn()}
        chatNameMap={new Map()}
      />
    )
    expect(screen.getByText('已通过审核')).toBeInTheDocument()
  })

  it('点击关闭按钮回调 onOpenChange(false)', async () => {
    const user = userEvent.setup()
    const onOpenChange = vi.fn()
    render(
      <ExpressionDetailDialog
        expression={makeExpression()}
        open
        onOpenChange={onOpenChange}
        chatNameMap={new Map()}
      />
    )
    // 对话框存在两个「关闭」按钮（底部按钮与右上角 X），点击任意一个都应回调关闭
    const closeButtons = screen.getAllByRole('button', { name: '关闭' })
    await user.click(closeButtons[0])
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })
})

describe('ExpressionCreateDialog', () => {
  const chatList = [makeChat('chat-1', 'Chat 1')]

  it('必填字段缺失时弹验证失败提示且不调用创建接口', async () => {
    const user = userEvent.setup()
    render(
      <ExpressionCreateDialog open onOpenChange={vi.fn()} chatList={chatList} onSuccess={vi.fn()} />
    )
    await user.click(screen.getByRole('button', { name: '创建' }))
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '验证失败',
        // 现状特征化：源码文案中「聊天」误写为「聚天」
        description: '请填写必填字段：情境、风格和聚天',
        variant: 'destructive',
      })
    )
    expect(createExpression).not.toHaveBeenCalled()
  })

  it('填写完整后创建成功：调用接口、弹提示、重置表单并回调 onSuccess', async () => {
    const user = userEvent.setup()
    const onSuccess = vi.fn()
    vi.mocked(createExpression).mockResolvedValue(makeExpression())
    render(
      <ExpressionCreateDialog open onOpenChange={vi.fn()} chatList={chatList} onSuccess={onSuccess} />
    )

    await user.type(screen.getByLabelText(/情境/), '被感谢时')
    await user.type(screen.getByLabelText(/风格/), '谦虚回应')
    await user.click(screen.getByRole('combobox'))
    await user.click(await screen.findByRole('option', { name: 'Chat 1' }))
    await user.click(screen.getByRole('button', { name: '创建' }))

    await waitFor(() => {
      expect(createExpression).toHaveBeenCalledWith({
        situation: '被感谢时',
        style: '谦虚回应',
        chat_id: 'chat-1',
      })
    })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({ title: '创建成功', description: '表达方式已创建' })
    )
    expect(onSuccess).toHaveBeenCalledTimes(1)
    // 创建成功后表单被重置
    expect(screen.getByLabelText(/情境/)).toHaveValue('')
  })

  it('创建接口失败时弹创建失败提示', async () => {
    const user = userEvent.setup()
    vi.mocked(createExpression).mockRejectedValue(new Error('创建接口炸了'))
    render(
      <ExpressionCreateDialog open onOpenChange={vi.fn()} chatList={chatList} onSuccess={vi.fn()} />
    )

    await user.type(screen.getByLabelText(/情境/), '被感谢时')
    await user.type(screen.getByLabelText(/风格/), '谦虚回应')
    await user.click(screen.getByRole('combobox'))
    await user.click(await screen.findByRole('option', { name: 'Chat 1' }))
    await user.click(screen.getByRole('button', { name: '创建' }))

    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '创建失败',
          description: '创建接口炸了',
          variant: 'destructive',
        })
      )
    })
  })
})

describe('ExpressionEditDialog', () => {
  const chatList = [makeChat('chat-1', 'Chat 1')]

  it('expression 为 null 时不渲染对话框', () => {
    render(
      <ExpressionEditDialog
        expression={null}
        open
        onOpenChange={vi.fn()}
        chatList={chatList}
        onSuccess={vi.fn()}
      />
    )
    expect(screen.queryByText('编辑表达方式')).not.toBeInTheDocument()
  })

  it('打开时用 expression 预填输入框', () => {
    render(
      <ExpressionEditDialog
        expression={makeExpression()}
        open
        onOpenChange={vi.fn()}
        chatList={chatList}
        onSuccess={vi.fn()}
      />
    )
    expect(screen.getByLabelText('情境')).toHaveValue('被夸奖时')
    expect(screen.getByLabelText('风格')).toHaveValue('害羞回应')
  })

  it('修改情境后保存：调用更新接口并回调 onSuccess', async () => {
    const user = userEvent.setup()
    const onSuccess = vi.fn()
    vi.mocked(updateExpression).mockResolvedValue(makeExpression())
    render(
      <ExpressionEditDialog
        expression={makeExpression()}
        open
        onOpenChange={vi.fn()}
        chatList={chatList}
        onSuccess={onSuccess}
      />
    )

    const situationInput = screen.getByLabelText('情境')
    await user.clear(situationInput)
    await user.type(situationInput, '被调侃时')
    await user.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => {
      expect(updateExpression).toHaveBeenCalledWith(7, {
        situation: '被调侃时',
        style: '害羞回应',
        chat_id: 'chat-1',
      })
    })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({ title: '保存成功', description: '表达方式已更新' })
    )
    expect(onSuccess).toHaveBeenCalledTimes(1)
  })

  it('保存失败时弹保存失败提示且不回调 onSuccess', async () => {
    const user = userEvent.setup()
    const onSuccess = vi.fn()
    vi.mocked(updateExpression).mockRejectedValue(new Error('更新接口炸了'))
    render(
      <ExpressionEditDialog
        expression={makeExpression()}
        open
        onOpenChange={vi.fn()}
        chatList={chatList}
        onSuccess={onSuccess}
      />
    )

    await user.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '保存失败',
          description: '更新接口炸了',
          variant: 'destructive',
        })
      )
    })
    expect(onSuccess).not.toHaveBeenCalled()
  })
})

describe('确认对话框', () => {
  it('批量删除确认框展示数量并在确认时回调', async () => {
    const user = userEvent.setup()
    const onConfirm = vi.fn()
    render(
      <BatchDeleteConfirmDialog open onOpenChange={vi.fn()} onConfirm={onConfirm} count={5} />
    )
    expect(screen.getByText(/您即将删除 5 个表达方式/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '确认删除' }))
    expect(onConfirm).toHaveBeenCalledTimes(1)
  })

  it('清除确认框空名称时回退为当前聊天并在确认时回调', async () => {
    const user = userEvent.setup()
    const onConfirm = vi.fn(async () => {})
    render(
      <ClearChatExpressionsConfirmDialog
        open
        onOpenChange={vi.fn()}
        chatName=""
        onConfirm={onConfirm}
      />
    )
    expect(screen.getByText(/当前聊天/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '确认清除' }))
    expect(onConfirm).toHaveBeenCalledTimes(1)
  })

  it('单个删除确认框展示情境并在确认时回调', async () => {
    const user = userEvent.setup()
    const onConfirm = vi.fn(async () => {})
    render(
      <DeleteConfirmDialog
        expression={makeExpression()}
        open
        onOpenChange={vi.fn()}
        onConfirm={onConfirm}
      />
    )
    expect(screen.getByText(/被夸奖时/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '删除' }))
    expect(onConfirm).toHaveBeenCalledTimes(1)
  })
})

describe('LegacyExpressionImportDialog', () => {
  function makeGroup(
    overrides: Partial<LegacyExpressionGroupPreview> = {}
  ): LegacyExpressionGroupPreview {
    return {
      old_chat_id: 'old-1',
      expression_count: 3,
      platform: 'qq',
      target_id: '12345',
      chat_type: 'group',
      matched_session_id: 's1',
      matched_chat_name: '匹配群',
      matched: true,
      matched_sessions: [{ session_id: 's1', chat_name: '匹配群', account_id: null }],
      ...overrides,
    }
  }

  function makePreview(
    groups: LegacyExpressionGroupPreview[]
  ): LegacyExpressionImportPreviewResponse {
    return {
      success: true,
      db_path: '/data/legacy.db',
      total_count: 4,
      matched_count: 1,
      unmatched_count: 1,
      groups,
    }
  }

  function getFileInput(): HTMLInputElement {
    const input = document.querySelector('input[type="file"]')
    if (!(input instanceof HTMLInputElement)) {
      throw new Error('未找到文件输入框')
    }
    return input
  }

  function renderDialog(onSuccess = vi.fn(), onOpenChange = vi.fn()) {
    render(
      <LegacyExpressionImportDialog
        open
        onOpenChange={onOpenChange}
        chatList={[makeChat('chat-9', '备用聊天')]}
        onSuccess={onSuccess}
      />
    )
    return { onSuccess, onOpenChange }
  }

  beforeEach(() => {
    vi.mocked(getExpressionChatTargets).mockResolvedValue([makeChat('chat-9', '备用聊天')])
  })

  it('选择无本地路径的文件时走文件上传预览并渲染分组信息', async () => {
    vi.mocked(previewLegacyExpressionImportFile).mockResolvedValue(
      makePreview([
        makeGroup(),
        makeGroup({
          old_chat_id: 'old-2',
          expression_count: 1,
          platform: null,
          target_id: null,
          chat_type: null,
          matched_session_id: null,
          matched_chat_name: null,
          matched: false,
          matched_sessions: [],
        }),
      ])
    )
    renderDialog()
    await waitFor(() => expect(getExpressionChatTargets).toHaveBeenCalled())

    const file = new File(['sqlite'], 'legacy.db', { type: 'application/octet-stream' })
    fireEvent.change(getFileInput(), { target: { files: [file] } })

    await waitFor(() => expect(previewLegacyExpressionImportFile).toHaveBeenCalledWith(file))
    expect(previewLegacyExpressionImport).not.toHaveBeenCalled()
    // 统计信息
    expect(await screen.findByText('表达方式 4 条')).toBeInTheDocument()
    expect(screen.getByText('已匹配 1 组')).toBeInTheDocument()
    expect(screen.getByText('未匹配 1 组')).toBeInTheDocument()
    // 分组标签：平台:类型:目标 与未匹配提示
    expect(screen.getByText('qq:群:12345')).toBeInTheDocument()
    // 「匹配群」同时出现在自动匹配列与目标下拉的隐藏原生 select 中
    expect(screen.getAllByText('匹配群').length).toBeGreaterThan(0)
    expect(screen.getByText('未找到')).toBeInTheDocument()
  })

  it('确认导入：已匹配组映射到会话，未匹配组目标为 null，成功后关闭并回调', async () => {
    const user = userEvent.setup()
    vi.mocked(previewLegacyExpressionImportFile).mockResolvedValue(
      makePreview([
        makeGroup(),
        makeGroup({
          old_chat_id: 'old-2',
          matched_session_id: null,
          matched_chat_name: null,
          matched: false,
          matched_sessions: [],
        }),
      ])
    )
    vi.mocked(importLegacyExpressions).mockResolvedValue({
      success: true,
      message: '导入 3 条',
      imported_count: 3,
      skipped_count: 0,
      failed_count: 0,
      ignored_group_count: 1,
    })
    const { onSuccess, onOpenChange } = renderDialog()

    const file = new File(['sqlite'], 'legacy.db', { type: 'application/octet-stream' })
    fireEvent.change(getFileInput(), { target: { files: [file] } })
    await screen.findByText('表达方式 4 条')

    await user.click(screen.getByRole('button', { name: '确认导入' }))

    await waitFor(() => {
      expect(importLegacyExpressions).toHaveBeenCalledWith({
        db_path: '/data/legacy.db',
        mappings: [
          { old_chat_id: 'old-1', target_chat_id: 's1', target_chat_ids: [] },
          { old_chat_id: 'old-2', target_chat_id: null, target_chat_ids: [] },
        ],
      })
    })
    expect(toastMock).toHaveBeenCalledWith(
      expect.objectContaining({ title: '导入完成', description: '导入 3 条' })
    )
    expect(onSuccess).toHaveBeenCalledTimes(1)
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it('多匹配组默认导入到全部匹配项', async () => {
    const user = userEvent.setup()
    vi.mocked(previewLegacyExpressionImportFile).mockResolvedValue(
      makePreview([
        makeGroup({
          old_chat_id: 'old-3',
          matched_session_id: 's2',
          matched_sessions: [
            { session_id: 's2', chat_name: '群A', account_id: null },
            { session_id: 's3', chat_name: '群B', account_id: null },
          ],
        }),
      ])
    )
    vi.mocked(importLegacyExpressions).mockResolvedValue({
      success: true,
      message: 'ok',
      imported_count: 3,
      skipped_count: 0,
      failed_count: 0,
      ignored_group_count: 0,
    })
    renderDialog()

    const file = new File(['sqlite'], 'legacy.db', { type: 'application/octet-stream' })
    fireEvent.change(getFileInput(), { target: { files: [file] } })
    await screen.findByText('表达方式 4 条')
    // 多匹配时展示匹配数量
    expect(screen.getByText('2 个匹配')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '确认导入' }))

    await waitFor(() => {
      expect(importLegacyExpressions).toHaveBeenCalledWith({
        db_path: '/data/legacy.db',
        mappings: [{ old_chat_id: 'old-3', target_chat_id: null, target_chat_ids: ['s2', 's3'] }],
      })
    })
  })

  it('取消勾选分组后导入目标为 null', async () => {
    const user = userEvent.setup()
    vi.mocked(previewLegacyExpressionImportFile).mockResolvedValue(makePreview([makeGroup()]))
    vi.mocked(importLegacyExpressions).mockResolvedValue({
      success: true,
      message: 'ok',
      imported_count: 0,
      skipped_count: 0,
      failed_count: 0,
      ignored_group_count: 1,
    })
    renderDialog()

    const file = new File(['sqlite'], 'legacy.db', { type: 'application/octet-stream' })
    fireEvent.change(getFileInput(), { target: { files: [file] } })
    await screen.findByText('表达方式 4 条')

    // 行内勾选框默认勾选（自动匹配成功），取消后该组不导入
    await user.click(screen.getByRole('checkbox'))
    await user.click(screen.getByRole('button', { name: '确认导入' }))

    await waitFor(() => {
      expect(importLegacyExpressions).toHaveBeenCalledWith({
        db_path: '/data/legacy.db',
        mappings: [{ old_chat_id: 'old-1', target_chat_id: null, target_chat_ids: [] }],
      })
    })
  })

  it('带本地路径的文件（Electron）走 db_path 预览', async () => {
    vi.mocked(previewLegacyExpressionImport).mockResolvedValue(makePreview([makeGroup()]))
    renderDialog()

    const file = new File(['sqlite'], 'legacy.db', { type: 'application/octet-stream' })
    Object.defineProperty(file, 'path', { value: '/tmp/legacy.db' })
    fireEvent.change(getFileInput(), { target: { files: [file] } })

    await waitFor(() => {
      expect(previewLegacyExpressionImport).toHaveBeenCalledWith({ db_path: '/tmp/legacy.db' })
    })
    expect(previewLegacyExpressionImportFile).not.toHaveBeenCalled()
    // 输入框展示本地路径
    expect(screen.getByLabelText('旧数据库路径')).toHaveValue('/tmp/legacy.db')
  })

  it('预览失败时弹预览失败提示', async () => {
    vi.mocked(previewLegacyExpressionImportFile).mockRejectedValue(new Error('数据库损坏'))
    renderDialog()

    const file = new File(['sqlite'], 'legacy.db', { type: 'application/octet-stream' })
    fireEvent.change(getFileInput(), { target: { files: [file] } })

    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '预览失败',
          description: '数据库损坏',
          variant: 'destructive',
        })
      )
    })
  })

  it('导入失败时弹导入失败提示且不关闭对话框', async () => {
    const user = userEvent.setup()
    vi.mocked(previewLegacyExpressionImportFile).mockResolvedValue(makePreview([makeGroup()]))
    vi.mocked(importLegacyExpressions).mockRejectedValue(new Error('导入接口炸了'))
    const { onSuccess, onOpenChange } = renderDialog()

    const file = new File(['sqlite'], 'legacy.db', { type: 'application/octet-stream' })
    fireEvent.change(getFileInput(), { target: { files: [file] } })
    await screen.findByText('表达方式 4 条')

    await user.click(screen.getByRole('button', { name: '确认导入' }))

    await waitFor(() => {
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({
          title: '导入失败',
          description: '导入接口炸了',
          variant: 'destructive',
        })
      )
    })
    expect(onSuccess).not.toHaveBeenCalled()
    expect(onOpenChange).not.toHaveBeenCalled()
  })
})
