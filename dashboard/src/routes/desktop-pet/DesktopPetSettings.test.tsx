import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { expect, it, vi } from 'vitest'

import { backendApi } from '@/lib/http'
import type { FieldHookComponentProps } from '@/lib/field-hooks'

import { DesktopPetSettings } from './DesktopPetSettings'

vi.mock('@/lib/http', () => ({
  backendApi: { get: vi.fn(), post: vi.fn() },
}))

vi.mock('@/components/DesktopPetSkinField', () => ({
  DesktopPetSkinField: ({ value, onChange }: FieldHookComponentProps) => (
    <select aria-label="当前皮肤" value={String(value)} onChange={(event) => onChange?.(event.target.value)}>
      <option value="builtin">内置麦麦</option>
      <option value="orange-sprout">橙芽</option>
    </select>
  ),
}))

it('切换皮肤立即保存全部草稿，其他字段停止编辑后自动保存', async () => {
  vi.mocked(backendApi.get).mockImplementation(async (path) => {
    if (path === '/api/webui/config/bot') {
      return { success: true, config: { desktop_pet: { enabled: true, skin_id: 'builtin', proactive_wake_interval_minutes: 30 } } }
    }
    if (path === '/api/webui/config/schema/bot') {
      return {
        success: true,
        schema: {
          className: 'Config',
          classDoc: '',
          fields: [],
          nested: {
            desktop_pet: {
              className: 'DesktopPetConfig',
              classDoc: '',
              fields: [{
                name: 'skin_id',
                type: 'string',
                label: '当前皮肤',
                description: '',
                required: true,
              }, {
                name: 'proactive_wake_interval_minutes',
                type: 'integer',
                label: '主动思考间隔',
                description: '',
                required: true,
                'x-widget': 'input',
              }],
            },
          },
        },
      }
    }
    throw new Error(`Unexpected request: ${path}`)
  })
  vi.mocked(backendApi.post).mockResolvedValue({ success: true })
  const onSaved = vi.fn()
  const queryClient = new QueryClient()
  render(
    <QueryClientProvider client={queryClient}>
      <DesktopPetSettings onSaved={onSaved} />
    </QueryClientProvider>
  )

  const interval = await screen.findByRole('spinbutton')
  fireEvent.change(interval, { target: { value: '45' } })
  expect(backendApi.post).not.toHaveBeenCalled()
  fireEvent.change(screen.getByRole('combobox', { name: '当前皮肤' }), { target: { value: 'orange-sprout' } })

  await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1))
  expect(backendApi.post).toHaveBeenCalledExactlyOnceWith(
    '/api/webui/config/bot/section/desktop_pet',
    expect.objectContaining({ body: { skin_id: 'orange-sprout', proactive_wake_interval_minutes: 45 } })
  )
  expect(interval).toHaveValue(45)
  fireEvent.change(interval, { target: { value: '46' } })
  await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(2), { timeout: 4000 })
  expect(backendApi.post).toHaveBeenLastCalledWith(
    '/api/webui/config/bot/section/desktop_pet',
    expect.objectContaining({ body: { proactive_wake_interval_minutes: 46 } })
  )
  expect(interval).toHaveValue(46)
})
