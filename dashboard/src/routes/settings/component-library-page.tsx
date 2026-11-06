import { Link } from '@tanstack/react-router'
import { ArrowLeft } from 'lucide-react'

import { Button } from '@/components/ui/button'

import ComponentGallery from './component-gallery'
import './component-library-page.css'

export function ComponentLibraryPage() {
  return (
    <div
      data-component-library-page
      className="flex h-full min-h-0 w-full min-w-0 flex-col overflow-hidden px-2 pt-2 pb-2 sm:px-3 sm:pt-3"
    >
      <ComponentGallery
        heading={
          <>
            <Button variant="ghost" size="icon-sm" asChild>
              <Link to="/settings" aria-label="返回 WebUI 设置" title="返回 WebUI 设置">
                <ArrowLeft className="h-4 w-4" />
              </Link>
            </Button>
            <h1 className="shrink-0 text-sm font-semibold">公共组件库</h1>
          </>
        }
      />
    </div>
  )
}
