import { CopyOutlined, RobotOutlined } from '@ant-design/icons'
import { App, Button, Tag } from 'antd'
import { MarkdownContent } from '../../../components/MarkdownContent'
import { brand } from '../../../app/theme'
import type { ChatItem } from '../types'
import { ToolActivity } from './ToolActivity'

export function MessageBubble({ item }: { item: ChatItem }) {
  return item.kind === 'user' ? <UserBubble content={item.content} /> : <AssistantBubble item={item} />
}

function UserBubble({ content }: { content: string }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
      <div
        style={{
          maxWidth: '82%',
          background: brand.signalBlueSoft,
          border: `1px solid ${brand.border}`,
          borderRadius: 10,
          padding: '9px 13px',
          fontSize: 14,
          lineHeight: 1.55,
          whiteSpace: 'pre-wrap',
          overflowWrap: 'anywhere',
        }}
      >
        {content}
      </div>
    </div>
  )
}

function AssistantBubble({ item }: { item: Extract<ChatItem, { kind: 'assistant' }> }) {
  const { message } = App.useApp()

  const copy = () => {
    void navigator.clipboard?.writeText(item.content).then(
      () => message.success('Copied'),
      () => message.error('Could not copy'),
    )
  }

  return (
    <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
      <div
        aria-hidden
        style={{
          flex: '0 0 auto',
          width: 30,
          height: 30,
          borderRadius: 8,
          display: 'grid',
          placeItems: 'center',
          background: brand.tealSoft,
          color: brand.teal,
          fontSize: 15,
        }}
      >
        <RobotOutlined />
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <ToolActivity activities={item.toolActivities} />
        {item.content && <MarkdownContent>{item.content}</MarkdownContent>}
        {item.isStreaming && <span className="rasyona-caret" aria-hidden />}
        {item.isPartial && (
          <div style={{ marginTop: 6 }}>
            <Tag color="warning">Response interrupted</Tag>
          </div>
        )}
        {item.content && !item.isStreaming && (
          <div style={{ marginTop: 6 }}>
            <Button
              type="text"
              size="small"
              icon={<CopyOutlined />}
              onClick={copy}
              aria-label="Copy message"
              style={{ color: 'rgba(230, 237, 246, 0.5)' }}
            >
              Copy
            </Button>
          </div>
        )}
      </div>
    </div>
  )
}
