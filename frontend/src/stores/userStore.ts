import { create } from 'zustand'
import type { UserProfile } from '../types'

interface UserState {
  selectedUser: UserProfile | null
  setSelectedUser: (user: UserProfile | null) => void
  currentUserName: string | null
  setCurrentUserName: (name: string | null) => void
}

export const useUserStore = create<UserState>()((set) => ({
  selectedUser: null,
  setSelectedUser: (user) => set({ selectedUser: user }),
  currentUserName: localStorage.getItem('sp-user-name'),
  setCurrentUserName: (name) => {
    if (name) localStorage.setItem('sp-user-name', name)
    else localStorage.removeItem('sp-user-name')
    set({ currentUserName: name })
  },
}))
