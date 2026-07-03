import { theme, type ThemeConfig } from 'antd'

// --- Rasyona visual identity ---------------------------------------------------
// An ops console for SAP Basis operators on long monitoring shifts: precision,
// auditability, calm density. A cool blue-slate dark surface (not near-black) with
// one action color (Signal Blue) and one reserved accent (teal) that ONLY ever marks
// AI presence — the agent's avatar, the streaming caret, a running tool. Semantic
// success/warning/error stay AntD's dark defaults.

export const brand = {
  /** App base — behind panels. */
  bgLayout: '#0e1420',
  /** Panels, header, sider, cards. */
  bgContainer: '#161d2b',
  /** Popovers, composer, elevated surfaces. */
  bgElevated: '#1d2636',
  /** Hairline borders / dividers. */
  border: '#26304291',
  /** Actions, active menu item, links. */
  signalBlue: '#3d9fe0',
  signalBlueHover: '#5cb1e8',
  signalBlueSoft: 'rgba(61, 159, 224, 0.14)',
  /** AI presence ONLY (copilot avatar, streaming caret, running-tool pulse). */
  teal: '#2fbfa7',
  tealSoft: 'rgba(47, 191, 167, 0.16)',
  textHeading: '#e6edf6',
} as const

export const fontSans =
  "'IBM Plex Sans', system-ui, -apple-system, Segoe UI, Roboto, sans-serif"
export const fontMono =
  "'IBM Plex Mono', ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"

const sharedToken: ThemeConfig['token'] = {
  colorPrimary: brand.signalBlue,
  colorLink: brand.signalBlue,
  colorLinkHover: brand.signalBlueHover,
  borderRadius: 6,
  fontFamily: fontSans,
  fontFamilyCode: fontMono,
  fontSize: 14,
  wireframe: false,
}

export const themeConfigs: Record<'dark' | 'light', ThemeConfig> = {
  dark: {
    algorithm: theme.darkAlgorithm,
    token: {
      ...sharedToken,
      colorBgLayout: brand.bgLayout,
      colorBgContainer: brand.bgContainer,
      colorBgElevated: brand.bgElevated,
      colorBorderSecondary: brand.border,
    },
    components: {
      Layout: {
        headerBg: brand.bgContainer,
        siderBg: brand.bgContainer,
        bodyBg: brand.bgLayout,
        headerPadding: '0 20px',
        headerHeight: 56,
      },
      // Regular Menu tokens (NOT the dark* ones — those only apply to <Menu theme="dark">,
      // which we don't use; the darkAlgorithm already supplies the dark palette).
      Menu: {
        itemBg: 'transparent',
        subMenuItemBg: 'transparent',
        itemSelectedBg: brand.signalBlueSoft,
        itemSelectedColor: brand.signalBlue,
        itemHoverBg: 'rgba(255, 255, 255, 0.04)',
        itemBorderRadius: 6,
        itemMarginInline: 8,
      },
      Card: { colorBgContainer: brand.bgContainer },
    },
  },
  light: {
    algorithm: theme.defaultAlgorithm,
    token: { ...sharedToken },
  },
}
