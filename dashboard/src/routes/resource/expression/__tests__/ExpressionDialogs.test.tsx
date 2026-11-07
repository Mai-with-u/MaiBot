/**
 * ExpressionDialogs 行为测试：
 * 覆盖编辑对话框的信息展示、预填与保存 / 创建对话框校验与提交 /
 * 三个确认对话框回调。
 */
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { createExpression, updateExpression } from '@/lib/expression-api'

import {
  BatchDeleteConfirmDialog,
  ClearChatExpressionsConfirmDialog,
  DeleteConfirmDialog,
  ExpressionCreateDialog,
  ExpressionEditDialog,
} from '../ExpressionDialogs'

import type { ChatInfo, Expression } from '@/types/expression'

const toastMock = vi.hoisted(() => vi.fn())

vi.mock('@/hooks/use-toast', () => ({ useToast: () => ({ toast: toastMock }) }))

vi.mock('@/lib/expression-api', () => ({
  createExpression: vi.fn(),
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
        description: '请填写必填字段：情境、风格和聊天',
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
      <ExpressionCreateDialog
        open
        onOpenChange={vi.fn()}
        chatList={chatList}
        onSuccess={onSuccess}
      />
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

  it('打开时预填可编辑字段，并展示原详情中的记录信息', () => {
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
    expect(screen.getByText('7')).toBeInTheDocument()
    expect(screen.getByText('未人工精选')).toBeInTheDocument()
    expect(screen.getByText('-')).toBeInTheDocument()
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
    render(<BatchDeleteConfirmDialog open onOpenChange={vi.fn()} onConfirm={onConfirm} count={5} />)
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
