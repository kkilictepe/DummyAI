import { PlusOutlined } from '@ant-design/icons'
import { Alert, Button, Popconfirm } from 'antd'
import { brand, fontMono, fontSans } from '../../app/theme'
import { Composer } from './components/Composer'
import { EmptyState } from './components/EmptyState'
import { MessageList } from './components/MessageList'
import { useCopilotAgent } from './hooks/useCopilotAgent'
import type { ChatItem } from './types'

function activeTypingLabel(items: ChatItem[]): string {
  const last = items[items.length - 1]
  if (last?.kind === 'assistant') {
    const running = last.toolActivities.find((a) => a.phase !== 'done')
    if (running) return running.label
  }
  return 'Copilot is thinking…'
}

export function CopilotPage() {
  const copilot = useCopilotAgent()
  const typingLabel = activeTypingLabel(copilot.chatItems)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }} data-testid="copilot-page">
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '10px 16px',
          borderBottom: `1px solid ${brand.border}`,
          flex: '0 0 auto',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
          <span style={{ fontFamily: fontSans, fontWeight: 600, fontSize: 15 }}>Copilot</span>
          {copilot.hasHistory && (
            <span style={{ fontFamily: fontMono, fontSize: 11, opacity: 0.45 }}>
              thread {copilot.threadId.slice(0, 8)}
            </span>
          )}
        </div>
        {copilot.hasHistory && (
          <Popconfirm
            title="Start a new conversation?"
            description="This clears the current chat."
            okText="Clear chat"
            cancelText="Cancel"
            onConfirm={copilot.newConversation}
          >
            <Button size="small" icon={<PlusOutlined />}>
              New conversation
            </Button>
          </Popconfirm>
        )}
      </div>

      {copilot.hasHistory ? (
        <MessageList
          items={copilot.chatItems}
          isRunning={copilot.isRunning}
          typingLabel={typingLabel}
        />
      ) : (
        <div style={{ flex: 1, overflowY: 'auto' }}>
          <EmptyState onPick={copilot.sendMessage} />
        </div>
      )}

      {copilot.error && (
        <div style={{ maxWidth: 820, margin: '0 auto', width: '100%', padding: '0 16px' }}>
          <Alert
            key={copilot.error}
            type="error"
            role="alert"
            showIcon
            closable
            message={copilot.error}
          />
        </div>
      )}

      <Composer onSend={copilot.sendMessage} onStop={copilot.stop} isRunning={copilot.isRunning} />
    </div>
  )
}
