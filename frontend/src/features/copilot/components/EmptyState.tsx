import { RobotOutlined } from '@ant-design/icons'
import { Card, Typography } from 'antd'
import { brand, fontMono } from '../../../app/theme'

const { Title, Paragraph } = Typography

// Concrete SAP-ops starters so a new operator sees what the Copilot is for.
const EXAMPLES = [
  'Which SAP systems do you monitor?',
  'Any high CPU on KHP in the last hour?',
  'Show recent error logs for KBP',
  'Is response time healthy across all systems?',
]

export function EmptyState({ onPick }: { onPick: (text: string) => void }) {
  return (
    <div style={{ maxWidth: 720, margin: '0 auto', padding: '48px 8px', textAlign: 'center' }}>
      <div
        style={{
          width: 48,
          height: 48,
          borderRadius: 12,
          margin: '0 auto 16px',
          display: 'grid',
          placeItems: 'center',
          background: brand.tealSoft,
          color: brand.teal,
          fontSize: 22,
        }}
      >
        <RobotOutlined />
      </div>
      <Title level={4} style={{ marginTop: 0 }}>
        Ask about your SAP systems
      </Title>
      <Paragraph type="secondary" style={{ marginBottom: 24 }}>
        The Copilot reads live metrics from Prometheus and logs from Elasticsearch across the
        systems it monitors. Ask a question to get started.
      </Paragraph>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))',
          gap: 12,
          textAlign: 'left',
        }}
      >
        {EXAMPLES.map((example) => (
          <Card
            key={example}
            size="small"
            hoverable
            onClick={() => onPick(example)}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault()
                onPick(example)
              }
            }}
            styles={{ body: { padding: '12px 14px' } }}
          >
            <span style={{ fontFamily: fontMono, fontSize: 13 }}>{example}</span>
          </Card>
        ))}
      </div>
    </div>
  )
}
