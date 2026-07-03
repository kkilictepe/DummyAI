import { App as AntdApp, ConfigProvider } from 'antd'
import type { ReactNode } from 'react'
import { themeConfigs } from './theme'

// App-wide providers: AntD theme (dark-first) + the antd <App> context that backs
// message/notification/Modal so those work without the static-method React 19 caveats.
// Light theme is wired but not yet selectable (no toggle in v1).
export function AppProviders({
  children,
  mode = 'dark',
}: {
  children: ReactNode
  mode?: 'dark' | 'light'
}) {
  return (
    <ConfigProvider theme={themeConfigs[mode]}>
      <AntdApp>{children}</AntdApp>
    </ConfigProvider>
  )
}
