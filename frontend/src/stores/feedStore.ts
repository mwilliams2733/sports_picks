import { create } from 'zustand'

export interface FeedEvent {
  id: string
  type: string
  userName: string
  message: string
  timestamp: string
}

interface FeedState {
  events: FeedEvent[]
  addEvent: (event: FeedEvent) => void
  wsConnected: boolean
  setWsConnected: (connected: boolean) => void
}

export const useFeedStore = create<FeedState>()((set) => ({
  events: [],
  addEvent: (event) => set((state) => ({
    events: [event, ...state.events].slice(0, 50)
  })),
  wsConnected: false,
  setWsConnected: (connected) => set({ wsConnected: connected }),
}))
