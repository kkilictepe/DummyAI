import { DownOutlined } from '@ant-design/icons'
import { Button } from 'antd'
import { useStickToBottom } from '../hooks/useStickToBottom'
import type { ChatItem } from '../types'
import { MessageBubble } from './MessageBubble'
import { TypingIndicator } from './TypingIndicator'

interface MessageListProps {
  items: ChatItem[]
  isRunning: boolean
  typingLabel: string
}

export function MessageList({ items, isRunning, typingLabel }: MessageListProps) {
  const { containerRef, isPinned, scrollToBottom, onScroll } = useStickToBottom(items)

  return (
    <div style={{ position: 'relative', flex: 1, minHeight: 0 }}>
      <div
        ref={containerRef}
        onScroll={onScroll}
        role="log"
        aria-label="Conversation"
        style={{ height: '100%', overflowY: 'auto', padding: '16px 0' }}
      >
        <div
          style={{
            maxWidth: 820,
            margin: '0 auto',
            padding: '0 16px',
            display: 'flex',
            flexDirection: 'column',
            gap: 16,
          }}
        >
          {items.map((item) => (
            <MessageBubble key={item.id} item={item} />
          ))}
          {isRunning && <TypingIndicator label={typingLabel} />}
        </div>
      </div>
      {!isPinned && (
        <Button
          size="small"
          shape="round"
          icon={<DownOutlined />}
          onClick={scrollToBottom}
          style={{ position: 'absolute', bottom: 12, left: '50%', transform: 'translateX(-50%)' }}
        >
          Jump to latest
        </Button>
      )}
    </div>
  )
}
