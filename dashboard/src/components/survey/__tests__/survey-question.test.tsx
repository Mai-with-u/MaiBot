/**
 * SurveyQuestion 单个问题渲染组件测试
 * 覆盖：标题/必填/描述/错误提示、各问题类型（单选/多选/文本/多行文本/评分/量表/下拉）的渲染与交互回调
 */
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { SurveyQuestion } from '../survey-question'
import type { SurveyQuestion as SurveyQuestionType } from '@/types/survey'

afterEach(() => {
  cleanup()
})

/** 构造问题定义，默认给定 id/title，type 与其余字段由用例覆盖 */
function makeQuestion(
  overrides: Partial<SurveyQuestionType> & { type: SurveyQuestionType['type'] }
): SurveyQuestionType {
  return {
    id: 'q1',
    title: '测试问题',
    ...overrides,
  }
}

const twoOptions = [
  { id: 'a', label: '选项A', value: 'a' },
  { id: 'b', label: '选项B', value: 'b' },
]

describe('SurveyQuestion 公共结构', () => {
  it('渲染标题、必填星号、描述与错误提示', () => {
    render(
      <SurveyQuestion
        question={makeQuestion({ type: 'text', title: '你的昵称', description: '请如实填写', required: true })}
        value={undefined}
        onChange={vi.fn()}
        error="此题为必填项"
      />
    )
    expect(screen.getByText('你的昵称')).toBeInTheDocument()
    expect(screen.getByText('*')).toBeInTheDocument()
    expect(screen.getByText('请如实填写')).toBeInTheDocument()
    expect(screen.getByText('此题为必填项')).toBeInTheDocument()
  })

  it('非必填且无错误时不渲染星号与错误文案', () => {
    render(
      <SurveyQuestion
        question={makeQuestion({ type: 'text', title: '选填问题' })}
        value={undefined}
        onChange={vi.fn()}
      />
    )
    expect(screen.queryByText('*')).not.toBeInTheDocument()
    expect(screen.queryByText('此题为必填项')).not.toBeInTheDocument()
  })

  it('未知问题类型显示不支持提示', () => {
    render(
      <SurveyQuestion
        question={makeQuestion({ type: 'unknown' as SurveyQuestionType['type'] })}
        value={undefined}
        onChange={vi.fn()}
      />
    )
    expect(screen.getByText('不支持的问题类型')).toBeInTheDocument()
  })
})

describe('SurveyQuestion 单选题', () => {
  it('渲染全部选项并在点击时回调选项值', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(
      <SurveyQuestion
        question={makeQuestion({ type: 'single', options: twoOptions })}
        value={undefined}
        onChange={onChange}
      />
    )
    expect(screen.getAllByRole('radio')).toHaveLength(2)
    await user.click(screen.getByRole('radio', { name: '选项A' }))
    expect(onChange).toHaveBeenCalledWith('a')
  })

  it('当前值对应的选项处于选中态', () => {
    render(
      <SurveyQuestion
        question={makeQuestion({ type: 'single', options: twoOptions })}
        value="b"
        onChange={vi.fn()}
      />
    )
    expect(screen.getByRole('radio', { name: '选项B' })).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByRole('radio', { name: '选项A' })).toHaveAttribute('aria-checked', 'false')
  })
})

describe('SurveyQuestion 多选题', () => {
  it('勾选追加值、取消勾选移除值', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(
      <SurveyQuestion
        question={makeQuestion({ type: 'multiple', options: twoOptions })}
        value={['a']}
        onChange={onChange}
      />
    )
    await user.click(screen.getByRole('checkbox', { name: '选项B' }))
    expect(onChange).toHaveBeenCalledWith(['a', 'b'])
    await user.click(screen.getByRole('checkbox', { name: '选项A' }))
    expect(onChange).toHaveBeenCalledWith([])
  })

  it('达到 maxSelections 时禁用未选中项并显示上限提示', () => {
    const threeOptions = [...twoOptions, { id: 'c', label: '选项C', value: 'c' }]
    render(
      <SurveyQuestion
        question={makeQuestion({ type: 'multiple', options: threeOptions, maxSelections: 2 })}
        value={['a', 'b']}
        onChange={vi.fn()}
      />
    )
    // 未选中的第三项被禁用，已选中的仍可取消
    expect(screen.getByRole('checkbox', { name: '选项C' })).toBeDisabled()
    expect(screen.getByRole('checkbox', { name: '选项A' })).not.toBeDisabled()
    expect(screen.getByText('最多选择 2 项')).toBeInTheDocument()
  })
})

