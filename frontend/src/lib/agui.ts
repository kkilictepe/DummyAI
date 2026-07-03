// Single seam onto the AG-UI TypeScript SDK (@ag-ui/client, exact-pinned).
// Features import the HttpAgent factory and SDK types from HERE — never from
// '@ag-ui/client' directly — so a future SDK bump is a one-file change.

import { HttpAgent, randomUUID, EventType } from '@ag-ui/client'
import type {
  Message,
  AssistantMessage,
  UserMessage,
  ToolMessage,
  ToolCall,
  AgentSubscriber,
  RunAgentInput,
} from '@ag-ui/client'
import { env } from './env'

export { HttpAgent, randomUUID, EventType }
export type {
  Message,
  AssistantMessage,
  UserMessage,
  ToolMessage,
  ToolCall,
  AgentSubscriber,
  RunAgentInput,
}

/** A backend flow endpoint path, e.g. `/copilot` (mirrors the page route). */
export type FlowPath = `/${string}`

export interface CreateFlowAgentOptions {
  /** Groups Langfuse traces server-side; the backend is otherwise stateless. */
  threadId?: string
  initialMessages?: Message[]
  headers?: Record<string, string>
}

/**
 * Create an HttpAgent bound to a flow's SSE endpoint (`${aguiBaseUrl}${flowPath}`).
 * The returned instance *is* the conversation store — one agent per conversation.
 */
export function createFlowAgent(
  flowPath: FlowPath,
  opts: CreateFlowAgentOptions = {},
): HttpAgent {
  return new HttpAgent({
    url: `${env.aguiBaseUrl}${flowPath}`,
    threadId: opts.threadId,
    initialMessages: opts.initialMessages,
    headers: opts.headers,
  })
}
