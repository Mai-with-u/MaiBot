import { fireEvent, render, screen } from '@testing-library/react'
import { CalendarDay } from 'react-day-picker'
import { describe, expect, it, vi } from 'vitest'

import { Calendar, CalendarDayButton } from '../calendar'

describe('Calendar', () => {
  it('渲染 data-slot 根节点与月份标题、导航箭头', () => {
    const { container } = render(<Calendar defaultMonth={new Date(2026, 0, 1)} />)

    expect(container.querySelector('[data-slot="calendar"]')).toBeInTheDocument()
    // react-day-picker 默认 date-fns enUS 本地化，标题固定为英文
    expect(screen.getByText('January 2026')).toBeInTheDocument()

    const previousButton = screen.getByRole('button', { name: /previous month/i })
    const nextButton = screen.getByRole('button', { name: /next month/i })
    expect(previousButton.querySelector('.lucide-chevron-left')).not.toBeNull()
    expect(nextButton.querySelector('.lucide-chevron-right')).not.toBeNull()
  })

  it('点击下一月按钮切换展示月份', () => {
    render(<Calendar defaultMonth={new Date(2026, 0, 1)} />)

    fireEvent.click(screen.getByRole('button', { name: /next month/i }))
    expect(screen.getByText('February 2026')).toBeInTheDocument()
  })

  it('single 模式点击日期触发 onSelect', () => {
    const onSelect = vi.fn()
    render(
      <Calendar mode="single" defaultMonth={new Date(2026, 0, 1)} onSelect={onSelect} />
    )

    fireEvent.click(screen.getByRole('button', { name: /15/ }))

    expect(onSelect).toHaveBeenCalledTimes(1)
    const selectedDate = onSelect.mock.calls[0][0] as Date
    expect(selectedDate.getFullYear()).toBe(2026)
    expect(selectedDate.getMonth()).toBe(0)
    expect(selectedDate.getDate()).toBe(15)
  })

  it('选中的单个日期带 data-selected-single 标记', () => {
    const { container } = render(
      <Calendar
        mode="single"
        defaultMonth={new Date(2026, 0, 1)}
        selected={new Date(2026, 0, 15)}
      />
    )

    const selectedButton = container.querySelector('button[data-selected-single="true"]')
    expect(selectedButton).not.toBeNull()
    expect(selectedButton).toHaveTextContent('15')
  })

  it('range 模式下起止与中间日期分别打上范围标记', () => {
    const { container } = render(
      <Calendar
        mode="range"
        defaultMonth={new Date(2026, 0, 1)}
        selected={{ from: new Date(2026, 0, 10), to: new Date(2026, 0, 12) }}
      />
    )

    expect(container.querySelector('button[data-range-start="true"]')).toHaveTextContent('10')
    expect(container.querySelector('button[data-range-middle="true"]')).toHaveTextContent('11')
    expect(container.querySelector('button[data-range-end="true"]')).toHaveTextContent('12')
  })

  it('dropdown 布局的月份下拉使用短月份格式', () => {
    render(
      <Calendar
        captionLayout="dropdown"
        defaultMonth={new Date(2026, 0, 1)}
        startMonth={new Date(2025, 0, 1)}
        endMonth={new Date(2026, 11, 31)}
      />
    )

    expect(screen.getAllByRole('combobox').length).toBeGreaterThanOrEqual(2)

    // 与组件同样的取值方式计算期望文案，避免受运行环境语言影响
    const expectedShortMonth = new Date(2026, 0, 1).toLocaleString('default', { month: 'short' })
    const options = screen.getAllByRole('option')
    expect(options.some((option) => option.textContent === expectedShortMonth)).toBe(true)
  })
})

describe('CalendarDayButton', () => {
  function makeDay(): CalendarDay {
    return new CalendarDay(new Date(2026, 0, 15), new Date(2026, 0, 1))
  }

  it('focused 修饰符生效时自动聚焦按钮', () => {
    render(
      <CalendarDayButton day={makeDay()} modifiers={{ focused: true }}>
        15
      </CalendarDayButton>
    )

    const button = screen.getByRole('button', { name: '15' })
    expect(button).toHaveFocus()
    expect(button).toHaveAttribute('data-day', new Date(2026, 0, 15).toLocaleDateString())
  })

  it('未聚焦时不抢占焦点，选中态写入 data-selected-single', () => {
    render(
      <CalendarDayButton
        day={makeDay()}
        modifiers={{
          focused: false,
          selected: true,
          range_start: false,
          range_middle: false,
          range_end: false,
        }}
      >
        15
      </CalendarDayButton>
    )

    const button = screen.getByRole('button', { name: '15' })
    expect(button).not.toHaveFocus()
    expect(button).toHaveAttribute('data-selected-single', 'true')
    expect(button).toHaveAttribute('data-range-start', 'false')
  })
})
