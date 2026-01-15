import { useContext } from 'react'
import { TourContext } from './tour-context'

export function useTour() {
  const context = useContext(TourContext)
  if (!context) {
    throw new Error('useTour must be used within a TourProvider')
  }
  return context
}
