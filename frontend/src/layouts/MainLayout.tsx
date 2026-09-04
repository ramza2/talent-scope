import { Layout, Menu, Typography, theme } from 'antd'
import {
  DashboardOutlined,
  TeamOutlined,
  SearchOutlined,
  SettingOutlined,
  FileSearchOutlined,
  UserAddOutlined,
  ApartmentOutlined,
  UserOutlined,
  BarChartOutlined,
} from '@ant-design/icons'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import type { ReactNode } from 'react'

const { Header, Sider, Content } = Layout

type MenuItem = {
  key: string
  icon?: ReactNode
  label: string
  children?: MenuItem[]
}

const menuItems: MenuItem[] = [
  { key: '/', icon: <DashboardOutlined />, label: '대시보드' },
  {
    key: 'people-group',
    icon: <TeamOutlined />,
    label: '인력관리',
    children: [
      { key: '/people', icon: <TeamOutlined />, label: '인력 목록' },
      { key: '/people/new', icon: <UserAddOutlined />, label: '신규 인력 등록' },
      { key: '/analyses', icon: <FileSearchOutlined />, label: 'AI 분석 검토' },
    ],
  },
  {
    key: 'search-group',
    icon: <SearchOutlined />,
    label: '인력검색',
    children: [{ key: '/search', icon: <SearchOutlined />, label: '통합 인력검색' }],
  },
  {
    key: 'admin-group',
    icon: <SettingOutlined />,
    label: '관리',
    children: [
      { key: '/admin/codes', icon: <ApartmentOutlined />, label: '코드 관리' },
      { key: '/admin/users', icon: <UserOutlined />, label: '사용자 관리' },
      { key: '/admin/analyses', icon: <BarChartOutlined />, label: 'AI 분석 현황' },
    ],
  },
]

function selectedKeysFromPath(pathname: string): string[] {
  if (pathname.startsWith('/people/new')) return ['/people/new']
  if (pathname.startsWith('/people')) return ['/people']
  if (pathname.startsWith('/analyses')) return ['/analyses']
  if (pathname.startsWith('/search')) return ['/search']
  if (pathname.startsWith('/admin/codes')) return ['/admin/codes']
  if (pathname.startsWith('/admin/users')) return ['/admin/users']
  if (pathname.startsWith('/admin/analyses')) return ['/admin/analyses']
  return ['/']
}

export function MainLayout() {
  const navigate = useNavigate()
  const location = useLocation()
  const {
    token: { colorBgContainer, borderRadiusLG },
  } = theme.useToken()

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider breakpoint="lg" collapsedWidth={64} width={220}>
        <div
          style={{
            height: 56,
            margin: 12,
            display: 'flex',
            alignItems: 'center',
            color: '#fff',
            fontWeight: 700,
            letterSpacing: 0.2,
          }}
        >
          TalentScope
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={selectedKeysFromPath(location.pathname)}
          defaultOpenKeys={['people-group', 'search-group', 'admin-group']}
          items={menuItems}
          onClick={({ key }) => {
            if (key.startsWith('/')) navigate(key)
          }}
        />
      </Sider>
      <Layout>
        <Header
          style={{
            background: colorBgContainer,
            padding: '0 24px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            borderBottom: '1px solid #f0f0f0',
          }}
        >
          <Typography.Text type="secondary">
            AI 기반 인력 프로필 관리 · 검색 (Skeleton)
          </Typography.Text>
          <Typography.Text>개발 사용자</Typography.Text>
        </Header>
        <Content style={{ margin: 24 }}>
          <div
            style={{
              padding: 24,
              minHeight: 360,
              background: colorBgContainer,
              borderRadius: borderRadiusLG,
            }}
          >
            <Outlet />
          </div>
        </Content>
      </Layout>
    </Layout>
  )
}
