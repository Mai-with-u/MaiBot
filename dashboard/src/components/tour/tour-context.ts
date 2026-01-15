import { createContext } from 'react'
import type { TourContextType } from './types'

export const TourContext = createContext<TourContextType | null>(null)
