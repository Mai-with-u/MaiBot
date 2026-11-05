import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { SearchArtifacts } from './search-artifacts'

const getAIHelpImage = vi.hoisted(() => vi.fn())
const isElectron = vi.hoisted(() => vi.fn(() => false))

vi.mock('@/lib/ai-search-api', () => ({
  AI_HELP_IMAGE_PATH: '/api/webui/reasoning-process/image',
  getAIHelpImage,
}))

vi.mock('@/lib/runtime', () => ({ isElectron }))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  getAIHelpImage.mockReset()
  isElectron.mockReset().mockReturnValue(false)
})

describe('SearchArtifacts', () => {
  it('renders markdown without HTML and isolates SVG markup in an image', () => {
    const svg = '<svg xmlns="http://www.w3.org/2000/svg"><script>unsafe()</script></svg>'
    const { container } = render(
      <SearchArtifacts
        artifacts={[
          {
            kind: 'markdown',
            title: 'Instructions',
            content:
              '**Install**\n\n<iframe src="https://example.com"></iframe>\n\n![Tracker](https://example.com/t.png)',
          },
          { kind: 'svg', title: 'Flowchart', content: svg },
        ]}
      />
    )

    expect(screen.getByText('Install', { selector: 'strong' })).toBeInTheDocument()
    expect(container.querySelector('iframe, svg, script')).toBeNull()
    expect(screen.queryByRole('img', { name: 'Tracker' })).not.toBeInTheDocument()
    expect(screen.getByRole('img', { name: 'Flowchart' })).toHaveAttribute(
      'src',
      `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`
    )
  })

  it('loads an HTTPS image only after the preview button is clicked', () => {
    const url = 'https://images.example.org/diagram.png'
    render(<SearchArtifacts artifacts={[{ kind: 'image', title: 'Diagram', content: url }]} />)

    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    expect(screen.getByRole('link')).toHaveAttribute('href', url)
    expect(screen.getByRole('link')).toHaveAttribute('rel', 'noopener noreferrer')
    fireEvent.click(screen.getByRole('button', { name: 'search.previewArtifactImage' }))
    expect(screen.getByRole('img', { name: 'Diagram' })).toHaveAttribute('src', url)
    expect(screen.getByRole('img', { name: 'Diagram' })).toHaveAttribute(
      'referrerpolicy',
      'no-referrer'
    )
  })

  it('uses only the external link in Electron and rejects unapproved URLs', () => {
    isElectron.mockReturnValue(true)
    render(
      <SearchArtifacts
        artifacts={[
          { kind: 'image', title: 'External', content: 'https://images.example.org/diagram.png' },
          { kind: 'image', title: 'Unsafe protocol', content: 'javascript:alert(1)' },
          { kind: 'image', title: 'Private host', content: 'https://machine.internal/diagram.png' },
          {
            kind: 'image',
            title: 'Explicit port',
            content: 'https://example.org:8443/diagram.png',
          },
          { kind: 'image', title: 'Wrong route', content: '/api/webui/plugins' },
          {
            kind: 'image',
            title: 'Extra query',
            content: '/api/webui/reasoning-process/image?path=foo.png&x=1',
          },
        ]}
      />
    )

    expect(screen.getAllByRole('link')).toHaveLength(1)
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    expect(getAIHelpImage).not.toHaveBeenCalled()
  })

  it('loads the authorized local image through the authenticated API and revokes its URL', async () => {
    const revokeObjectURL = vi.fn()
    vi.stubGlobal(
      'URL',
      class extends URL {
        static createObjectURL = vi.fn(() => 'blob:local-preview')
        static revokeObjectURL = revokeObjectURL
      }
    )
    getAIHelpImage.mockResolvedValue(new Blob(['image bytes'], { type: 'image/png' }))

    const { unmount } = render(
      <SearchArtifacts
        artifacts={[
          {
            kind: 'image',
            title: 'Screenshot',
            content: '/api/webui/reasoning-process/image?path=data%2Fprompt_imgs%2Fscreen.png',
          },
        ]}
      />
    )

    expect(await screen.findByRole('img', { name: 'Screenshot' })).toHaveAttribute(
      'src',
      'blob:local-preview'
    )
    expect(getAIHelpImage).toHaveBeenCalledWith(
      'data/prompt_imgs/screen.png',
      expect.any(AbortSignal)
    )
    unmount()
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:local-preview')
  })

  it('rejects local responses with a non-image media type', async () => {
    getAIHelpImage.mockResolvedValue(new Blob(['<p>not an image</p>'], { type: 'text/html' }))
    render(
      <SearchArtifacts
        artifacts={[
          {
            kind: 'image',
            title: 'Local',
            content: '/api/webui/reasoning-process/image?path=local.png',
          },
        ]}
      />
    )
    expect(await screen.findByRole('alert')).toHaveTextContent('search.invalidArtifactImage')
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
  })
})
