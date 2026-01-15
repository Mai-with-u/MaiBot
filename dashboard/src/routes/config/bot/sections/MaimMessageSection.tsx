import React, { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Plus, Trash2 } from 'lucide-react'
import type { MaimMessageConfig } from '../types'

interface MaimMessageSectionProps {
  config: MaimMessageConfig
  onChange: (config: MaimMessageConfig) => void
}

export const MaimMessageSection = React.memo(function MaimMessageSection({ config, onChange }: MaimMessageSectionProps) {
  const [newToken, setNewToken] = useState('')
  const [newApiKey, setNewApiKey] = useState('')

  const addToken = () => {
    if (newToken && !config.auth_token.includes(newToken)) {
      onChange({ ...config, auth_token: [...config.auth_token, newToken] })
      setNewToken('')
    }
  }

  const removeToken = (index: number) => {
    onChange({
      ...config,
      auth_token: config.auth_token.filter((_, i) => i !== index),
    })
  }

  const addApiKey = () => {
    if (newApiKey && !config.api_server_allowed_api_keys.includes(newApiKey)) {
      onChange({ ...config, api_server_allowed_api_keys: [...config.api_server_allowed_api_keys, newApiKey] })
      setNewApiKey('')
    }
  }

  const removeApiKey = (index: number) => {
    onChange({
      ...config,
      api_server_allowed_api_keys: config.api_server_allowed_api_keys.filter((_, i) => i !== index),
    })
  }

  return (
    <div className="rounded-lg border bg-card p-4 sm:p-6 space-y-6">
      {/* 认证令牌 */}
      <div>
        <h3 className="text-lg font-semibold mb-2">旧版 API 认证令牌</h3>
        <p className="text-sm text-muted-foreground mb-3">用于旧版 API 验证，为空则不启用验证</p>
        <div className="flex gap-2 mb-2">
          <Input
            value={newToken}
            onChange={(e) => setNewToken(e.target.value)}
            placeholder="输入认证令牌"
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault()
                addToken()
              }
            }}
          />
          <Button onClick={addToken} size="sm">
            <Plus className="h-4 w-4" strokeWidth={2} fill="none" />
          </Button>
        </div>
        <div className="space-y-2">
          {config.auth_token.map((token, index) => (
            <div
              key={index}
              className="flex items-center justify-between bg-secondary px-3 py-2 rounded-md"
            >
              <span className="text-sm font-mono">{token}</span>
              <Button
                variant="ghost"
                size="sm"
                className="h-6 w-6 p-0"
                onClick={() => removeToken(index)}
              >
                <Trash2 className="h-3 w-3" strokeWidth={2} fill="none" />
              </Button>
            </div>
          ))}
        </div>
      </div>

      {/* 新版 API Server */}
      <div>
        <h3 className="text-lg font-semibold mb-4">新版 API Server 配置</h3>
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <div className="space-y-0.5">
              <Label>启用新版 API Server</Label>
              <p className="text-sm text-muted-foreground">
                是否启用额外的新版 API Server（额外监听端口）
              </p>
            </div>
            <Switch
              checked={config.enable_api_server}
              onCheckedChange={(checked) => onChange({ ...config, enable_api_server: checked })}
            />
          </div>

          {config.enable_api_server && (
            <>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div className="grid gap-2">
                  <Label>主机地址</Label>
                  <Input
                    value={config.api_server_host}
                    onChange={(e) => onChange({ ...config, api_server_host: e.target.value })}
                    placeholder="0.0.0.0"
                  />
                </div>

                <div className="grid gap-2">
                  <Label>端口号</Label>
                  <Input
                    type="number"
                    value={config.api_server_port}
                    onChange={(e) => onChange({ ...config, api_server_port: parseInt(e.target.value) })}
                    placeholder="8090"
                  />
                </div>
              </div>

              <div className="flex items-center space-x-2">
                <Switch
                  checked={config.api_server_use_wss}
                  onCheckedChange={(checked) => onChange({ ...config, api_server_use_wss: checked })}
                />
                <Label>启用 WSS 安全连接</Label>
              </div>

              {config.api_server_use_wss && (
                <div className="grid gap-4">
                  <div className="grid gap-2">
                    <Label>SSL 证书文件路径</Label>
                    <Input
                      value={config.api_server_cert_file}
                      onChange={(e) => onChange({ ...config, api_server_cert_file: e.target.value })}
                      placeholder="cert.pem"
                    />
                  </div>

                  <div className="grid gap-2">
                    <Label>SSL 密钥文件路径</Label>
                    <Input
                      value={config.api_server_key_file}
                      onChange={(e) => onChange({ ...config, api_server_key_file: e.target.value })}
                      placeholder="key.pem"
                    />
                  </div>
                </div>
              )}

              {/* API Keys */}
              <div>
                <Label className="mb-2 block">允许的 API Key 列表</Label>
                <p className="text-sm text-muted-foreground mb-2">为空则允许所有连接</p>
                <div className="flex gap-2 mb-2">
                  <Input
                    value={newApiKey}
                    onChange={(e) => setNewApiKey(e.target.value)}
                    placeholder="输入 API Key"
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') {
                        e.preventDefault()
                        addApiKey()
                      }
                    }}
                  />
                  <Button onClick={addApiKey} size="sm">
                    <Plus className="h-4 w-4" strokeWidth={2} fill="none" />
                  </Button>
                </div>
                <div className="space-y-2">
                  {config.api_server_allowed_api_keys.map((apiKey, index) => (
                    <div
                      key={index}
                      className="flex items-center justify-between bg-secondary px-3 py-2 rounded-md"
                    >
                      <span className="text-sm font-mono">{apiKey}</span>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-6 w-6 p-0"
                        onClick={() => removeApiKey(index)}
                      >
                        <Trash2 className="h-3 w-3" strokeWidth={2} fill="none" />
                      </Button>
                    </div>
                  ))}
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
})