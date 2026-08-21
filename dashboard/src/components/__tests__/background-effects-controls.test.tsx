import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { BackgroundEffectsControls } from '@/components/background-effects-controls'
import type { BackgroundEffects } from '@/lib/theme/tokens'
import { defaultBackgroundEffects } from '@/lib/theme/tokens'

// jsdom 未实现 Pointer Capture，Radix Select 打开下拉时会调用
if (!Element.prototype.hasPointerCapture) {
  Element.prototype.hasPointerCapture = () => false
}
if (!Element.prototype.setPointerCapture) {
  Element.prototype.setPointerCapture = () => {}
}
if (!Element.prototype.releasePointerCapture) {
  Element.prototype.releasePointerCapture = () => {}
}

const baseEffects: BackgroundEffects = {
  ...defaultBackgroundEffects,
  overlayColor: '0 100% 50%',
  overlayOpacity: 0.2,
  gradientOverlay: 'linear-gradient(red, blue)',
}

afterEach(() => {
  cleanup()
})

describe('BackgroundEffectsControls', () => {
  it('将 overlayColor 的各色相区间转换为十六进制并展示', () => {
    const samples: Array<{ overlayColor: string; hex: string }> = [
      { overlayColor: '', hex: '#000000' },
      { overlayColor: '120 50%', hex: '#000000' },
      { overlayColor: '30 100% 50%', hex: '#ff8000' },
      { overlayColor: '90 100% 50%', hex: '#80ff00' },
      { overlayColor: '150 100% 50%', hex: '#00ff80' },
      { overlayColor: '210 100% 50%', hex: '#0080ff' },
      { overlayColor: '270 100% 50%', hex: '#8000ff' },
      { overlayColor: '330 100% 50%', hex: '#ff0080' },
      { overlayColor: '360 100% 50%', hex: '#000000' },
      { overlayColor: '0 0% 0.2%', hex: '#010101' },
    ]

    const { rerender } = render(
      <BackgroundEffectsControls effects={baseEffects} onChange={vi.fn()} />
    )

    for (const sample of samples) {
      rerender(
        <BackgroundEffectsControls
          effects={{ ...baseEffects, overlayColor: sample.overlayColor }}
          onChange={vi.fn()}
        />
      )
      expect(screen.getAllByDisplayValue(sample.hex)).toHaveLength(2)
    }
  })

  it('对比度和饱和度滑块会按当前值增量回调', () => {
    const onChange = vi.fn()
    render(
      <BackgroundEffectsControls
        effects={{ ...baseEffects, contrast: 100, saturate: 80 }}
        onChange={onChange}
      />
    )

    const sliders = screen.getAllByRole('slider')
    expect(sliders).toHaveLength(5)

    fireEvent.keyDown(sliders[3], { key: 'ArrowRight' })
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ contrast: 101 }))

    fireEvent.keyDown(sliders[4], { key: 'ArrowRight' })
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ saturate: 81 }))
  })

  it('渐变叠加为空时展示空输入，修改后写回字符串', () => {
    const onChange = vi.fn()
    render(
      <BackgroundEffectsControls
        effects={{ ...baseEffects, gradientOverlay: undefined }}
        onChange={onChange}
      />
    )

    const gradientInput = screen.getByPlaceholderText(
      'e.g. linear-gradient(to bottom, transparent, black)'
    )
    expect(gradientInput).toHaveValue('')

    fireEvent.change(gradientInput, { target: { value: 'linear-gradient(black, white)' } })
    expect(onChange).toHaveBeenCalledWith({
      ...baseEffects,
      gradientOverlay: 'linear-gradient(black, white)',
    })
  })

  it('禁用时为容器加上半透明样式，并拦截颜色、渐变和滑块变更', () => {
    const onChange = vi.fn()
    const { container } = render(
      <BackgroundEffectsControls effects={baseEffects} onChange={onChange} disabled />
    )

    expect(container.firstElementChild).toHaveClass('opacity-50')
    expect(screen.getByRole('button', { name: '重置默认' })).toBeDisabled()
    expect(document.querySelector('input[type="color"]')).toBeDisabled()
    expect(
      screen.getByPlaceholderText('e.g. linear-gradient(to bottom, transparent, black)')
    ).toBeDisabled()

    fireEvent.change(document.querySelector('input[type="color"]') as HTMLInputElement, {
      target: { value: '#00ff00' },
    })
    fireEvent.change(
      screen.getByPlaceholderText('e.g. linear-gradient(to bottom, transparent, black)'),
      { target: { value: 'linear-gradient(black, transparent)' } }
    )
    fireEvent.keyDown(screen.getAllByRole('slider')[0], { key: 'ArrowRight' })
    fireEvent.click(screen.getByRole('button', { name: '重置默认' }))

    expect(onChange).not.toHaveBeenCalled()
  })

  it('位置选择会写回对应枚举值', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(<BackgroundEffectsControls effects={baseEffects} onChange={onChange} />)

    await user.click(screen.getByRole('combobox'))
    await user.click(await screen.findByRole('option', { name: '包含 (Contain)' }))
    expect(onChange).toHaveBeenCalledWith({ ...baseEffects, position: 'contain' })
  })
})
