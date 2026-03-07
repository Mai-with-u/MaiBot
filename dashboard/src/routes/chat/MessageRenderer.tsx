import { cn } from '@/lib/utils'

import type { ChatMessage, MessageSegment } from './types'

// 渲染单个消息段
export function RenderMessageSegment({ segment }: { segment: MessageSegment }) {
  switch (segment.type) {
    case 'text':
      return <span className="whitespace-pre-wrap">{String(segment.data)}</span>
    
    case 'image':
    case 'emoji':
      return (
        <img 
          src={String(segment.data)} 
          alt={segment.type === 'emoji' ? '表情包' : '图片'}
          className={cn(
            "rounded-lg max-w-full",
            segment.type === 'emoji' ? "max-h-32" : "max-h-64"
          )}
          loading="lazy"
          onError={(e) => {
            // 图片加载失败时显示占位符
            const target = e.target as HTMLImageElement
            target.style.display = 'none'
            target.parentElement?.insertAdjacentHTML(
              'beforeend',
              `<span class="text-muted-foreground text-xs">[${segment.type === 'emoji' ? '表情包' : '图片'}加载失败]</span>`
            )
          }}
        />
      )
    
    case 'voice':
      return (
        <div className="flex items-center gap-2">
          <audio 
            controls 
            src={String(segment.data)} 
            className="max-w-[200px] h-8"
          >
            <track kind="captions" src="" label="无字幕" default />
            您的浏览器不支持音频播放
          </audio>
        </div>
      )
    
    case 'video':
      return (
        <video 
          controls 
          src={String(segment.data)} 
          className="rounded-lg max-w-full max-h-64"
        >
          <track kind="captions" src="" label="无字幕" default />
          您的浏览器不支持视频播放
        </video>
      )
    
    case 'face':
      // QQ 原生表情，显示为文本
      return <span className="text-muted-foreground">[表情:{String(segment.data)}]</span>
    
    case 'music':
      return <span className="text-muted-foreground">[音乐分享]</span>
    
    case 'file':
      return <span className="text-muted-foreground">[文件: {String(segment.data)}]</span>
    
    case 'reply':
      return <span className="text-muted-foreground text-xs">[回复消息]</span>
    
    case 'forward':
      return <span className="text-muted-foreground">[转发消息]</span>
    
    case 'unknown':
    default:
      return <span className="text-muted-foreground">[{segment.original_type || '未知消息'}]</span>
  }
}

// 渲染消息内容（支持富文本）
// eslint-disable-next-line @typescript-eslint/no-unused-vars
export function RenderMessageContent({ message, isBot: _isBot }: { message: ChatMessage; isBot: boolean }) {
  // 如果是富文本消息，渲染消息段
  if (message.message_type === 'rich' && message.segments && message.segments.length > 0) {
    return (
      <div className="flex flex-col gap-2">
        {message.segments.map((segment, index) => (
          <RenderMessageSegment key={index} segment={segment} />
        ))}
      </div>
    )
  }
  
  // 普通文本消息
  return <span className="whitespace-pre-wrap">{message.content}</span>
}
