import { Layout, Menu, type MenuProps } from 'antd'
import { useState } from 'react'
import { Outlet, useLocation, useNavigate } from 'react-router'
import { BackendStatusBadge } from '../components/BackendStatusBadge'
import { FLOWS } from './flows'
import { brand, fontMono, fontSans } from './theme'

const { Header, Sider, Content } = Layout

function isActive(pathname: string, flowPath: string): boolean {
  return pathname === flowPath || pathname.startsWith(`${flowPath}/`)
}

/** The Rasyona wordmark: word in Plex Sans semibold + a teal terminal block-caret. */
function Wordmark() {
  return (
    <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
      <span
        aria-hidden
        style={{ color: brand.teal, fontFamily: fontMono, fontWeight: 700, fontSize: 18, lineHeight: 1 }}
      >
        &#9613;
      </span>
      <h1
        style={{
          margin: 0,
          fontFamily: fontSans,
          fontWeight: 600,
          fontSize: 18,
          letterSpacing: '0.2px',
          color: brand.textHeading,
        }}
      >
        Rasyona
      </h1>
      <span
        style={{
          fontFamily: fontMono,
          fontSize: 11,
          letterSpacing: '0.4px',
          color: 'rgba(230, 237, 246, 0.4)',
        }}
      >
        SAP Basis Ops
      </span>
    </div>
  )
}

export function AppLayout() {
  const location = useLocation()
  const navigate = useNavigate()
  const [collapsed, setCollapsed] = useState(false)

  // Enabled flows first; unbuilt flows grouped under a "Coming soon" header so the
  // roadmap stays visible without crowding each label with a tag.
  const toItem = (flow: (typeof FLOWS)[number]) => ({
    key: flow.path,
    icon: flow.icon,
    disabled: !flow.enabled,
    title: flow.enabled ? flow.description : `${flow.label} — coming soon`,
    label: flow.label,
  })
  const enabled = FLOWS.filter((f) => f.enabled).map(toItem)
  const upcoming = FLOWS.filter((f) => !f.enabled).map(toItem)
  const items: MenuProps['items'] = [
    ...enabled,
    ...(upcoming.length
      ? [{ key: 'grp-soon', type: 'group' as const, label: 'Coming soon', children: upcoming }]
      : []),
  ]

  const selected = FLOWS.find((flow) => isActive(location.pathname, flow.path))
  const selectedKeys = selected ? [selected.path] : []

  const onClick: MenuProps['onClick'] = ({ key }) => {
    navigate(key)
  }

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          borderBottom: `1px solid ${brand.border}`,
        }}
      >
        <Wordmark />
        <BackendStatusBadge />
      </Header>
      <Layout>
        <Sider
          breakpoint="lg"
          collapsedWidth={64}
          width={240}
          collapsible
          collapsed={collapsed}
          onCollapse={setCollapsed}
          style={{ borderRight: `1px solid ${brand.border}` }}
        >
          <Menu
            mode="inline"
            selectedKeys={selectedKeys}
            items={items}
            onClick={onClick}
            style={{ borderInlineEnd: 'none', paddingTop: 8 }}
          />
        </Sider>
        <Content style={{ display: 'flex', flexDirection: 'column', minHeight: 0 }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  )
}
