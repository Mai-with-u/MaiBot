import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { expect, it, vi } from 'vitest'

import { getBotConfig, getBotConfigSchema, updateBotConfigSection } from '@/lib/config-api'

import { DesktopPetSettings } from './DesktopPetSettings'

vi.mock('@/lib/config-api', () => ({
  getBotConfig: vi.fn(),
  getBotConfigSchema: vi.fn(),
  updateBotConfigSection: vi.fn(),
}))

it('桌宠设置只保存修改过的字段，并在成功后刷新运行状态', async () => {
  vi.mocked(getBotConfig).mockResolvedValue({ desktop_pet: { enabled: true, proactive_wake_interval_minutes: 30 } })
  vi.mocked(getBotConfigSchema).mockResolvedValue({
    className: 'Config',
    classDoc: '',
    fields: [],
    nested: {
      desktop_pet: {
        className: 'DesktopPetConfig',
        classDoc: '',
        fields: [{
          name: 'proactive_wake_interval_minutes',
          type: 'integer',
          label: '主动思考间隔',
          description: '',
          required: true,
          'x-widget': 'input',
        }],
      },
    },
  })
  vi.mocked(updateBotConfigSection).mockResolvedValue({ success: true })
  const onSaved = vi.fn()
  const queryClient = new QueryClient()
  render(
    <QueryClientProvider client={queryClient}>
      <DesktopPetSettings onSaved={onSaved} />
    </QueryClientProvider>
  )

  const interval = await screen.findByRole('spinbutton')
  const form = interval.closest('form')!
  fireEvent.change(interval, { target: { value: '45' } })
  fireEvent.submit(form)

  await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1))
  expect(updateBotConfigSection).toHaveBeenCalledExactlyOnceWith(
    'desktop_pet', { proactive_wake_interval_minutes: 45 }, expect.any(String)
  )
  expect(interval).toHaveValue(45)
})
