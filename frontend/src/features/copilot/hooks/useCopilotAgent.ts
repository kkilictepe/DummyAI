import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createFlowAgent, randomUUID } from '../../../lib/agui'
import type { AgentSubscriber, HttpAgent, Message } from '../../../lib/agui'
import { buildChatItems } from '../chatItems'
import type { ChatItem, ToolRunStatus } from '../types'

const COPILOT_PATH = '/copilot' as const

// User-facing error copy — say what happened and what to do next; never leak internals.
export const ERROR_SERVICE_UNCONFIGURED =
  'The Copilot service is not configured on the server. Contact your platform team.'
export const ERROR_TRANSPORT =
  'Could not reach the Copilot service. Check your connection and try again.'
export const ERROR_RUN_FAILED = 'The Copilot hit an error while answering. Please try again.'

export interface UseCopilotAgent {
  chatItems: ChatItem[]
  isRunning: boolean
  error: string | null
  threadId: string
  hasHistory: boolean
  sendMessage: (text: string) => void
  stop: () => void
  newConversation: () => void
}

export function useCopilotAgent(): UseCopilotAgent {
  // Agent lives in state (not a ref) so newConversation swaps the instance and the
  // subscription effect re-runs against the fresh conversation store.
  const [agent, setAgent] = useState<HttpAgent>(() => createFlowAgent(COPILOT_PATH))
  const [messages, setMessages] = useState<Message[]>([])
  const [isRunning, setIsRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [toolStatuses, setToolStatuses] = useState<Record<string, ToolRunStatus>>({})
  const [streamingMessageId, setStreamingMessageId] = useState<string | null>(null)
  const [partialMessageIds, setPartialMessageIds] = useState<ReadonlySet<string>>(new Set())

  // Which assistant text message is currently open — read by onRunErrorEvent (which
  // has no messageId) to flag the interrupted message as partial.
  const streamingIdRef = useRef<string | null>(null)

  // The agent whose emissions we accept. The SDK captures its subscriber list per-run,
  // so unsubscribe() does NOT stop a discarded agent's in-flight run from firing our
  // callbacks. newConversation bumps this synchronously so residual frames (and a late
  // rejection) from the superseded agent are ignored instead of corrupting the fresh
  // conversation.
  const activeAgentRef = useRef(agent)

  useEffect(() => {
    activeAgentRef.current = agent
    const isCurrent = () => activeAgentRef.current === agent
    const snapshot = () => setMessages([...agent.messages] as Message[])

    const subscriber: AgentSubscriber = {
      onMessagesChanged: ({ messages: next }) => {
        if (isCurrent()) setMessages([...next] as Message[])
      },

      onTextMessageStartEvent: ({ event }) => {
        if (!isCurrent()) return
        streamingIdRef.current = event.messageId
        setStreamingMessageId(event.messageId)
      },
      onTextMessageEndEvent: ({ event }) => {
        if (isCurrent() && streamingIdRef.current === event.messageId) {
          streamingIdRef.current = null
          setStreamingMessageId(null)
        }
      },

      onToolCallStartEvent: ({ event }) => {
        if (!isCurrent()) return
        setToolStatuses((s) => ({
          ...s,
          [event.toolCallId]: {
            toolCallId: event.toolCallId,
            toolName: event.toolCallName,
            phase: 'streaming-args',
          },
        }))
      },
      onToolCallEndEvent: ({ event }) => {
        if (!isCurrent()) return
        setToolStatuses((s) =>
          s[event.toolCallId]
            ? { ...s, [event.toolCallId]: { ...s[event.toolCallId]!, phase: 'executing' } }
            : s,
        )
      },
      onToolCallResultEvent: ({ event }) => {
        if (!isCurrent()) return
        setToolStatuses((s) =>
          s[event.toolCallId]
            ? { ...s, [event.toolCallId]: { ...s[event.toolCallId]!, phase: 'done' } }
            : s,
        )
      },

      onRunErrorEvent: ({ event }) => {
        if (!isCurrent()) return
        // Either a real failure or a user Stop (RUN_ERROR{code:'abort'}) leaves the open
        // message half-written — flag it partial in both cases. Only a real failure gets
        // the error banner; a deliberate Stop is not an error.
        const openId = streamingIdRef.current
        if (openId) setPartialMessageIds((p) => new Set(p).add(openId))
        if (event.code === 'abort') return
        setError(ERROR_RUN_FAILED)
      },

      // onRunFinalized always fires (success, RUN_ERROR, or abort) — the one place we
      // flip isRunning off and clear the streaming marker.
      onRunFinalized: () => {
        if (!isCurrent()) return
        setIsRunning(false)
        setStreamingMessageId(null)
        streamingIdRef.current = null
      },
    }

    const { unsubscribe } = agent.subscribe(subscriber)
    snapshot() // pick up initialMessages / a carried-over history
    return () => {
      unsubscribe()
      agent.abortRun() // no-op if idle; fresh AbortController per run makes this StrictMode-safe
    }
  }, [agent])

  const sendMessage = useCallback(
    (text: string) => {
      const content = text.trim()
      if (!content || isRunning) return
      setError(null)
      setIsRunning(true)
      agent.addMessage({ id: randomUUID(), role: 'user', content })
      setMessages([...agent.messages] as Message[]) // defensive: addMessage may not emit onMessagesChanged
      agent.runAgent().catch((err: unknown) => {
        // Ignore a rejection from a run we've since discarded (newConversation) — it must
        // not paint an error on the fresh conversation.
        if (activeAgentRef.current !== agent) return
        // Aborts (incl. pre-headers, which reject with AbortError) are never user errors.
        if (err instanceof Error && err.name === 'AbortError') return
        const status = (err as { status?: number }).status
        setError(status === 503 ? ERROR_SERVICE_UNCONFIGURED : ERROR_TRANSPORT)
        setIsRunning(false) // belt-and-braces in case finalize didn't fire on a hard reject
      })
    },
    [agent, isRunning],
  )

  const stop = useCallback(() => {
    agent.abortRun()
  }, [agent])

  const newConversation = useCallback(() => {
    agent.abortRun()
    const next = createFlowAgent(COPILOT_PATH)
    // Mark the new agent active SYNCHRONOUSLY (before the subscribe effect runs) so any
    // frames the old agent's run has already buffered are ignored from this point on.
    activeAgentRef.current = next
    setAgent(next)
    setMessages([])
    setError(null)
    setToolStatuses({})
    setStreamingMessageId(null)
    setPartialMessageIds(new Set())
    setIsRunning(false)
    streamingIdRef.current = null
  }, [agent])

  const chatItems = useMemo(
    () => buildChatItems(messages, toolStatuses, streamingMessageId, partialMessageIds),
    [messages, toolStatuses, streamingMessageId, partialMessageIds],
  )

  return {
    chatItems,
    isRunning,
    error,
    threadId: agent.threadId,
    hasHistory: messages.length > 0,
    sendMessage,
    stop,
    newConversation,
  }
}
