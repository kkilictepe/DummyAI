// AG-UI event fixture builders — camelCase, mirroring the backend wire contract
// (backend/tests/agui/test_copilot_endpoint_sse.py). Sequences are ordered to satisfy
// the SDK's event-ordering verification; a malformed order intentionally fails a run.

export type WireEvent = Record<string, unknown>

export const GENERIC_ERROR =
  'The copilot hit an internal error while processing this request.'

export const runStarted = (opts: { threadId?: string; runId?: string } = {}): WireEvent => ({
  type: 'RUN_STARTED',
  threadId: opts.threadId ?? 't-test',
  runId: opts.runId ?? 'r-test',
})

export const runFinished = (opts: { threadId?: string; runId?: string } = {}): WireEvent => ({
  type: 'RUN_FINISHED',
  threadId: opts.threadId ?? 't-test',
  runId: opts.runId ?? 'r-test',
})

export const runError = (message: string = GENERIC_ERROR, code?: string): WireEvent => ({
  type: 'RUN_ERROR',
  message,
  ...(code ? { code } : {}),
})

export const textStart = (messageId: string, role = 'assistant'): WireEvent => ({
  type: 'TEXT_MESSAGE_START',
  messageId,
  role,
})

export const textContent = (messageId: string, delta: string): WireEvent => ({
  type: 'TEXT_MESSAGE_CONTENT',
  messageId,
  delta,
})

export const textEnd = (messageId: string): WireEvent => ({
  type: 'TEXT_MESSAGE_END',
  messageId,
})

export const toolStart = (
  toolCallId: string,
  toolCallName: string,
  parentMessageId?: string,
): WireEvent => ({
  type: 'TOOL_CALL_START',
  toolCallId,
  toolCallName,
  ...(parentMessageId ? { parentMessageId } : {}),
})

export const toolArgs = (toolCallId: string, delta: string): WireEvent => ({
  type: 'TOOL_CALL_ARGS',
  toolCallId,
  delta,
})

export const toolEnd = (toolCallId: string): WireEvent => ({
  type: 'TOOL_CALL_END',
  toolCallId,
})

export const toolResult = (
  messageId: string,
  toolCallId: string,
  content: string,
): WireEvent => ({
  type: 'TOOL_CALL_RESULT',
  messageId,
  toolCallId,
  content,
  role: 'tool',
})

export const stateSnapshot = (snapshot: unknown): WireEvent => ({
  type: 'STATE_SNAPSHOT',
  snapshot,
})

// ---- Composite sequences ----

/**
 * Happy path: one tool call (with a streamed args delta + result) followed by a
 * streamed markdown answer, then a state snapshot and the terminal RUN_FINISHED.
 */
export function happyPathWithTool(
  opts: {
    toolCallId?: string
    toolName?: string
    toolArgsJson?: string
    toolResultJson?: string
    answer?: string
    assistantToolMsgId?: string
    assistantTextMsgId?: string
    toolResultMsgId?: string
  } = {},
): WireEvent[] {
  const tc = opts.toolCallId ?? 'call-1'
  const amTool = opts.assistantToolMsgId ?? 'a-tool-1'
  const amText = opts.assistantTextMsgId ?? 'a-text-1'
  const trMsg = opts.toolResultMsgId ?? 'm-toolresult-1'
  const answer = opts.answer ?? '**KHP** is healthy.'
  return [
    runStarted(),
    toolStart(tc, opts.toolName ?? 'list_sap_systems', amTool),
    toolArgs(tc, opts.toolArgsJson ?? '{}'),
    toolEnd(tc),
    toolResult(trMsg, tc, opts.toolResultJson ?? '["KHP","KBP"]'),
    textStart(amText),
    textContent(amText, answer),
    textEnd(amText),
    stateSnapshot({ messages: [] }),
    runFinished(),
  ]
}

/** Plain streamed answer with no tools (also the shape of a guardrail refusal). */
export function textOnly(answer: string, messageId = 'a-text-1'): WireEvent[] {
  return [
    runStarted(),
    textStart(messageId),
    textContent(messageId, answer),
    textEnd(messageId),
    stateSnapshot({ messages: [] }),
    runFinished(),
  ]
}

/** A guardrail refusal is a NORMAL run: fixed text, ends RUN_FINISHED, no tools. */
export function refusal(
  text = "I can only help with SAP Basis operations for the systems I monitor.",
): WireEvent[] {
  return textOnly(text, 'a-refusal-1')
}

/** Failure mid-message: partial text then a terminal RUN_ERROR (block left unclosed). */
export function runErrorMidMessage(
  opts: { partial?: string; message?: string; messageId?: string } = {},
): WireEvent[] {
  const id = opts.messageId ?? 'a-text-1'
  return [
    runStarted(),
    textStart(id),
    textContent(id, opts.partial ?? 'Checking KHP'),
    runError(opts.message ?? GENERIC_ERROR),
  ]
}
