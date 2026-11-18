import { useEffect, useRef, useState } from 'react'
import { Pencil } from 'lucide-react'

interface ObservationQuoteProps {
  text: string
  editLabel: string
  onEdit: () => void
}

export function ObservationQuote({ text, editLabel, onEdit }: ObservationQuoteProps) {
  const viewportRef = useRef<HTMLSpanElement>(null)
  const textRef = useRef<HTMLSpanElement>(null)
  const [overflowing, setOverflowing] = useState(false)

  useEffect(() => {
    const viewport = viewportRef.current!
    const content = textRef.current!
    let timer: ReturnType<typeof setTimeout> | undefined
    let disposed = false
    const measure = () => {
      clearTimeout(timer)
      content.style.transform = 'translateX(0)'
      const overflow = content.scrollWidth - viewport.clientWidth
      setOverflowing(overflow > 1)
      if (overflow <= 1 || !content.firstChild) return
      // 将末尾移出右侧 2rem 渐变区，保证最后几个字也能完整读到。
      const distance = overflow + 2 * parseFloat(getComputedStyle(document.documentElement).fontSize)

      // 以实际字宽为步长跳帧，避免平滑跑马灯；首尾停留，方便读完再循环。
      const range = document.createRange()
      const offsets = [0]
      let offset = 0
      for (const character of Array.from(text)) {
        offset += character.length
        range.setStart(content.firstChild, 0)
        range.setEnd(content.firstChild, offset)
        const width = range.getBoundingClientRect().width
        offsets.push(Math.min(width, distance))
        if (width >= distance) break
      }
      let frame = 0
      const advance = () => {
        frame = (frame + 1) % offsets.length
        content.style.transform = `translateX(-${offsets[frame]}px)`
        timer = setTimeout(advance, frame === 0 || frame === offsets.length - 1 ? 2200 : 180)
      }
      timer = setTimeout(advance, 2200)
    }
    const observer = new ResizeObserver(measure)
    observer.observe(viewport)
    observer.observe(content)
    measure()
    void document.fonts.ready.then(() => { if (!disposed) measure() })
    return () => {
      disposed = true
      observer.disconnect()
      clearTimeout(timer)
    }
  }, [text])

  return (
    <p data-observation-quote="true">
      <button type="button" aria-label={editLabel} title={editLabel} onClick={onEdit}>
        <Pencil aria-hidden="true" />
      </button>
      <span ref={viewportRef} data-quote-viewport="true" data-overflow={overflowing} title={text}>
        <span ref={textRef} data-quote-text="true">{text || '\u00a0'}</span>
      </span>
    </p>
  )
}
