import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { PluginWebUIRenderer } from '@/components/plugin-webui-renderer'
import type { WebUINode } from '@/lib/plugin-webui'

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }))
const uploadNode: WebUINode = {
  type: 'upload', label: 'Upload images', action: 'add', value: null, children: [], columns: null,
  name: null, options: [], variant: 'primary', chart_type: 'line', x: null, y: null,
}

describe('plugin multipart upload control', () => {
  it('reports progress and per-file errors without abandoning remaining files', async () => {
    const upload = vi.fn(async (_name, file: File, progress: (n: number) => void) => {
      progress(40)
      if (file.name === 'bad.png') throw new Error('Invalid image')
      return { added: true }
    })
    const complete = vi.fn(async () => undefined)
    render(<PluginWebUIRenderer nodes={[uploadNode]} data={{}} values={{}} busy={false}
      onChange={vi.fn()} onAction={vi.fn()} onUpload={upload} onUploadComplete={complete} />)
    fireEvent.change(screen.getByLabelText('Upload images'), { target: { files: [
      new File(['a'], 'bad.png', { type: 'image/png' }), new File(['b'], 'good.png', { type: 'image/png' }),
    ] } })
    await waitFor(() => expect(screen.getByText('good.png: 100%')).toBeInTheDocument())
    expect(screen.getByRole('alert')).toHaveTextContent('Invalid image')
    expect(upload).toHaveBeenCalledTimes(2)
    expect(complete).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('progressbar', { name: 'good.png' })).toHaveAttribute('value', '100')
  })

  it('clearly disables unsupported upload on old hosts', () => {
    render(<PluginWebUIRenderer nodes={[uploadNode]} data={{}} values={{}} busy={false}
      onChange={vi.fn()} onAction={vi.fn()} />)
    expect(screen.getByLabelText('Upload images')).toBeDisabled()
    expect(screen.getByRole('alert')).toHaveTextContent('file_upload')
  })
})
