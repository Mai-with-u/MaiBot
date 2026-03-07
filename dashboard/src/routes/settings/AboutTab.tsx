import { useTranslation } from 'react-i18next'

import { ScrollArea } from '@/components/ui/scroll-area'

import { APP_NAME, APP_VERSION } from '@/lib/version'
import { cn } from '@/lib/utils'

import { LibraryItem } from './LibraryItem'

export function AboutTab() {
  const { t } = useTranslation()

  return (
    <div className="space-y-4 sm:space-y-6">
      {/* GitHub 开源地址 */}
      <div className="rounded-lg border-2 border-primary/30 bg-gradient-to-r from-primary/5 to-primary/10 p-4 sm:p-6">
        <div className="flex items-start gap-3 sm:gap-4">
          <div className="flex-shrink-0 rounded-lg bg-primary/10 p-2 sm:p-3">
            <svg
              className="h-6 w-6 sm:h-8 sm:w-8 text-primary"
              fill="currentColor"
              viewBox="0 0 24 24"
              aria-hidden="true"
            >
              <path
                fillRule="evenodd"
                d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.531 1.032 1.531 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0112 6.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0022 12.017C22 6.484 17.522 2 12 2z"
                clipRule="evenodd"
              />
            </svg>
          </div>
          <div className="flex-1 min-w-0">
            <h3 className="text-lg sm:text-xl font-bold text-foreground mb-2">
              {t('settings.about.openSource')}
            </h3>
            <p className="text-sm sm:text-base text-muted-foreground mb-3">
              {t('settings.about.openSourceDesc')}
            </p>
            <a
              href="https://github.com/Mai-with-u/MaiBot-Dashboard"
              target="_blank"
              rel="noopener noreferrer"
              className={cn(
                "inline-flex items-center gap-2 px-4 py-2 rounded-lg",
                "bg-primary text-primary-foreground font-medium text-sm",
                "hover:bg-primary/90 transition-colors",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
              )}
            >
              <svg
                className="h-4 w-4"
                fill="currentColor"
                viewBox="0 0 24 24"
                aria-hidden="true"
              >
                <path
                  fillRule="evenodd"
                  d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.531 1.032 1.531 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0112 6.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0022 12.017C22 6.484 17.522 2 12 2z"
                  clipRule="evenodd"
                />
              </svg>
              {t('settings.about.visitGitHub')}
              <svg
                className="h-4 w-4"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"
                />
              </svg>
            </a>
          </div>
        </div>
      </div>

      {/* 应用信息 */}
      <div className="rounded-lg border bg-card p-4 sm:p-6">
        <h3 className="text-base sm:text-lg font-semibold mb-3 sm:mb-4">{t('settings.about.aboutApp')} {APP_NAME}</h3>
        <div className="space-y-2 text-xs sm:text-sm text-muted-foreground">
          <p>{t('settings.about.version')} {APP_VERSION}</p>
          <p>{t('settings.about.appDesc')}</p>
        </div>
      </div>

      {/* 作者信息 */}
      <div className="rounded-lg border bg-card p-4 sm:p-6">
        <h3 className="text-base sm:text-lg font-semibold mb-3 sm:mb-4">{t('settings.about.author')}</h3>
        <div className="space-y-3">
          <div className="space-y-1">
            <p className="text-sm font-medium">{t('settings.about.maimaiCore')}</p>
            <p className="text-xs sm:text-sm text-muted-foreground">Mai-with-u</p>
          </div>
          <div className="space-y-1">
            <p className="text-sm font-medium">WebUI</p>
            <p className="text-xs sm:text-sm text-muted-foreground">Mai-with-u <a href="https://github.com/DrSmoothl" target="_blank" rel="noopener noreferrer" className="text-primary underline">@MotricSeven</a></p>
          </div>
        </div>
      </div>

      {/* 技术栈 */}
      <div className="rounded-lg border bg-card p-4 sm:p-6">
        <h3 className="text-base sm:text-lg font-semibold mb-3 sm:mb-4">{t('settings.about.techStack')}</h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs sm:text-sm text-muted-foreground">
          <div className="space-y-1.5">
            <p className="font-medium text-foreground">{t('settings.about.frontendFramework')}</p>
            <ul className="space-y-0.5 list-disc list-inside">
              <li>React 19.2.0</li>
              <li>TypeScript 5.7.2</li>
              <li>Vite 6.0.7</li>
              <li>TanStack Router 1.94.2</li>
            </ul>
          </div>
          <div className="space-y-1.5">
            <p className="font-medium text-foreground">{t('settings.about.uiComponents')}</p>
            <ul className="space-y-0.5 list-disc list-inside">
              <li>shadcn/ui</li>
              <li>Radix UI</li>
              <li>Tailwind CSS 3.4.17</li>
              <li>Lucide Icons</li>
            </ul>
          </div>
          <div className="space-y-1.5">
            <p className="font-medium text-foreground">{t('settings.about.backend')}</p>
            <ul className="space-y-0.5 list-disc list-inside">
              <li>Python 3.12+</li>
              <li>FastAPI</li>
              <li>Uvicorn</li>
              <li>WebSocket</li>
            </ul>
          </div>
          <div className="space-y-1.5">
            <p className="font-medium text-foreground">{t('settings.about.buildTool')}</p>
            <ul className="space-y-0.5 list-disc list-inside">
              <li>Bun / npm</li>
              <li>ESLint 9.17.0</li>
              <li>PostCSS</li>
            </ul>
          </div>
        </div>
      </div>

      {/* 开源感谢 */}
      <div className="rounded-lg border bg-card p-4 sm:p-6">
        <h3 className="text-base sm:text-lg font-semibold mb-3 sm:mb-4">{t('settings.about.openSourceThanks')}</h3>
        <p className="text-xs sm:text-sm text-muted-foreground mb-3">
          {t('settings.about.openSourceThanksDesc')}
        </p>
        <ScrollArea className="h-[300px] sm:h-[400px]">
          <div className="space-y-4 pr-4">
            {/* UI 框架 */}
            <div className="space-y-2">
              <p className="text-sm font-medium text-foreground">{t('settings.about.uiFrameworkGroup')}</p>
              <div className="grid gap-2 text-xs sm:text-sm">
                <LibraryItem name="React" description={t('settings.about.lib.react')} license="MIT" />
                <LibraryItem name="shadcn/ui" description={t('settings.about.lib.shadcn')} license="MIT" />
                <LibraryItem name="Radix UI" description={t('settings.about.lib.radix')} license="MIT" />
                <LibraryItem name="Tailwind CSS" description={t('settings.about.lib.tailwind')} license="MIT" />
                <LibraryItem name="Lucide React" description={t('settings.about.lib.lucide')} license="ISC" />
              </div>
            </div>

            {/* 路由与状态 */}
            <div className="space-y-2">
              <p className="text-sm font-medium text-foreground">{t('settings.about.routingStateGroup')}</p>
              <div className="grid gap-2 text-xs sm:text-sm">
                <LibraryItem name="TanStack Router" description={t('settings.about.lib.tanstackRouter')} license="MIT" />
                <LibraryItem name="Zustand" description={t('settings.about.lib.zustand')} license="MIT" />
              </div>
            </div>

            {/* 表单与验证 */}
            <div className="space-y-2">
              <p className="text-sm font-medium text-foreground">{t('settings.about.formGroup')}</p>
              <div className="grid gap-2 text-xs sm:text-sm">
                <LibraryItem name="React Hook Form" description={t('settings.about.lib.reactHookForm')} license="MIT" />
                <LibraryItem name="Zod" description={t('settings.about.lib.zod')} license="MIT" />
              </div>
            </div>

            {/* 工具库 */}
            <div className="space-y-2">
              <p className="text-sm font-medium text-foreground">{t('settings.about.utilsGroup')}</p>
              <div className="grid gap-2 text-xs sm:text-sm">
                <LibraryItem name="clsx" description={t('settings.about.lib.clsx')} license="MIT" />
                <LibraryItem name="tailwind-merge" description={t('settings.about.lib.tailwindMerge')} license="MIT" />
                <LibraryItem name="class-variance-authority" description={t('settings.about.lib.cva')} license="Apache-2.0" />
                <LibraryItem name="date-fns" description={t('settings.about.lib.dateFns')} license="MIT" />
              </div>
            </div>

            {/* 动画 */}
            <div className="space-y-2">
              <p className="text-sm font-medium text-foreground">{t('settings.about.animationGroup')}</p>
              <div className="grid gap-2 text-xs sm:text-sm">
                <LibraryItem name="Framer Motion" description={t('settings.about.lib.framerMotion')} license="MIT" />
                <LibraryItem name="vaul" description={t('settings.about.lib.vaul')} license="MIT" />
              </div>
            </div>

            {/* 后端相关 */}
            <div className="space-y-2">
              <p className="text-sm font-medium text-foreground">{t('settings.about.backendGroup')}</p>
              <div className="grid gap-2 text-xs sm:text-sm">
                <LibraryItem name="FastAPI" description={t('settings.about.lib.fastapi')} license="MIT" />
                <LibraryItem name="Uvicorn" description={t('settings.about.lib.uvicorn')} license="BSD-3-Clause" />
                <LibraryItem name="Pydantic" description={t('settings.about.lib.pydantic')} license="MIT" />
                <LibraryItem name="python-multipart" description={t('settings.about.lib.pythonMultipart')} license="Apache-2.0" />
              </div>
            </div>

            {/* 开发工具 */}
            <div className="space-y-2">
              <p className="text-sm font-medium text-foreground">{t('settings.about.devToolsGroup')}</p>
              <div className="grid gap-2 text-xs sm:text-sm">
                <LibraryItem name="TypeScript" description={t('settings.about.lib.typescript')} license="Apache-2.0" />
                <LibraryItem name="Vite" description={t('settings.about.lib.vite')} license="MIT" />
                <LibraryItem name="ESLint" description={t('settings.about.lib.eslint')} license="MIT" />
                <LibraryItem name="PostCSS" description={t('settings.about.lib.postcss')} license="MIT" />
              </div>
            </div>
          </div>
        </ScrollArea>
      </div>

      {/* 许可证 */}
      <div className="rounded-lg border bg-card p-4 sm:p-6">
        <h3 className="text-base sm:text-lg font-semibold mb-3 sm:mb-4">{t('settings.about.openSourceLicense')}</h3>
        <div className="space-y-3">
          <div className="rounded-lg bg-primary/5 border border-primary/20 p-3 sm:p-4">
            <div className="flex items-start gap-2 sm:gap-3">
              <div className="flex-shrink-0 mt-0.5">
                <div className="rounded-md bg-primary/10 px-2 py-1">
                  <span className="text-xs sm:text-sm font-bold text-primary">GPLv3</span>
                </div>
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-sm sm:text-base font-semibold text-foreground mb-1">
                  MaiBot WebUI
                </p>
                <p className="text-xs sm:text-sm text-muted-foreground">
                  {t('settings.about.licenseDesc')}
                </p>
              </div>
            </div>
          </div>
          <p className="text-xs sm:text-sm text-muted-foreground">
            {t('settings.about.licenseDeps')}
          </p>
        </div>
      </div>
    </div>
  )
}
