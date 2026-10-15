import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { LibraryItem } from '../LibraryItem'

afterEach(() => cleanup())

describe('设置页小型模块', () => {
  it('LibraryItem 展示名称、描述和许可证', () => {
    render(<LibraryItem name="React" description="界面运行库" license="MIT" />)
    expect(screen.getByText('React')).toBeInTheDocument()
    expect(screen.getByText('界面运行库')).toBeInTheDocument()
    expect(screen.getByText('MIT')).toBeInTheDocument()
  })
})
