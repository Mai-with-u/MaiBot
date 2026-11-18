import { Link } from '@tanstack/react-router'
import { Library } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'

export function ComponentLibrary() {
  return (
    <Card className="flex flex-wrap items-center justify-between gap-4 p-4">
      <div className="space-y-1">
        <div className="font-medium">WebUI 公共组件库</div>
        <p className="text-muted-foreground text-sm">
          浏览公共组件，预览当前主题下的样式与交互。
        </p>
      </div>
      <Button variant="outline" asChild>
        <Link to="/settings/components">
          <Library className="mr-2 h-4 w-4" />
          浏览组件库
        </Link>
      </Button>
    </Card>
  )
}
