import { cleanup, render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { PluginMirrorsPage } from '../plugin-mirrors'

const navigateMock = vi.fn()

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => navigateMock,
}))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('PluginMirrorsPage 重定向', () => {
  it('默认将旧镜像源页面重定向至 /plugins', () => {
    const { container } = render(<PluginMirrorsPage />)

    expect(container).toBeEmptyDOMElement()
    expect(navigateMock).toHaveBeenCalledTimes(1)
    expect(navigateMock).toHaveBeenCalledWith({ to: '/plugins', replace: true })
  })

  it('embedded 模式下重定向至 /plugins/embed', () => {
    const { container } = render(<PluginMirrorsPage embedded />)

    expect(container).toBeEmptyDOMElement()
    expect(navigateMock).toHaveBeenCalledTimes(1)
    expect(navigateMock).toHaveBeenCalledWith({ to: '/plugins/embed', replace: true })
  })

  it('embedded 属性切换后会重新导航到目标路由', () => {
    const { rerender } = render(<PluginMirrorsPage />)
    expect(navigateMock).toHaveBeenLastCalledWith({ to: '/plugins', replace: true })

    rerender(<PluginMirrorsPage embedded />)
    expect(navigateMock).toHaveBeenCalledTimes(2)
    expect(navigateMock).toHaveBeenLastCalledWith({ to: '/plugins/embed', replace: true })
  })
})
