import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { LibraryItem } from '../LibraryItem'
import { ThemeOption } from '../ThemeOption'

afterEach(() => cleanup())

describe('设置页小型模块', () => {
  it('LibraryItem 展示名称、描述和许可证', () => {
    render(<LibraryItem name="React" description="界面运行库" license="MIT" />)
    expect(screen.getByText('React')).toBeInTheDocument()
    expect(screen.getByText('界面运行库')).toBeInTheDocument()
    expect(screen.getByText('MIT')).toBeInTheDocument()
  })

  it.each(['light', 'dark', 'system'] as const)(
    'ThemeOption 点击 %s 主题时回传对应值',
    async (value) => {
      const user = userEvent.setup()
      const onChange = vi.fn()
      render(
        <ThemeOption
          value={value}
          current="light"
          onChange={onChange}
          label={`${value} 标签`}
          description={`${value} 描述`}
        />
      )

      const button = screen.getByRole('button', { name: new RegExp(`${value} 标签`) })
      await user.click(button)
      expect(onChange).toHaveBeenCalledWith(value)
      expect(screen.getByText(`${value} 描述`)).toBeInTheDocument()
    }
  )

  it('当前主题呈现选中样式，其他主题呈现普通边框', () => {
    const { rerender } = render(
      <ThemeOption
        value="dark"
        current="dark"
        onChange={vi.fn()}
        label="深色"
        description="深色描述"
      />
    )
    expect(screen.getByRole('button')).toHaveClass('border-primary', 'bg-accent')

    rerender(
      <ThemeOption
        value="dark"
        current="light"
        onChange={vi.fn()}
        label="深色"
        description="深色描述"
      />
    )
    expect(screen.getByRole('button')).toHaveClass('border-border')
  })
})
