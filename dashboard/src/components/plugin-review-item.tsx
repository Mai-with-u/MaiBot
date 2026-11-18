import { Star } from 'lucide-react'

import type { DeveloperReply } from '@/lib/plugin-feedback'

interface ReviewItem {
  username?: string | null
  anonymous?: boolean
  rating?: number | null
  comment?: string
  created_at: string
  reply?: DeveloperReply | null
}

/** 单条最近评价：有评分显示星级，有评论内容时展示评论正文及开发者回复。 */
export function PluginReviewItem({ item }: { item: ReviewItem }) {
  const displayName = item.anonymous || !item.username ? '匿名用户' : item.username

  return (
    <div data-plugin-rating-item="true" className="rounded-lg border bg-muted/50 p-3">
      <div className={`flex flex-wrap items-center justify-between gap-2${item.comment ? ' mb-2' : ''}`}>
        <div className="grid grid-cols-[5em_auto] items-center gap-2 text-sm">
          <span className="min-w-0 truncate font-medium" title={displayName}>{displayName}</span>
          {item.rating != null && (
            <div className="flex items-center gap-1">
              {[1, 2, 3, 4, 5].map((star) => (
                <Star key={star} className={`h-3 w-3 ${star <= Number(item.rating) ? 'fill-yellow-400 text-yellow-400' : 'text-muted-foreground'}`} />
              ))}
            </div>
          )}
        </div>
        <span className="ml-auto shrink-0 whitespace-nowrap text-xs text-muted-foreground">
          {new Date(item.created_at).toLocaleDateString()}
        </span>
      </div>
      {item.comment && <p className="whitespace-pre-wrap break-words text-sm text-muted-foreground">{item.comment}</p>}
      {item.reply && (
        <div className="mt-3 space-y-1 border-l-2 border-primary/50 pl-3">
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <span className="font-medium text-foreground">开发者回复 · @{item.reply.login}</span>
            <span>{new Date(item.reply.updated * 1000).toLocaleString()}</span>
          </div>
          <p className="whitespace-pre-wrap break-words text-sm">{item.reply.body}</p>
        </div>
      )}
    </div>
  )
}
