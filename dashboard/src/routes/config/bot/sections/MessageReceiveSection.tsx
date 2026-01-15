import React, { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog'
import { Plus, Trash2, AlertTriangle } from 'lucide-react'
import type { MessageReceiveConfig } from '../types'

interface MessageReceiveSectionProps {
  config: MessageReceiveConfig
  onChange: (config: MessageReceiveConfig) => void
}

/**
 * 消息过滤配置模块
 * 管理 ban_words、ban_msgs_regex 和 mute_group_list
 */
export default function MessageReceiveSection({
  config,
  onChange,
}: MessageReceiveSectionProps) {
  const [newBanWord, setNewBanWord] = useState('')
  const [newBanRegex, setNewBanRegex] = useState('')

  // === 禁用词管理 ===
  const handleAddBanWord = () => {
    const trimmed = newBanWord.trim()
    if (trimmed && !config.ban_words.includes(trimmed)) {
      onChange({
        ...config,
        ban_words: [...config.ban_words, trimmed],
      })
      setNewBanWord('')
    }
  }

  const handleRemoveBanWord = (index: number) => {
    onChange({
      ...config,
      ban_words: config.ban_words.filter((_, i) => i !== index),
    })
  }

  const handleBanWordKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      e.preventDefault()
      handleAddBanWord()
    }
  }

  // === 禁用正则表达式管理 ===
  const handleAddBanRegex = () => {
    const trimmed = newBanRegex.trim()
    if (trimmed && !config.ban_msgs_regex.includes(trimmed)) {
      // 验证正则表达式语法
      try {
        new RegExp(trimmed)
        onChange({
          ...config,
          ban_msgs_regex: [...config.ban_msgs_regex, trimmed],
        })
        setNewBanRegex('')
      } catch (err) {
        alert(`正则表达式语法错误：${(err as Error).message}`)
      }
    }
  }

  const handleRemoveBanRegex = (index: number) => {
    onChange({
      ...config,
      ban_msgs_regex: config.ban_msgs_regex.filter((_, i) => i !== index),
    })
  }

  const handleBanRegexKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      e.preventDefault()
      handleAddBanRegex()
    }
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>消息过滤配置</CardTitle>
          <CardDescription>
            配置消息过滤规则，过滤特定消息或在特定群组启用静默模式
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Tabs defaultValue="ban_words" className="w-full">
            <TabsList className="grid w-full grid-cols-2">
              <TabsTrigger value="ban_words">禁用关键词</TabsTrigger>
              <TabsTrigger value="ban_regex">禁用正则</TabsTrigger>
            </TabsList>

            {/* 禁用关键词 Tab */}
            <TabsContent value="ban_words" className="space-y-4">
              <div className="space-y-2">
                <div className="flex items-start gap-2">
                  <AlertTriangle className="h-4 w-4 text-yellow-500 mt-1 flex-shrink-0" />
                  <p className="text-sm text-muted-foreground">
                    包含以下关键词的消息将被过滤，Bot 不会读取这些消息
                  </p>
                </div>

                <div className="flex gap-2">
                  <Input
                    placeholder="输入要禁用的关键词（按回车添加）"
                    value={newBanWord}
                    onChange={(e) => setNewBanWord(e.target.value)}
                    onKeyDown={handleBanWordKeyDown}
                  />
                  <Button onClick={handleAddBanWord} size="icon">
                    <Plus className="h-4 w-4" />
                  </Button>
                </div>

                {config.ban_words.length === 0 ? (
                  <div className="rounded-md border border-dashed p-8 text-center">
                    <p className="text-sm text-muted-foreground">
                      暂无禁用关键词，点击上方添加
                    </p>
                  </div>
                ) : (
                  <div className="space-y-2">
                    {config.ban_words.map((word, index) => (
                      <div
                        key={index}
                        className="flex items-center justify-between rounded-md border p-3"
                      >
                        <code className="text-sm">{word}</code>
                        <AlertDialog>
                          <AlertDialogTrigger asChild>
                            <Button variant="ghost" size="icon">
                              <Trash2 className="h-4 w-4 text-destructive" />
                            </Button>
                          </AlertDialogTrigger>
                          <AlertDialogContent>
                            <AlertDialogHeader>
                              <AlertDialogTitle>确认删除</AlertDialogTitle>
                              <AlertDialogDescription>
                                确定要删除关键词 <code>"{word}"</code> 吗？
                              </AlertDialogDescription>
                            </AlertDialogHeader>
                            <AlertDialogFooter>
                              <AlertDialogCancel>取消</AlertDialogCancel>
                              <AlertDialogAction
                                onClick={() => handleRemoveBanWord(index)}
                              >
                                删除
                              </AlertDialogAction>
                            </AlertDialogFooter>
                          </AlertDialogContent>
                        </AlertDialog>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </TabsContent>

            {/* 禁用正则表达式 Tab */}
            <TabsContent value="ban_regex" className="space-y-4">
              <div className="space-y-2">
                <div className="flex items-start gap-2">
                  <AlertTriangle className="h-4 w-4 text-yellow-500 mt-1 flex-shrink-0" />
                  <div className="text-sm text-muted-foreground space-y-1">
                    <p>匹配以下正则表达式的消息将被过滤</p>
                    <p className="text-xs">
                      ⚠️ 若不了解正则表达式，请勿随意修改
                    </p>
                  </div>
                </div>

                <div className="flex gap-2">
                  <Textarea
                    placeholder="输入正则表达式（按回车添加）&#10;示例：https?://[^\s]+ 匹配链接"
                    value={newBanRegex}
                    onChange={(e) => setNewBanRegex(e.target.value)}
                    onKeyDown={handleBanRegexKeyDown}
                    className="min-h-[60px] font-mono text-sm"
                  />
                  <Button onClick={handleAddBanRegex} size="icon">
                    <Plus className="h-4 w-4" />
                  </Button>
                </div>

                {config.ban_msgs_regex.length === 0 ? (
                  <div className="rounded-md border border-dashed p-8 text-center">
                    <p className="text-sm text-muted-foreground">
                      暂无禁用正则表达式，点击上方添加
                    </p>
                  </div>
                ) : (
                  <div className="space-y-2">
                    {config.ban_msgs_regex.map((regex, index) => (
                      <div
                        key={index}
                        className="flex items-center justify-between rounded-md border p-3"
                      >
                        <code className="text-sm font-mono flex-1 break-all">
                          {regex}
                        </code>
                        <AlertDialog>
                          <AlertDialogTrigger asChild>
                            <Button variant="ghost" size="icon" className="ml-2 flex-shrink-0">
                              <Trash2 className="h-4 w-4 text-destructive" />
                            </Button>
                          </AlertDialogTrigger>
                          <AlertDialogContent>
                            <AlertDialogHeader>
                              <AlertDialogTitle>确认删除</AlertDialogTitle>
                              <AlertDialogDescription>
                                确定要删除正则表达式 <code>"{regex}"</code> 吗？
                              </AlertDialogDescription>
                            </AlertDialogHeader>
                            <AlertDialogFooter>
                              <AlertDialogCancel>取消</AlertDialogCancel>
                              <AlertDialogAction
                                onClick={() => handleRemoveBanRegex(index)}
                              >
                                删除
                              </AlertDialogAction>
                            </AlertDialogFooter>
                          </AlertDialogContent>
                        </AlertDialog>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </TabsContent>
          </Tabs>
        </CardContent>
      </Card>
    </div>
  )
}
