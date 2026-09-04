import { useQuery } from '@tanstack/react-query'

import { ApiError, getCurrentUser, type AuthUser } from '@/api/client'

export const authMeQueryKey = ['auth', 'me'] as const

export function useAuthMe() {
  return useQuery({
    queryKey: authMeQueryKey,
    queryFn: async (): Promise<AuthUser | null> => {
      try {
        const response = await getCurrentUser()
        return response.data
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) {
          return null
        }
        throw error
      }
    },
    retry: false,
    staleTime: 30_000,
  })
}
