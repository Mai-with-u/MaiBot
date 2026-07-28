import { Download, Star, ThumbsDown, ThumbsUp } from 'lucide-react'
import { useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import { Textarea } from '@/components/ui/textarea'
import { useToast } from '@/hooks/use-toast'
import {
  dislikePlugin,
  getPluginStats,
  getPluginUserState,
  likePlugin,
  ratePlugin,
  type PluginStatsData,
  type VoteStatsResponse,
} from '@/lib/plugin-stats'

interface PluginStatsProps {
  pluginId: string
  compact?: boolean
}

export function PluginStats(props: PluginStatsProps) {
  return <PluginStatsContent key={props.pluginId} {...props} />
}

function PluginStatsContent({ pluginId, compact = false }: PluginStatsProps) {
  const [stats, setStats] = useState<PluginStatsData | null>(null)
  const [loading, setLoading] = useState(true)
  const [userRating, setUserRating] = useState(0)
  const [savedUserRating, setSavedUserRating] = useState(0)
  const [userComment, setUserComment] = useState('')
  const [savedUserComment, setSavedUserComment] = useState('')
  const [liked, setLiked] = useState(false)
  const [disliked, setDisliked] = useState(false)
  const [actionLoading, setActionLoading] = useState<'like' | 'dislike' | 'rating' | null>(null)
  const [isRatingDialogOpen, setIsRatingDialogOpen] = useState(false)
  const { toast } = useToast()

  useEffect(() => {
    let cancelled = false

    void Promise.all([
      getPluginStats(pluginId),
      getPluginUserState(pluginId),
    ])
      .then(([statsData, userState]) => {
        if (cancelled) {
          return
        }

        setStats(statsData)
        if (userState) {
          setLiked(userState.liked)
          setDisliked(userState.disliked)
          setUserRating(userState.rating ?? 0)
          setSavedUserRating(userState.rating ?? 0)
          setUserComment(userState.comment)
          setSavedUserComment(userState.comment)
        }
      })
      .catch((error: unknown) => {
        console.error('加载插件统计失败:', error)
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [pluginId])

  const updateVoteStats = (result: VoteStatsResponse) => {
    setLiked(result.liked === true)
    setDisliked(result.disliked === true)
    setStats((currentStats) => currentStats
      ? {
        ...currentStats,
        likes: Number(result.likes ?? currentStats.likes),
        dislikes: Number(result.dislikes ?? currentStats.dislikes),
      }
      : currentStats)
  }

  const handleLike = async () => {
    setActionLoading('like')
    const result = await likePlugin(pluginId)
    setActionLoading(null)

    if (result.success) {
      updateVoteStats(result)
      toast({
        title: result.liked ? '已点赞' : '已取消点赞',
        description: result.liked ? '感谢你的支持' : '已更新你的反馈状态',
      })
      return
    }

    toast({
      title: '点赞失败',
      description: result.error || '未知错误',
      variant: 'destructive',
    })
  }

  const handleDislike = async () => {
    setActionLoading('dislike')
    const result = await dislikePlugin(pluginId)
    setActionLoading(null)

    if (result.success) {
      updateVoteStats(result)
      toast({
        title: result.disliked ? '已点踩' : '已取消点踩',
        description: '已更新你的反馈状态',
      })
      return
    }

    toast({
      title: '操作失败',
      description: result.error || '未知错误',
      variant: 'destructive',
    })
  }

  const handleSubmitRating = async () => {
    const commentChanged = userComment !== savedUserComment
    const canSubmit = userRating > 0 || commentChanged

    if (!canSubmit) {
      toast({
        title: '请填写评分或评论',
        description: '可以只评分，也可以只写评论',
        variant: 'destructive',
      })
      return
    }

    const ratingToSubmit = userRating > 0 && (userRating !== savedUserRating || !commentChanged)
      ? userRating
      : undefined
    const commentToSubmit = commentChanged ? userComment : undefined

    setActionLoading('rating')
    const result = await ratePlugin(pluginId, ratingToSubmit, commentToSubmit)
    setActionLoading(null)

    if (result.success) {
      const nextUserRating = result.user_rating === null
        ? 0
        : Number(result.user_rating ?? userRating)
      const nextUserComment = typeof result.user_comment === 'string'
        ? result.user_comment
        : typeof result.comment === 'string'
          ? result.comment
          : commentToSubmit !== undefined
            ? commentToSubmit
            : userComment

      setUserRating(nextUserRating)
      setSavedUserRating(nextUserRating)
      setUserComment(nextUserComment)
      setSavedUserComment(nextUserComment)
      setStats((currentStats) => currentStats
        ? {
          ...currentStats,
          rating: Number(result.rating ?? currentStats.rating),
          rating_count: Number(result.rating_count ?? currentStats.rating_count),
        }
        : currentStats)
      setIsRatingDialogOpen(false)
      toast({ title: '评价已更新', description: '你的评分或评论已保存' })
      return
    }

    toast({
      title: '评价失败',
      description: result.error || '未知错误',
      variant: 'destructive',
    })
  }

  if (loading) {
    return (
      <div className="flex items-center gap-4 text-sm text-muted-foreground">
        <div className="flex items-center gap-1">
          <Download className="h-4 w-4" />
          <span>-</span>
        </div>
        <div className="flex items-center gap-1">
          <Star className="h-4 w-4" />
          <span>-</span>
        </div>
      </div>
    )
  }

  if (!stats) {
    return null
  }

  if (compact) {
    return (
      <div className="flex items-center gap-4 text-sm text-muted-foreground">
        <div className="flex items-center gap-1" title={`下载量: ${stats.downloads.toLocaleString()}`}>
          <Download className="h-4 w-4" />
          <span>{stats.downloads.toLocaleString()}</span>
        </div>
        <div className="flex items-center gap-1" title={`评分: ${stats.rating.toFixed(1)} (${stats.rating_count} 条评分)`}>
          <Star className="h-4 w-4 fill-yellow-400 text-yellow-400" />
          <span>{stats.rating.toFixed(1)}</span>
        </div>
        <div className="flex items-center gap-1" title={`点赞数: ${stats.likes}`}>
          <ThumbsUp className="h-4 w-4" />
          <span>{stats.likes}</span>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <div className="flex flex-col items-center rounded-lg border bg-card p-3">
          <Download className="mb-1 h-5 w-5 text-muted-foreground" />
          <span className="text-2xl font-bold">{stats.downloads.toLocaleString()}</span>
          <span className="text-xs text-muted-foreground">下载量</span>
        </div>

        <div className="flex flex-col items-center rounded-lg border bg-card p-3">
          <Star className="mb-1 h-5 w-5 fill-yellow-400 text-yellow-400" />
          <span className="text-2xl font-bold">{stats.rating.toFixed(1)}</span>
          <span className="text-xs text-muted-foreground">{stats.rating_count} 条评分</span>
        </div>

        <div className="flex flex-col items-center rounded-lg border bg-card p-3">
          <ThumbsUp className="mb-1 h-5 w-5 text-green-500" />
          <span className="text-2xl font-bold">{stats.likes}</span>
          <span className="text-xs text-muted-foreground">点赞</span>
        </div>

        <div className="flex flex-col items-center rounded-lg border bg-card p-3">
          <ThumbsDown className="mb-1 h-5 w-5 text-red-500" />
          <span className="text-2xl font-bold">{stats.dislikes}</span>
          <span className="text-xs text-muted-foreground">点踩</span>
        </div>
      </div>

      <div className="flex items-center gap-2">
        <Button
          variant={liked ? 'default' : 'outline'}
          size="sm"
          onClick={handleLike}
          disabled={actionLoading !== null}
        >
          <ThumbsUp className="mr-1 h-4 w-4" />
          {liked ? '已点赞' : '点赞'}
        </Button>

        <Button
          variant={disliked ? 'destructive' : 'outline'}
          size="sm"
          onClick={handleDislike}
          disabled={actionLoading !== null}
        >
          <ThumbsDown className="mr-1 h-4 w-4" />
          {disliked ? '已点踩' : '点踩'}
        </Button>

        <Dialog open={isRatingDialogOpen} onOpenChange={setIsRatingDialogOpen}>
          <DialogTrigger asChild>
            <Button variant="default" size="sm" disabled={actionLoading !== null}>
              <Star className="mr-1 h-4 w-4" />
              {userRating > 0 || savedUserComment ? '修改评价' : '评价'}
            </Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>评价插件</DialogTitle>
              <DialogDescription>可以单独评分或评论；再次提交会更新你的评价。</DialogDescription>
            </DialogHeader>

            <div className="space-y-4 py-4">
              <div className="flex flex-col items-center gap-2">
                <div className="flex gap-2">
                  {[1, 2, 3, 4, 5].map((star) => (
                    <button
                      key={star}
                      type="button"
                      onClick={() => setUserRating(star)}
                      className="focus:outline-none"
                    >
                      <Star
                        className={`h-8 w-8 transition-colors ${
                          star <= userRating
                            ? 'fill-yellow-400 text-yellow-400'
                            : 'text-muted-foreground hover:text-yellow-300'
                        }`}
                      />
                    </button>
                  ))}
                </div>
                <span className="text-sm text-muted-foreground">
                  {userRating === 0 && '点击星星进行评分'}
                  {userRating === 1 && '很差'}
                  {userRating === 2 && '一般'}
                  {userRating === 3 && '还行'}
                  {userRating === 4 && '不错'}
                  {userRating === 5 && '非常好'}
                </span>
              </div>

              <div>
                <label htmlFor="plugin-rating-comment" className="mb-2 block text-sm font-medium">
                  评论
                </label>
                <Textarea
                  value={userComment}
                  id="plugin-rating-comment"
                  onChange={(event) => setUserComment(event.target.value)}
                  placeholder="分享你的使用体验..."
                  rows={4}
                  maxLength={500}
                />
                <div className="mt-1 text-right text-xs text-muted-foreground">
                  {userComment.length} / 500
                </div>
              </div>
            </div>

            <DialogFooter>
              <Button variant="outline" onClick={() => setIsRatingDialogOpen(false)}>
                取消
              </Button>
              <Button
                onClick={handleSubmitRating}
                disabled={actionLoading !== null || (userRating === 0 && userComment === savedUserComment)}
              >
                {actionLoading === 'rating' ? '提交中...' : '提交评价'}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>

      {stats.recent_ratings && stats.recent_ratings.length > 0 && (
        <div className="space-y-2">
          <h4 className="text-sm font-semibold">最近评价</h4>
          <div className="space-y-3">
            {stats.recent_ratings.map((rating, index) => (
              <div key={`${rating.user_id}-${rating.created_at}-${index}`} className="rounded-lg border bg-muted/50 p-3">
                <div className="mb-2 flex items-center justify-between">
                  <div className="flex gap-1">
                    {rating.rating == null ? (
                      <span className="text-xs text-muted-foreground">仅评论</span>
                    ) : (
                      [1, 2, 3, 4, 5].map((star) => (
                        <Star
                          key={star}
                          className={`h-3 w-3 ${
                            star <= Number(rating.rating)
                              ? 'fill-yellow-400 text-yellow-400'
                              : 'text-muted-foreground'
                          }`}
                        />
                      ))
                    )}
                  </div>
                  <span className="text-xs text-muted-foreground">
                    {new Date(rating.created_at).toLocaleDateString()}
                  </span>
                </div>
                {rating.comment && (
                  <p className="text-sm text-muted-foreground">{rating.comment}</p>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
