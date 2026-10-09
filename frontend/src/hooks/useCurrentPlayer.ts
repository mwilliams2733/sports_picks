import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { useUserStore } from '../stores/userStore'

/** The player chosen in the top bar, from the same cached users list the chip reads. */
export function useCurrentPlayer() {
  const { currentUserName } = useUserStore()
  const users = useQuery({ queryKey: ['users', 'list'], queryFn: api.users.list, refetchInterval: 60_000 })
  return users.data?.find(u => u.name === currentUserName)
}
