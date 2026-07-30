import type { CSSProperties } from 'react'
import { Plus, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'

import type { CustomHitokoto, HitokotoSettings } from './hooks/useMaibotVersion'

interface HitokotoEditorDialogProps {
  initialSettings: HitokotoSettings
  onOpenChange: (open: boolean) => void
  onSave: (settings: HitokotoSettings) => Promise<void>
}

function createCustomHitokoto(): CustomHitokoto {
  return {
    id: crypto.randomUUID(),
    content: '',
    source: '',
  }
}

export function HitokotoEditorDialog({
  initialSettings,
  onOpenChange,
  onSave,
}: HitokotoEditorDialogProps) {
  const { t } = useTranslation()
  const [settings, setSettings] = useState<HitokotoSettings>(() => ({
    defaultEnabled: initialSettings.defaultEnabled,
    customItems: initialSettings.customItems.map((item) => ({ ...item })),
  }))
  const [isSaving, setIsSaving] = useState(false)

  const updateItem = (id: string, patch: Partial<CustomHitokoto>) => {
    setSettings((current) => ({
      ...current,
      customItems: current.customItems.map((item) =>
        item.id === id ? { ...item, ...patch } : item
      ),
    }))
  }

  const removeItem = (id: string) => {
    setSettings((current) => ({
      ...current,
      customItems: current.customItems.filter((item) => item.id !== id),
    }))
  }

  const handleSave = async () => {
    setIsSaving(true)
    try {
      await onSave(settings)
      onOpenChange(false)
    } finally {
      setIsSaving(false)
    }
  }

  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent style={{ '--dialog-width': '44rem' } as CSSProperties}>
        <DialogHeader>
          <DialogTitle>{t('home.hitokoto.editor.title')}</DialogTitle>
          <DialogDescription>{t('home.hitokoto.editor.description')}</DialogDescription>
        </DialogHeader>
        <DialogBody viewportClassName="max-h-[62vh]">
          <div className="space-y-5 pr-1">
            <div className="bg-muted/25 flex items-center justify-between gap-4 rounded-lg border p-4">
              <div className="min-w-0">
                <div className="font-semibold">{t('home.hitokoto.editor.defaultSource')}</div>
                <div className="text-muted-foreground mt-1 text-sm">
                  {t('home.hitokoto.editor.defaultSourceDescription')}
                </div>
              </div>
              <Switch
                checked={settings.defaultEnabled}
                onCheckedChange={(checked) =>
                  setSettings((current) => ({ ...current, defaultEnabled: checked }))
                }
                aria-label={t('home.hitokoto.editor.defaultSource')}
              />
            </div>

            <section className="space-y-3">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <h3 className="font-semibold">{t('home.hitokoto.editor.customList')}</h3>
                  <p className="text-muted-foreground mt-0.5 text-sm">
                    {t('home.hitokoto.editor.customListDescription')}
                  </p>
                </div>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="shrink-0 gap-1.5"
                  onClick={() =>
                    setSettings((current) => ({
                      ...current,
                      customItems: [...current.customItems, createCustomHitokoto()],
                    }))
                  }
                >
                  <Plus className="h-4 w-4" />
                  {t('home.hitokoto.editor.add')}
                </Button>
              </div>

              {settings.customItems.length === 0 ? (
                <button
                  type="button"
                  className="text-muted-foreground hover:border-primary/35 hover:bg-accent/20 flex w-full flex-col items-center justify-center gap-2 rounded-lg border border-dashed px-4 py-8 text-sm transition-colors"
                  onClick={() =>
                    setSettings((current) => ({
                      ...current,
                      customItems: [createCustomHitokoto()],
                    }))
                  }
                >
                  <Plus className="h-5 w-5" />
                  <span>{t('home.hitokoto.editor.empty')}</span>
                </button>
              ) : (
                <div className="space-y-3">
                  {settings.customItems.map((item, index) => (
                    <div key={item.id} className="rounded-lg border p-3">
                      <div className="mb-2 flex items-center justify-between">
                        <span className="text-muted-foreground text-xs font-semibold">
                          {t('home.hitokoto.editor.item', { index: index + 1 })}
                        </span>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="text-muted-foreground hover:text-destructive h-7 w-7"
                          aria-label={t('home.hitokoto.editor.remove', { index: index + 1 })}
                          onClick={() => removeItem(item.id)}
                        >
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </div>
                      <Textarea
                        value={item.content}
                        minHeight={72}
                        maxHeight={160}
                        placeholder={t('home.hitokoto.editor.contentPlaceholder')}
                        aria-label={t('home.hitokoto.editor.content', { index: index + 1 })}
                        onChange={(event) => updateItem(item.id, { content: event.target.value })}
                      />
                      <Input
                        value={item.source}
                        className="mt-2"
                        placeholder={t('home.hitokoto.editor.sourcePlaceholder')}
                        aria-label={t('home.hitokoto.editor.source', { index: index + 1 })}
                        onChange={(event) => updateItem(item.id, { source: event.target.value })}
                      />
                    </div>
                  ))}
                </div>
              )}

              {!settings.defaultEnabled && settings.customItems.length === 0 && (
                <p className="text-muted-foreground rounded-lg border border-dashed px-3 py-2 text-xs">
                  {t('home.hitokoto.editor.blankHint')}
                </p>
              )}
            </section>
          </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" disabled={isSaving} onClick={() => onOpenChange(false)}>
            {t('common.cancel')}
          </Button>
          <Button disabled={isSaving} onClick={() => void handleSave()}>
            {isSaving ? t('home.hitokoto.editor.saving') : t('common.save')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
