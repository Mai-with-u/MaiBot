import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { APP_NAME, APP_VERSION } from '@/lib/version'

import { AboutTab } from '../AboutTab'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

afterEach(() => cleanup())

const TECH_STACK_ITEMS = [
  'React 19.2.0',
  'TypeScript 5.7.2',
  'Vite 6.0.7',
  'TanStack Router 1.94.2',
  'shadcn/ui',
  'Radix UI',
  'Tailwind CSS 4.2.1',
  'Lucide Icons',
  'Python 3.12+',
  'FastAPI',
  'Uvicorn',
  'WebSocket',
  'Bun / npm',
  'ESLint 9.17.0',
]

const LIBRARY_ITEMS: Array<[name: string, license: string]> = [
  ['React', 'MIT'],
  ['shadcn/ui', 'MIT'],
  ['Radix UI', 'MIT'],
  ['Tailwind CSS', 'MIT'],
  ['Lucide React', 'ISC'],
  ['Iconify React', 'MIT'],
  ['Streamline Sharp', 'CC BY 4.0'],
  ['Streamline Block', 'CC BY 4.0'],
  ['TanStack Router', 'MIT'],
  ['Zustand', 'MIT'],
  ['React Hook Form', 'MIT'],
  ['Zod', 'MIT'],
  ['clsx', 'MIT'],
  ['tailwind-merge', 'MIT'],
  ['class-variance-authority', 'Apache-2.0'],
  ['date-fns', 'MIT'],
  ['Framer Motion', 'MIT'],
  ['vaul', 'MIT'],
  ['FastAPI', 'MIT'],
  ['Uvicorn', 'BSD-3-Clause'],
  ['Pydantic', 'MIT'],
  ['python-multipart', 'Apache-2.0'],
  ['TypeScript', 'Apache-2.0'],
  ['Vite', 'MIT'],
  ['ESLint', 'MIT'],
]

describe('AboutTab', () => {
  it('展示应用版本、作者外链和分区标题', () => {
    render(<AboutTab />)

    expect(
      screen.getByRole('heading', { name: `settings.about.aboutApp ${APP_NAME}` }),
    ).toBeInTheDocument()
    expect(screen.getByText(`settings.about.version ${APP_VERSION}`)).toBeInTheDocument()
    expect(screen.getByText('settings.about.appDesc')).toBeInTheDocument()
    expect(screen.getByText('settings.about.maimaiCore')).toBeInTheDocument()
    expect(screen.getByText('WebUI')).toBeInTheDocument()

    const githubLink = screen.getByRole('link', { name: /settings.about.visitGitHub/ })
    expect(githubLink).toHaveAttribute('target', '_blank')
    expect(githubLink).toHaveAttribute('rel', 'noopener noreferrer')

    const authorLink = screen.getByRole('link', { name: '@MotricSeven' })
    expect(authorLink).toHaveAttribute('href', 'https://github.com/DrSmoothl')
    expect(authorLink).toHaveAttribute('target', '_blank')
    expect(authorLink).toHaveAttribute('rel', 'noopener noreferrer')

    expect(screen.getByRole('heading', { name: 'settings.about.openSource' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'settings.about.author' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'settings.about.techStack' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'settings.about.openSourceThanks' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'settings.about.openSourceLicense' })).toBeInTheDocument()
    expect(screen.getByText('settings.about.licenseDesc')).toBeInTheDocument()
    expect(screen.getByText('settings.about.licenseDeps')).toBeInTheDocument()
    expect(screen.getByText('MaiBot WebUI')).toBeInTheDocument()
  })

  it('列出全部技术栈与开源库许可证', () => {
    render(<AboutTab />)

    for (const item of TECH_STACK_ITEMS) {
      expect(screen.getAllByText(item).length).toBeGreaterThan(0)
    }

    expect(screen.getByText('settings.about.frontendFramework')).toBeInTheDocument()
    expect(screen.getByText('settings.about.uiComponents')).toBeInTheDocument()
    expect(screen.getByText('settings.about.backend')).toBeInTheDocument()
    expect(screen.getByText('settings.about.buildTool')).toBeInTheDocument()
    expect(screen.getByText('settings.about.uiFrameworkGroup')).toBeInTheDocument()
    expect(screen.getByText('settings.about.routingStateGroup')).toBeInTheDocument()
    expect(screen.getByText('settings.about.formGroup')).toBeInTheDocument()
    expect(screen.getByText('settings.about.utilsGroup')).toBeInTheDocument()
    expect(screen.getByText('settings.about.animationGroup')).toBeInTheDocument()
    expect(screen.getByText('settings.about.backendGroup')).toBeInTheDocument()
    expect(screen.getByText('settings.about.devToolsGroup')).toBeInTheDocument()

    for (const [name, license] of LIBRARY_ITEMS) {
      const nameNode = screen.getByText(name, { selector: 'p.font-medium' })
      const row = nameNode.closest('div')?.parentElement
      expect(row, `找不到 ${name} 的许可证行`).not.toBeNull()
      expect(row).toHaveTextContent(license)
      expect(row).toHaveTextContent('settings.about.lib.')
    }

    expect(screen.getAllByText('CC BY 4.0')).toHaveLength(2)
    expect(screen.getByText('BSD-3-Clause')).toBeInTheDocument()
    expect(screen.getByText('ISC')).toBeInTheDocument()
  })
})
