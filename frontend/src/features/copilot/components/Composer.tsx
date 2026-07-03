import { ArrowUpOutlined, StopOutlined } from '@ant-design/icons'
import { Button, type GetRef, Input } from 'antd'
import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { brand } from '../../../app/theme'

type TextAreaRef = GetRef<typeof Input.TextArea>

interface ComposerProps {
  onSend: (text: string) => void
  onStop: () => void
  isRunning: boolean
}

// Enter sends, Shift+Enter newlines, Esc stops. The textarea stays editable while a run
// is in flight — only submission is gated — so operators can draft the next question.
export function Composer({ onSend, onStop, isRunning }: ComposerProps) {
  const [value, setValue] = useState('')
  const ref = useRef<TextAreaRef>(null)

  useEffect(() => {
    ref.current?.focus()
  }, [])

  const submit = () => {
    const text = value.trim()
    if (!text || isRunning) return
    onSend(text)
    setValue('')
    ref.current?.focus()
  }

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    // Don't treat the Enter that COMMITS an IME composition (CJK/JP/KR) as "send" — that
    // would ship a half-composed message. isComposing covers modern browsers; keyCode 229
    // is the legacy fallback.
    const composing = e.nativeEvent.isComposing || e.keyCode === 229
    if (e.key === 'Enter' && !e.shiftKey && !composing) {
      e.preventDefault()
      submit()
    } else if (e.key === 'Escape' && isRunning) {
      e.preventDefault()
      onStop()
    }
  }

  return (
    <div
      style={{
        display: 'flex',
        gap: 8,
        alignItems: 'flex-end',
        maxWidth: 820,
        margin: '0 auto',
        width: '100%',
        padding: '12px 16px 16px',
      }}
    >
      <Input.TextArea
        ref={ref}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={onKeyDown}
        autoSize={{ minRows: 1, maxRows: 8 }}
        placeholder="Ask about your SAP systems…"
        aria-label="Message the Copilot"
        style={{ background: brand.bgElevated, resize: 'none' }}
      />
      {isRunning ? (
        <Button danger icon={<StopOutlined />} onClick={onStop}>
          Stop
        </Button>
      ) : (
        <Button
          type="primary"
          icon={<ArrowUpOutlined />}
          onClick={submit}
          disabled={!value.trim()}
        >
          Send
        </Button>
      )}
    </div>
  )
}
