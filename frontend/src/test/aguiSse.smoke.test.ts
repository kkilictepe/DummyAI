import { http } from 'msw'
import { describe, expect, it } from 'vitest'
import { createFlowAgent } from '../lib/agui'
import type { AssistantMessage, Message } from '../lib/agui'
import { happyPathWithTool } from './aguiEvents'
import { server } from './server'
import { sseResponse } from './sse'

// The load-bearing test for the whole test approach: a REAL HttpAgent driven against a
// mocked SSE stream. It proves the env-URL -> MSW -> undici -> SDK-parser chain works
// before any UI exists. If this passes, feature/hook tests can rely on the same wiring.
describe('AG-UI SSE smoke', () => {
  it('drives an HttpAgent through the happy path via a mocked SSE stream', async () => {
    let captured: { messages?: unknown[] } | undefined
    server.use(
      http.post(/\/copilot$/, async ({ request }) => {
        captured = (await request.json()) as { messages?: unknown[] }
        return sseResponse(happyPathWithTool())
      }),
    )

    const agent = createFlowAgent('/copilot')
    agent.addMessage({ id: 'u1', role: 'user', content: 'How is KHP?' })
    const result = await agent.runAgent()

    // Stateless replay: the POST carried the full message history.
    expect(captured?.messages).toHaveLength(1)

    const isAssistant = (m: Message): m is AssistantMessage => m.role === 'assistant'
    const assistants = agent.messages.filter(isAssistant)

    // The streamed markdown answer landed on an assistant message.
    const answered = assistants.find((m) => m.content?.includes('**KHP** is healthy.'))
    expect(answered).toBeDefined()

    // The tool call was captured with its friendly backend name...
    const withToolCall = assistants.find((m) => (m.toolCalls?.length ?? 0) > 0)
    expect(withToolCall?.toolCalls?.[0]?.function.name).toBe('list_sap_systems')

    // ...and the tool result became a tool-role message.
    expect(agent.messages.some((m) => m.role === 'tool')).toBe(true)

    // runAgent resolves with the run result (new messages produced this turn).
    expect(result.newMessages.length).toBeGreaterThan(0)
  })
})
