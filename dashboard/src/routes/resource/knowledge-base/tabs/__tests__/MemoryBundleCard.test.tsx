import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { getMemoryBundles } from '@/lib/memory-api'

import { MemoryBundleCard } from '../MemoryBundleCard'

vi.mock('@/lib/memory-api', () => ({
  downloadMemoryBundle: vi.fn(),
  exportMemoryBundle: vi.fn(),
  getMemoryBundles: vi.fn(),
  importMemoryBundle: vi.fn(),
  uninstallMemoryBundle: vi.fn(),
}))

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(getMemoryBundles).mockResolvedValue({
    success: true,
    count: 1,
    items: [
      {
        installation_id: 'install-1',
        package_id: 'example.bundle',
        version: '1.0.0',
        name: '示例知识包',
        content_level: 'knowledge',
        content_digest: 'digest-1',
        scope_type: 'global',
        scope_key: 'global',
        status: 'installed',
        installed_at: 1,
        updated_at: 1,
        paragraph_count: 2,
      },
    ],
  })
})

describe('MemoryBundleCard', () => {
  it('挂载后自动读取并展示已安装知识包', async () => {
    render(<MemoryBundleCard chatTargets={[]} />)

    await waitFor(() => {
      expect(getMemoryBundles).toHaveBeenCalledWith(50)
    })
    expect(await screen.findByText('示例知识包')).toBeInTheDocument()
    expect(screen.getByText('example.bundle · 1.0.0 · 2 条')).toBeInTheDocument()
  })
})
