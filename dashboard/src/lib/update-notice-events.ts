export type UpdateNoticeTarget = 'maibot' | 'console'

export const UPDATE_NOTICE_OPEN_EVENT = 'maibot-open-update-notice'

export function openUpdateNotice(target: UpdateNoticeTarget): void {
  window.dispatchEvent(
    new CustomEvent<UpdateNoticeTarget>(UPDATE_NOTICE_OPEN_EVENT, {
      detail: target,
    })
  )
}
