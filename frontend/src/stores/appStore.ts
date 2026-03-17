import { create } from 'zustand'
import { persist } from 'zustand/middleware'

interface AppState {
  sport: string
  setSport: (sport: string) => void
  activeStrategy: string
  setActiveStrategy: (strategy: string) => void
}

export const useAppStore = create<AppState>()(
  persist(
    (set) => ({
      sport: 'all',
      setSport: (sport) => set({ sport }),
      activeStrategy: 'ensemble',
      setActiveStrategy: (strategy) => set({ activeStrategy: strategy }),
    }),
    { name: 'sp-app-prefs' }
  )
)
