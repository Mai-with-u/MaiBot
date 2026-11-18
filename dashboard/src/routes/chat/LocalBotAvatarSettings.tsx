import { useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { ConversationAvatar } from '@/components/ui/conversation'
import { uploadWebuiUserAvatar, useResolvedAvatarUrl } from '@/lib/avatar-url'
import { getDiscoveredBotAccounts, type BotPlatformAccount } from '@/lib/bot-accounts-api'

import { getLocalBotUploadId, setLocalBotAvatar, useLocalBotAvatarUrl } from './local-bot-avatar'

function PlatformAvatarOption({
  account,
  disabled,
  onSelect,
}: {
  account: BotPlatformAccount
  disabled: boolean
  onSelect: () => void
}) {
  const avatar = useResolvedAvatarUrl(account.platform, account.account_id, 'user', undefined, {
    accountId: account.account_id,
  })
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onSelect}
      className="hover:bg-muted flex w-full items-center gap-2 rounded-md p-2 text-left text-xs disabled:opacity-50"
    >
      <ConversationAvatar src={avatar} name={account.platform} bot />
      <span className="min-w-0 break-all">
        {account.platform} · {account.account_id}
      </span>
    </button>
  )
}

export function LocalBotAvatarSettings({ botQq }: { botQq?: string }) {
  const [accounts, setAccounts] = useState<BotPlatformAccount[]>([])
  const [loading, setLoading] = useState(true)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')
  const input = useRef<HTMLInputElement>(null)
  const avatar = useLocalBotAvatarUrl(botQq)

  useEffect(() => {
    let active = true
    getDiscoveredBotAccounts()
      .then((data) => {
        if (active) setAccounts(data)
      })
      .catch((error) => {
        if (active) setError(String(error))
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [])

  async function upload(file: File) {
    setUploading(true)
    setError('')
    try {
      if (file.size > 5 * 1024 * 1024) throw new Error('头像文件不能超过 5 MB')
      // 独立本地头像槽位，不覆盖任何平台账号或聊天用户的头像。
      const userId = getLocalBotUploadId()
      await uploadWebuiUserAvatar(userId, file)
      setLocalBotAvatar({ platform: 'internal', userId, version: Date.now() })
    } catch (error) {
      setError(error instanceof Error ? error.message : String(error))
    } finally {
      setUploading(false)
    }
  }

  return (
    <div className="mt-3 space-y-2 border-t pt-3">
      <div className="flex items-center gap-2">
        <ConversationAvatar src={avatar} name="Bot" bot />
        <div>
          <p className="text-sm">Bot 头像</p>
          <p className="text-muted-foreground text-xs">仅用于本地聊天室，保存在当前浏览器</p>
        </div>
      </div>
      <div className="flex gap-2">
        <Button
          size="sm"
          variant="outline"
          disabled={uploading}
          onClick={() => input.current?.click()}
        >
          {uploading ? '上传中…' : '上传头像'}
        </Button>
        <Button
          size="sm"
          variant="ghost"
          disabled={uploading}
          onClick={() => setLocalBotAvatar(null)}
        >
          恢复默认
        </Button>
      </div>
      <input
        ref={input}
        type="file"
        className="hidden"
        accept="image/jpeg,image/png,image/webp,image/gif,image/bmp"
        onChange={(event) => {
          const file = event.currentTarget.files?.[0]
          event.currentTarget.value = ''
          if (file) void upload(file)
        }}
      />
      <p className="text-muted-foreground text-xs">选择平台 Bot 头像</p>
      <div className="max-h-48 overflow-y-auto">
        {accounts.map((account) => (
          <PlatformAvatarOption
            key={account.id}
            account={account}
            disabled={uploading}
            onSelect={() =>
              setLocalBotAvatar({
                platform: account.platform,
                userId: account.account_id,
                accountId: account.account_id,
              })
            }
          />
        ))}
        {loading && <p className="text-muted-foreground text-xs">加载平台账号中…</p>}
        {!loading && !accounts.length && !error && (
          <p className="text-muted-foreground text-xs">暂无平台账号，可自行上传头像</p>
        )}
      </div>
      {error && (
        <p role="alert" className="text-destructive text-xs">
          {error}
        </p>
      )}
    </div>
  )
}
