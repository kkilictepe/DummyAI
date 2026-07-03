import { Badge, Tooltip } from 'antd'
import type { BadgeProps } from 'antd'
import { useEffect, useState } from 'react'
import { getHealth } from '../lib/api'

type Status = 'loading' | 'ok' | 'degraded' | 'down'

const VIEW: Record<Status, { badge: BadgeProps['status']; text: string }> = {
  loading: { badge: 'default', text: 'Checking backend…' },
  ok: { badge: 'success', text: 'Backend online' },
  degraded: { badge: 'warning', text: 'Backend degraded' },
  down: { badge: 'error', text: 'Backend unreachable' },
}

// Polls GET /health every 30s so operators see transport health at a glance.
export function BackendStatusBadge() {
  const [status, setStatus] = useState<Status>('loading')

  useEffect(() => {
    let active = true
    const poll = async () => {
      try {
        const health = await getHealth()
        if (active) setStatus(health.status === 'ok' ? 'ok' : 'degraded')
      } catch {
        if (active) setStatus('down')
      }
    }
    void poll()
    const id = setInterval(() => void poll(), 30_000)
    return () => {
      active = false
      clearInterval(id)
    }
  }, [])

  const view = VIEW[status]
  return (
    <Tooltip title={view.text}>
      <Badge
        status={view.badge}
        text={
          <span style={{ fontSize: 12, color: 'rgba(230, 237, 246, 0.65)' }}>{view.text}</span>
        }
      />
    </Tooltip>
  )
}
