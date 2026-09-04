import { ConfigProvider } from 'antd'
import koKR from 'antd/locale/ko_KR'
import { QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider } from 'react-router-dom'

import { queryClient } from '@/app/queryClient'
import { router } from '@/app/router'

export function App() {
  return (
    <ConfigProvider
      locale={koKR}
      theme={{
        token: {
          colorPrimary: '#0f5c4c',
          borderRadius: 6,
        },
      }}
    >
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </ConfigProvider>
  )
}
