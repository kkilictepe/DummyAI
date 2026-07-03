import {
  AlertOutlined,
  HeartOutlined,
  MessageOutlined,
  PartitionOutlined,
} from '@ant-design/icons'
import type { ReactNode } from 'react'
import type { FlowPath } from '../lib/agui'

// Single source of truth for the left menu AND the routes. Adding a flow later is one
// entry here + one route in router.tsx + one feature dir. `path` mirrors the backend
// endpoint path (CLAUDE.md: route path mirrors the flow endpoint).
export interface FlowDef {
  id: string
  path: FlowPath
  label: string
  /** Short menu tooltip / one-liner. */
  description: string
  icon: ReactNode
  /** Disabled flows show in the menu as a visible roadmap but aren't routable yet. */
  enabled: boolean
}

export const FLOWS: FlowDef[] = [
  {
    id: 'copilot',
    path: '/copilot',
    label: 'Copilot',
    description: 'Ask about your SAP systems',
    icon: <MessageOutlined />,
    enabled: true,
  },
  {
    id: 'root-cause',
    path: '/root-cause',
    label: 'Root Cause Analysis',
    description: 'Coming soon',
    icon: <PartitionOutlined />,
    enabled: false,
  },
  {
    id: 'alerts',
    path: '/alerts',
    label: 'Alert Analysis',
    description: 'Coming soon',
    icon: <AlertOutlined />,
    enabled: false,
  },
  {
    id: 'healthcheck',
    path: '/healthcheck',
    label: 'Daily HealthCheck',
    description: 'Coming soon',
    icon: <HeartOutlined />,
    enabled: false,
  },
]

export const DEFAULT_FLOW_PATH: FlowPath = '/copilot'
