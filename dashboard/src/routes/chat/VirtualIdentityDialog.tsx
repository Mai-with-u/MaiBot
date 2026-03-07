import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from '@/components/ui/input'
import { Label } from "@/components/ui/label"
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { cn } from '@/lib/utils'
import { Globe, Loader2, Search, UserCircle2, Users } from 'lucide-react'

import type { PersonInfo, PlatformInfo, VirtualIdentityConfig } from './types'

interface VirtualIdentityDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  platforms: PlatformInfo[]
  persons: PersonInfo[]
  isLoadingPlatforms: boolean
  isLoadingPersons: boolean
  personSearchQuery: string
  setPersonSearchQuery: (query: string) => void
  tempVirtualConfig: VirtualIdentityConfig
  setTempVirtualConfig: React.Dispatch<React.SetStateAction<VirtualIdentityConfig>>
  onSelectPerson: (person: PersonInfo) => void
  onCreateVirtualTab: () => void
}

export function VirtualIdentityDialog({
  open,
  onOpenChange,
  platforms,
  persons,
  isLoadingPlatforms,
  isLoadingPersons,
  personSearchQuery,
  setPersonSearchQuery,
  tempVirtualConfig,
  setTempVirtualConfig,
  onSelectPerson,
  onCreateVirtualTab,
}: VirtualIdentityDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[500px] max-h-[85vh] overflow-hidden flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <UserCircle2 className="h-5 w-5" />
            新建虚拟身份对话
          </DialogTitle>
          <DialogDescription>
            选择一个麦麦已认识的用户,以该用户的身份与麦麦对话。麦麦将使用她对该用户的记忆和认知来回应。
          </DialogDescription>
        </DialogHeader>
        
        <div className="space-y-4 flex-1 overflow-hidden flex flex-col">
          {/* 平台选择 */}
          <div className="space-y-2">
            <Label className="flex items-center gap-2">
              <Globe className="h-4 w-4" />
              选择平台
            </Label>
            <Select
              value={tempVirtualConfig.platform}
              onValueChange={(value) => {
                setTempVirtualConfig(prev => ({
                  ...prev,
                  platform: value,
                  personId: '',
                  userId: '',
                  userName: '',
                }))
              }}
            >
              <SelectTrigger disabled={isLoadingPlatforms}>
                <SelectValue placeholder={isLoadingPlatforms ? "加载中..." : "选择平台"} />
              </SelectTrigger>
              <SelectContent>
                {platforms.map((p) => (
                  <SelectItem key={p.platform} value={p.platform}>
                    {p.platform} ({p.count} 人)
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {/* 用户搜索和选择 */}
          {tempVirtualConfig.platform && (
            <div className="space-y-2 flex-1 overflow-hidden flex flex-col">
              <Label className="flex items-center gap-2">
                <Users className="h-4 w-4" />
                选择用户
              </Label>
              <div className="relative">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                <Input
                  placeholder="搜索用户名..."
                  value={personSearchQuery}
                  onChange={(e) => setPersonSearchQuery(e.target.value)}
                  className="pl-9"
                />
              </div>
              <ScrollArea className="h-[250px] border rounded-md">
                <div className="p-2">
                  {isLoadingPersons ? (
                    <div className="flex items-center justify-center py-8">
                      <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                    </div>
                  ) : persons.length === 0 ? (
                    <div className="flex flex-col items-center justify-center py-8 text-muted-foreground">
                      <Users className="h-8 w-8 mb-2 opacity-50" />
                      <p className="text-sm">没有找到用户</p>
                    </div>
                  ) : (
                    <div className="space-y-1">
                      {persons.map((person) => (
                        <button
                          key={person.person_id}
                          onClick={() => onSelectPerson(person)}
                          className={cn(
                            "w-full flex items-center gap-3 p-2 rounded-md text-left transition-colors",
                            tempVirtualConfig.personId === person.person_id
                              ? "bg-primary text-primary-foreground"
                              : "hover:bg-muted"
                          )}
                        >
                          <Avatar className="h-8 w-8 shrink-0">
                            <AvatarFallback className={cn(
                              "text-xs",
                              tempVirtualConfig.personId === person.person_id
                                ? "bg-primary-foreground/20"
                                : "bg-muted"
                            )}>
                              {(person.nickname || person.person_name || '?').charAt(0)}
                            </AvatarFallback>
                          </Avatar>
                          <div className="min-w-0 flex-1">
                            <div className="font-medium truncate">
                              {person.nickname || person.person_name}
                            </div>
                            <div className={cn(
                              "text-xs truncate",
                              tempVirtualConfig.personId === person.person_id
                                ? "text-primary-foreground/70"
                                : "text-muted-foreground"
                            )}>
                              ID: {person.user_id}
                              {person.is_known && " · 已认识"}
                            </div>
                          </div>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              </ScrollArea>
            </div>
          )}

          {/* 虚拟群名配置 */}
          {tempVirtualConfig.personId && (
            <div className="space-y-2">
              <Label>虚拟群名（可选）</Label>
              <Input
                placeholder="WebUI虚拟群聊"
                value={tempVirtualConfig.groupName}
                onChange={(e) => setTempVirtualConfig(prev => ({
                  ...prev,
                  groupName: e.target.value
                }))}
              />
              <p className="text-xs text-muted-foreground">
                麦麦会认为这是一个名为此名称的群聊
              </p>
            </div>
          )}
        </div>

        <DialogFooter className="gap-2 sm:gap-0">
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button 
            onClick={onCreateVirtualTab}
            disabled={!tempVirtualConfig.platform || !tempVirtualConfig.personId}
          >
            创建对话
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
