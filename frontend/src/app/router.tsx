import { createBrowserRouter } from 'react-router-dom'

import { MainLayout } from '@/layouts/MainLayout'
import { DashboardPage } from '@/pages/DashboardPage'
import { PeopleListPage } from '@/pages/PeopleListPage'
import { PeopleNewPage } from '@/pages/PeopleNewPage'
import { AnalysesPage } from '@/pages/AnalysesPage'
import { SearchPage } from '@/pages/SearchPage'
import { AdminCodesPage } from '@/pages/AdminCodesPage'
import { AdminUsersPage } from '@/pages/AdminUsersPage'
import { AdminAnalysesPage } from '@/pages/AdminAnalysesPage'
import { NotFoundPage } from '@/pages/NotFoundPage'
import { LoginPage } from '@/pages/LoginPage'
import { ForbiddenPage } from '@/pages/ForbiddenPage'
import { PublicOnly, RequireAdmin, RequireAuth } from '@/app/RequireAuth'

export const router = createBrowserRouter([
  {
    element: <PublicOnly />,
    children: [{ path: '/login', element: <LoginPage /> }],
  },
  {
    element: <RequireAuth />,
    children: [
      {
        path: '/',
        element: <MainLayout />,
        children: [
          { index: true, element: <DashboardPage /> },
          { path: 'people', element: <PeopleListPage /> },
          { path: 'search', element: <SearchPage /> },
          { path: 'forbidden', element: <ForbiddenPage /> },
          {
            element: <RequireAdmin />,
            children: [
              { path: 'people/new', element: <PeopleNewPage /> },
              { path: 'analyses', element: <AnalysesPage /> },
              { path: 'admin/codes', element: <AdminCodesPage /> },
              { path: 'admin/users', element: <AdminUsersPage /> },
              { path: 'admin/analyses', element: <AdminAnalysesPage /> },
            ],
          },
          { path: '*', element: <NotFoundPage /> },
        ],
      },
    ],
  },
])
