import { createContext, useContext } from 'react'

export const ConfigSectionDialogContext = createContext(false)

/** 字段编辑器位于配置弹窗内时，弹窗已提供标题与外框，编辑器应去掉自己的卡片外壳。 */
export const useInConfigSectionDialog = () => useContext(ConfigSectionDialogContext)
