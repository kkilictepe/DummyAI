import type { AssistantMessage, Message } from '../../lib/agui'
import { toolLabel, type ChatItem, type ToolActivityVM, type ToolRunStatus } from './types'

// Fold the SDK's flat message list into chat turns. The backend emits a tool-calling
// assistant message, then tool result message(s), then a separate answering assistant
// message; all assistant/tool messages between two user messages form ONE assistant
// turn (tool timeline + answer). Tool messages never render standalone — they are
// paired to their tool call by toolCallId.

interface Accumulator {
  firstId: string
  contents: string[]
  tools: ToolActivityVM[]
  isStreaming: boolean
  isPartial: boolean
}

// Message content may be a plain string or (for user messages) a multimodal parts
// array. The chat renders text; pull the text out and drop non-text parts.
function textOf(content: unknown): string {
  if (typeof content === 'string') return content
  if (Array.isArray(content)) {
    return content
      .map((part) =>
        part && typeof part === 'object' && typeof (part as { text?: unknown }).text === 'string'
          ? (part as { text: string }).text
          : '',
      )
      .join('')
  }
  return ''
}

export function buildChatItems(
  messages: readonly Message[],
  toolStatuses: Record<string, ToolRunStatus>,
  streamingMessageId: string | null,
  partialMessageIds: ReadonlySet<string>,
): ChatItem[] {
  // Index tool results by the call they answer.
  const toolResults = new Map<string, string>()
  for (const m of messages) {
    if (m.role === 'tool') toolResults.set(m.toolCallId, m.content)
  }

  const items: ChatItem[] = []
  let current: Accumulator | null = null

  const flush = () => {
    if (!current) return
    const content = current.contents.filter(Boolean).join('\n\n')
    if (content || current.tools.length || current.isStreaming) {
      items.push({
        kind: 'assistant',
        id: current.firstId,
        content,
        toolActivities: current.tools,
        isStreaming: current.isStreaming,
        isPartial: current.isPartial,
      })
    }
    current = null
  }

  const addAssistant = (m: AssistantMessage) => {
    if (!current) {
      current = { firstId: m.id, contents: [], tools: [], isStreaming: false, isPartial: false }
    }
    const text = textOf(m.content)
    if (text) current.contents.push(text)
    for (const tc of m.toolCalls ?? []) {
      const status = toolStatuses[tc.id]
      const result = toolResults.get(tc.id)
      current.tools.push({
        toolCallId: tc.id,
        toolName: tc.function.name,
        label: toolLabel(tc.function.name),
        phase: status?.phase ?? (result !== undefined ? 'done' : 'executing'),
        args: tc.function.arguments,
        result,
      })
    }
    if (streamingMessageId && m.id === streamingMessageId) current.isStreaming = true
    if (partialMessageIds.has(m.id)) current.isPartial = true
  }

  for (const m of messages) {
    if (m.role === 'user') {
      flush()
      items.push({ kind: 'user', id: m.id, content: textOf(m.content) })
    } else if (m.role === 'assistant') {
      addAssistant(m)
    }
    // 'tool' consumed via toolResults; other roles (system/reasoning/activity) ignored.
  }
  flush()
  return items
}