describe('SurveyQuestion 文本题', () => {
  it('输入时回调完整文本', () => {
    const onChange = vi.fn()
    render(
      <SurveyQuestion
        question={makeQuestion({ type: 'text', placeholder: '请输入昵称' })}
        value={undefined}
        onChange={onChange}
      />
    )
    fireEvent.change(screen.getByPlaceholderText('请输入昵称'), { target: { value: '麦麦' } })
    expect(onChange).toHaveBeenCalledWith('麦麦')
  })

  it('只读问题的输入框被禁用且带 readonly 属性', () => {
    render(
      <SurveyQuestion
        question={makeQuestion({ type: 'text', readOnly: true })}
        value="1.0.0"
        onChange={vi.fn()}
      />
    )
    const input = screen.getByDisplayValue('1.0.0')
    expect(input).toBeDisabled()
    expect(input).toHaveAttribute('readonly')
  })
})

describe('SurveyQuestion 多行文本题', () => {
  it('输入时回调完整文本并显示字数统计', () => {
    const onChange = vi.fn()
    render(
      <SurveyQuestion
        question={makeQuestion({ type: 'textarea', maxLength: 100 })}
        value="你好"
        onChange={onChange}
      />
    )
    expect(screen.getByText('2 / 100')).toBeInTheDocument()
    fireEvent.change(screen.getByDisplayValue('你好'), { target: { value: '你好呀' } })
    expect(onChange).toHaveBeenCalledWith('你好呀')
  })

  it('未设置 maxLength 时不显示字数统计', () => {
    render(
      <SurveyQuestion
        question={makeQuestion({ type: 'textarea' })}
        value="你好"
        onChange={vi.fn()}
      />
    )
    expect(screen.queryByText(/\/ \d+$/)).not.toBeInTheDocument()
  })
})

describe('SurveyQuestion 评分题', () => {
  it('渲染五颗星并在点击时回调对应分数', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(
      <SurveyQuestion
        question={makeQuestion({ type: 'rating' })}
        value={3}
        onChange={onChange}
      />
    )
    const stars = screen.getAllByRole('button')
    expect(stars).toHaveLength(5)
    // 已有评分时显示当前分数
    expect(screen.getByText('3 / 5')).toBeInTheDocument()
    await user.click(stars[3])
    expect(onChange).toHaveBeenCalledWith(4)
  })

  it('禁用时星星按钮不可点击且无分数展示（未评分）', () => {
    const onChange = vi.fn()
    render(
      <SurveyQuestion
        question={makeQuestion({ type: 'rating' })}
        value={undefined}
        onChange={onChange}
        disabled
      />
    )
    for (const star of screen.getAllByRole('button')) {
      expect(star).toBeDisabled()
    }
    expect(screen.queryByText(/\/ 5$/)).not.toBeInTheDocument()
  })
})

describe('SurveyQuestion 量表题', () => {
  it('渲染滑块的范围、当前值与两端标签', () => {
    render(
      <SurveyQuestion
        question={makeQuestion({ type: 'scale', min: 1, max: 10, minLabel: '很差', maxLabel: '很好' })}
        value={7}
        onChange={vi.fn()}
      />
    )
    const slider = screen.getByRole('slider')
    expect(slider).toHaveAttribute('aria-valuemin', '1')
    expect(slider).toHaveAttribute('aria-valuemax', '10')
    expect(slider).toHaveAttribute('aria-valuenow', '7')
    expect(screen.getByText('很差')).toBeInTheDocument()
    expect(screen.getByText('很好')).toBeInTheDocument()
    expect(screen.getByText('7')).toBeInTheDocument()
  })

  it('未作答时默认取最小值，未配置范围时使用默认 1-10', () => {
    render(
      <SurveyQuestion
        question={makeQuestion({ type: 'scale' })}
        value={undefined}
        onChange={vi.fn()}
      />
    )
    const slider = screen.getByRole('slider')
    expect(slider).toHaveAttribute('aria-valuemin', '1')
    expect(slider).toHaveAttribute('aria-valuemax', '10')
    expect(slider).toHaveAttribute('aria-valuenow', '1')
  })

  it('键盘操作滑块时回调新数值', () => {
    const onChange = vi.fn()
    render(
      <SurveyQuestion
        question={makeQuestion({ type: 'scale', min: 1, max: 10, step: 1 })}
        value={5}
        onChange={onChange}
      />
    )
    fireEvent.keyDown(screen.getByRole('slider'), { key: 'ArrowRight' })
    expect(onChange).toHaveBeenCalledWith(6)
  })
})

describe('SurveyQuestion 下拉题', () => {
  it('未选择时显示占位符', () => {
    render(
      <SurveyQuestion
        question={makeQuestion({ type: 'dropdown', options: twoOptions, placeholder: '选一个' })}
        value={undefined}
        onChange={vi.fn()}
      />
    )
    expect(screen.getByRole('combobox')).toBeInTheDocument()
    expect(screen.getByText('选一个')).toBeInTheDocument()
  })

  it('已选择时触发器显示对应选项文本', () => {
    render(
      <SurveyQuestion
        question={makeQuestion({ type: 'dropdown', options: twoOptions })}
        value="a"
        onChange={vi.fn()}
      />
    )
    expect(screen.getByText('选项A')).toBeInTheDocument()
  })
})
