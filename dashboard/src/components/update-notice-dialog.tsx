import { useCallback, useEffect, useRef, useState, type CSSProperties } from 'react'
import { Check } from 'lucide-react'

import { MarkdownRenderer } from '@/components/markdown-renderer'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { ackUpdateNotice, getUpdateNotice, type UpdateNoticeResponse } from '@/lib/system-api'

export function UpdateNoticeDialog() {
  const [notice, setNotice] = useState<UpdateNoticeResponse | null>(null)
  const [open, setOpen] = useState(false)
  const ackedRef = useRef(false)

  useEffect(() => {
    let cancelled = false

    async function loadNotice() {
      try {
        const response = await getUpdateNotice()
        if (cancelled || !response.pending) {
          return
        }
        ackedRef.current = false
        setNotice(response)
        setOpen(true)
      } catch (error) {
        console.error('[UpdateNotice] 获取更新公告失败:', error)
      }
    }

    void loadNotice()

    return () => {
      cancelled = true
    }
  }, [])

  const acknowledgeNotice = useCallback(async () => {
    if (ackedRef.current) {
      setOpen(false)
      return
    }

    ackedRef.current = true
    setOpen(false)
    try {
      await ackUpdateNotice()
    } catch (error) {
      console.error('[UpdateNotice] 确认更新公告失败:', error)
    }
  }, [])

  if (!notice) {
    return null
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(nextOpen) => {
        if (nextOpen) {
          setOpen(true)
          return
        }
        void acknowledgeNotice()
      }}
    >
      <DialogContent style={{ '--dialog-width': '44rem' } as CSSProperties}>
        <DialogHeader>
          <DialogTitle>更新内容</DialogTitle>
        </DialogHeader>
        <DialogBody className="max-h-[min(70vh,42rem)]">
          <MarkdownRenderer content={notice.content} className="[&_h1:first-child]:mt-0" />
        </DialogBody>
        <DialogFooter>
          <Button type="button" onClick={() => void acknowledgeNotice()}>
            <Check className="h-4 w-4" />
            知道了
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
