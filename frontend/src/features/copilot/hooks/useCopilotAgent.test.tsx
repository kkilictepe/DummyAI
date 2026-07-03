import { act, renderHook, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import {
  runFinished,
  runStarted,
  textContent,
  textEnd,
  textStart,
  toolArgs,
  toolEnd,
  toolResult,
  toolStart,
} from '../../../test/aguiEvents'
import { happyPathWithTool, refusal, runErrorMidMessage } from '../../../test/aguiEvents'
import { server } from '../../../test/server'
import { controlledSse, sseResponse } from '../../../test/sse'
import type { ChatItem } from '../types'
import { ERROR_SERVICE_UNCONFIGURED, useCopilotAgent } from './useCopilotAgent'

function assistantOf(items: ChatItem[]): Extract<ChatItem, { kind: 'assistant' }> | undefined {
  return items.find((i): i is Extract<ChatItem, { kind: 'assistant' }> => i.kind === 'assistant')
}

describe('useCopilotAgent', () => {
  it('runs the happy path: user turn, tool call, streamed markdown answer', async () => {
    let body: { messages?: unknown[] } | undefined
    server.use(
      http.post(/\/copilot$/, async ({ request }) => {
        body = (await request.json()) as { messages?: unknown[] }
        return sseResponse(happyPathWithTool())
      }),
    )
    const { result } = renderHook(() => useCopilotAgent())
    act(() => result.current.sendMessage('How is KHP?'))

    await waitFor(() => expect(result.current.isRunning).toBe(false))
    expect(result.current.chatItems[0]).toMatchObject({ kind: 'user', content: 'How is KHP?' })
    const assistant = assistantOf(result.current.chatItems)
    expect(assistant?.content).toContain('**KHP** is healthy.')
    expect(assistant?.toolActivities[0]?.label).toBe('Listing SAP systems')
    expect(assistant?.toolActivities[0]?.phase).toBe('done')
    expect(result.current.error).toBeNull()
    expect(body?.messages).toHaveLength(1) // stateless replay
  })

  it('advances tool phases as the stream progresses', async () => {
    const sse = controlledSse()
    server.use(http.post(/\/copilot$/, () => sse.response))
    const { result } = renderHook(() => useCopilotAgent())
    act(() => result.current.sendMessage('list systems'))
    await waitFor(() => expect(result.current.isRunning).toBe(true))

    act(() => {
      sse.push(runStarted())
      sse.push(toolStart('c1', 'list_sap_systems', 'a1'))
      sse.push(toolArgs('c1', '{}'))
    })
    await waitFor(() =>
      expect(assistantOf(result.current.chatItems)?.toolActivities[0]?.phase).toBe('streaming-args'),
    )

    act(() => sse.push(toolEnd('c1')))
    await waitFor(() =>
      expect(assistantOf(result.current.chatItems)?.toolActivities[0]?.phase).toBe('executing'),
    )

    act(() => sse.push(toolResult('t1', 'c1', '["KHP"]')))
    await waitFor(() =>
      expect(assistantOf(result.current.chatItems)?.toolActivities[0]?.phase).toBe('done'),
    )

    act(() => {
      sse.push(textStart('a2'))
      sse.push(textContent('a2', 'All good.'))
      sse.push(textEnd('a2'))
      sse.push(runFinished())
      sse.close()
    })
    await waitFor(() => expect(result.current.isRunning).toBe(false))
    expect(assistantOf(result.current.chatItems)?.content).toBe('All good.')
  })

  it('stops mid-stream: marks the message partial, shows no error', async () => {
    const sse = controlledSse()
    server.use(http.post(/\/copilot$/, () => sse.response))
    const { result } = renderHook(() => useCopilotAgent())
    act(() => result.current.sendMessage('go'))
    await waitFor(() => expect(result.current.isRunning).toBe(true))

    act(() => {
      sse.push(runStarted())
      sse.push(textStart('a1'))
      sse.push(textContent('a1', 'Checking'))
    })
    await waitFor(() => expect(assistantOf(result.current.chatItems)?.content).toBe('Checking'))

    // stop() aborts the fetch. In jsdom the abort doesn't propagate through MSW's
    // passthrough stream (it does in a real browser), so simulate the transport tearing
    // down with an AbortError — this drives the SDK's RUN_ERROR{code:'abort'} path.
    act(() => {
      result.current.stop()
      sse.error(new DOMException('The user aborted a request.', 'AbortError'))
    })
    await waitFor(() => expect(result.current.isRunning).toBe(false))
    expect(result.current.error).toBeNull()
    expect(assistantOf(result.current.chatItems)?.isPartial).toBe(true)
  })

  it('surfaces a run error and flags the interrupted message', async () => {
    server.use(http.post(/\/copilot$/, () => sseResponse(runErrorMidMessage())))
    const { result } = renderHook(() => useCopilotAgent())
    act(() => result.current.sendMessage('boom'))
    await waitFor(() => expect(result.current.isRunning).toBe(false))
    expect(result.current.error).toMatch(/error while answering/i)
    expect(assistantOf(result.current.chatItems)?.isPartial).toBe(true)
  })

  it('treats a guardrail refusal as a normal run (no error)', async () => {
    server.use(http.post(/\/copilot$/, () => sseResponse(refusal('I can only help with SAP Basis ops.'))))
    const { result } = renderHook(() => useCopilotAgent())
    act(() => result.current.sendMessage('write a poem'))
    await waitFor(() => expect(result.current.isRunning).toBe(false))
    expect(result.current.error).toBeNull()
    expect(assistantOf(result.current.chatItems)?.content).toContain('SAP Basis ops')
  })

  it('maps a 503 to the service-unconfigured message', async () => {
    server.use(http.post(/\/copilot$/, () => new HttpResponse('no key', { status: 503 })))
    const { result } = renderHook(() => useCopilotAgent())
    act(() => result.current.sendMessage('hi'))
    await waitFor(() => expect(result.current.error).toBe(ERROR_SERVICE_UNCONFIGURED))
    expect(result.current.isRunning).toBe(false)
  })

  it('ignores a second send while a run is in flight', async () => {
    const sse = controlledSse()
    let posts = 0
    server.use(
      http.post(/\/copilot$/, () => {
        posts += 1
        return sse.response
      }),
    )
    const { result } = renderHook(() => useCopilotAgent())
    act(() => result.current.sendMessage('first'))
    await waitFor(() => expect(result.current.isRunning).toBe(true))
    act(() => result.current.sendMessage('second (ignored)'))
    // Only the first user message exists; the second was gated out.
    expect(result.current.chatItems.filter((i) => i.kind === 'user')).toHaveLength(1)
    act(() => {
      sse.push(runStarted())
      sse.push(runFinished())
      sse.close()
    })
    await waitFor(() => expect(result.current.isRunning).toBe(false))
    expect(posts).toBe(1)
  })

  it('newConversation clears history and the next turn replays only the new message', async () => {
    const bodies: Array<{ messages?: unknown[] }> = []
    server.use(
      http.post(/\/copilot$/, async ({ request }) => {
        bodies.push((await request.json()) as { messages?: unknown[] })
        return sseResponse(happyPathWithTool())
      }),
    )
    const { result } = renderHook(() => useCopilotAgent())

    act(() => result.current.sendMessage('first question'))
    await waitFor(() => expect(result.current.isRunning).toBe(false))
    expect(result.current.hasHistory).toBe(true)

    act(() => result.current.newConversation())
    expect(result.current.hasHistory).toBe(false)
    expect(result.current.chatItems).toHaveLength(0)

    act(() => result.current.sendMessage('fresh question'))
    await waitFor(() => expect(result.current.isRunning).toBe(false))

    // The second POST carried only the new turn's single user message (stateless replay,
    // fresh conversation).
    expect(bodies).toHaveLength(2)
    expect(bodies[1]?.messages).toHaveLength(1)
  })

  it('ignores residual frames from a run discarded by newConversation', async () => {
    const sseA = controlledSse()
    server.use(http.post(/\/copilot$/, () => sseA.response))
    const { result } = renderHook(() => useCopilotAgent())
    act(() => result.current.sendMessage('q1'))
    await waitFor(() => expect(result.current.isRunning).toBe(true))
    act(() => {
      sseA.push(runStarted())
      sseA.push(textStart('a1'))
      sseA.push(textContent('a1', 'old answer'))
    })
    await waitFor(() => expect(assistantOf(result.current.chatItems)?.content).toBe('old answer'))

    act(() => result.current.newConversation())
    expect(result.current.chatItems).toHaveLength(0)

    // The SDK captured the subscriber per-run, so the discarded run keeps streaming —
    // these frames must NOT repopulate the fresh (empty) conversation.
    await act(async () => {
      sseA.push(textContent('a1', ' MORE'))
      await new Promise((r) => setTimeout(r, 30))
    })
    expect(result.current.chatItems).toHaveLength(0)
    act(() => sseA.close())
  })

  it('does not surface a late failure of a discarded run on the fresh conversation', async () => {
    // The discarded run's POST resolves to 503 AFTER newConversation — its runAgent()
    // rejects, and the guarded .catch must not paint an error on the fresh conversation.
    let release = () => {}
    const gate = new Promise<void>((r) => {
      release = r
    })
    server.use(
      http.post(/\/copilot$/, async () => {
        await gate
        return new HttpResponse('no key', { status: 503 })
      }),
    )
    const { result } = renderHook(() => useCopilotAgent())
    act(() => result.current.sendMessage('q1'))
    await waitFor(() => expect(result.current.isRunning).toBe(true))

    act(() => result.current.newConversation())
    expect(result.current.error).toBeNull()

    await act(async () => {
      release()
      await new Promise((r) => setTimeout(r, 30))
    })
    expect(result.current.error).toBeNull()
    expect(result.current.isRunning).toBe(false)
  })
})
