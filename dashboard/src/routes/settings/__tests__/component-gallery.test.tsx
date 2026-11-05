import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import ComponentGallery from '../component-gallery'

vi.mock('@/components/use-theme', () => ({
  useTheme: () => ({ themeConfig: { dashboardStyle: 'modern' } }),
}))

afterEach(cleanup)

describe('公共组件库', () => {
  it('为公共目录的每个生产模块提供且只提供一个可渲染的条目', () => {
    const modules = Object.keys(
      import.meta.glob(['/src/components/ui/*.{ts,tsx}', '!/src/components/ui/*.test.*'])
    )
      .map((path) =>
        path
          .split('/')
          .pop()!
          .replace(/\.tsx?$/, '')
      )
      .sort()
    render(<ComponentGallery />)
    fireEvent.click(screen.getByRole('switch', { name: '显示信息' }))
    const paths = screen
      .getAllByText(/^@\/components\/ui\//)
      .map((element) => element.textContent!.replace('@/components/ui/', ''))
      .sort()
    expect(paths).toEqual(modules)
  })

  it('可检索新增组件，清空搜索后恢复完整目录', () => {
    render(<ComponentGallery />)
    fireEvent.click(screen.getByRole('switch', { name: '显示信息' }))
    fireEvent.change(screen.getByRole('textbox', { name: '搜索公共组件' }), {
      target: { value: 'DateTimePicker' },
    })
    expect(screen.getAllByText(/^@\/components\/ui\//)).toHaveLength(1)
    fireEvent.change(screen.getByRole('textbox', { name: '搜索公共组件' }), {
      target: { value: '不存在的组件' },
    })
    expect(screen.getByText('没有找到匹配的组件')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '清除筛选' }))
    expect(screen.getAllByText(/^@\/components\/ui\//).length).toBeGreaterThan(24)
  })
})
