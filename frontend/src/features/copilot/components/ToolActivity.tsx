import { CheckCircleFilled, LoadingOutlined } from '@ant-design/icons'
import { Collapse } from 'antd'
import { brand, fontMono } from '../../../app/theme'
import type { ToolActivityVM } from '../types'

// The signature element: the agent's work is auditable. Each tool call is a collapsible
// step — spinner + friendly label while it runs, a teal check when it returns — and its
// body holds the exact args and result JSON an operator can inspect.
function prettyJson(raw: string | undefined): string {
  if (!raw) return '—'
  try {
    return JSON.stringify(JSON.parse(raw), null, 2)
  } catch {
    return raw
  }
}

function JsonBlock({ title, value }: { title: string; value: string }) {
  return (
    <div>
      <div
        style={{
          fontSize: 11,
          textTransform: 'uppercase',
          letterSpacing: '0.5px',
          opacity: 0.5,
          marginBottom: 4,
        }}
      >
        {title}
      </div>
      <pre
        style={{
          margin: 0,
          maxHeight: 280,
          overflow: 'auto',
          background: brand.bgElevated,
          border: `1px solid ${brand.border}`,
          borderRadius: 6,
          padding: '8px 10px',
          fontFamily: fontMono,
          fontSize: 12,
        }}
      >
        {value}
      </pre>
    </div>
  )
}

export function ToolActivity({ activities }: { activities: ToolActivityVM[] }) {
  if (activities.length === 0) return null
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginBottom: 10 }}>
      {activities.map((activity) => {
        const running = activity.phase !== 'done'
        return (
          <Collapse
            key={activity.toolCallId}
            size="small"
            ghost
            items={[
              {
                key: activity.toolCallId,
                label: (
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8, fontSize: 13 }}>
                    {running ? (
                      <LoadingOutlined spin style={{ color: brand.teal }} />
                    ) : (
                      <CheckCircleFilled style={{ color: brand.teal }} />
                    )}
                    <span>{activity.label}</span>
                    <code style={{ fontFamily: fontMono, fontSize: 11, opacity: 0.55 }}>
                      {activity.toolName}
                    </code>
                  </span>
                ),
                children: (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                    <JsonBlock title="Arguments" value={prettyJson(activity.args)} />
                    {activity.result !== undefined && (
                      <JsonBlock title="Result" value={prettyJson(activity.result)} />
                    )}
                  </div>
                ),
              },
            ]}
          />
        )
      })}
    </div>
  )
}
