import { describe, expect, it } from 'vitest'
import type { Message } from '../../lib/agui'
import { buildChatItems } from './chatItems'
import type { ToolRunStatus } from './types'

const NO_STATUS: Record<string, ToolRunStatus> = {}
const NO_PARTIAL = new Set<string>()

function assistantWithTool(): Message[] {
  return [
    { id: 'u1', role: 'user', content: 'How is KHP?' },
    {
      id: 'a1',
      role: 'assistant',
      toolCalls: [
        { id: 'c1', type: 'function', function: { name: 'list_sap_systems', arguments: '{}' } },
      ],
    },
    { id: 't1', role: 'tool', content: '["KHP","KBP"]', toolCallId: 'c1' },
    { id: 'a2', role: 'assistant', content: '**KHP** is healthy.' },
  ]
}

describe('buildChatItems', () => {
  it('maps a simple user + assistant exchange', () => {
    const messages: Message[] = [
      { id: 'u1', role: 'user', content: 'hi' },
      { id: 'a1', role: 'assistant', content: 'hello' },
    ]
    const items = buildChatItems(messages, NO_STATUS, null, NO_PARTIAL)
    expect(items).toHaveLength(2)
    expect(items[0]).toMatchObject({ kind: 'user', content: 'hi' })
    expect(items[1]).toMatchObject({ kind: 'assistant', content: 'hello' })
  })

  it('folds a tool call + result + answer into one assistant turn', () => {
    const items = buildChatItems(assistantWithTool(), NO_STATUS, null, NO_PARTIAL)
    expect(items).toHaveLength(2)
    const assistant = items[1]
    expect(assistant?.kind).toBe('assistant')
    if (assistant?.kind !== 'assistant') throw new Error('expected assistant')
    expect(assistant.content).toBe('**KHP** is healthy.')
    expect(assistant.toolActivities).toHaveLength(1)
    expect(assistant.toolActivities[0]).toMatchObject({
      toolName: 'list_sap_systems',
      label: 'Listing SAP systems',
      phase: 'done', // a result exists → done, even without a live status
      result: '["KHP","KBP"]',
      args: '{}',
    })
  })

  it('prefers a live tool status over the result-derived phase', () => {
    const statuses: Record<string, ToolRunStatus> = {
      c1: { toolCallId: 'c1', toolName: 'list_sap_systems', phase: 'executing' },
    }
    const items = buildChatItems(assistantWithTool(), statuses, null, NO_PARTIAL)
    const assistant = items[1]
    if (assistant?.kind !== 'assistant') throw new Error('expected assistant')
    expect(assistant.toolActivities[0]?.phase).toBe('executing')
  })

  it('falls back to executing for a tool call with no result yet', () => {
    const messages: Message[] = [
      { id: 'u1', role: 'user', content: 'go' },
      {
        id: 'a1',
        role: 'assistant',
        toolCalls: [
          { id: 'c1', type: 'function', function: { name: 'es_field_search', arguments: '{"q":"x"}' } },
        ],
      },
    ]
    const items = buildChatItems(messages, NO_STATUS, null, NO_PARTIAL)
    const assistant = items[1]
    if (assistant?.kind !== 'assistant') throw new Error('expected assistant')
    expect(assistant.toolActivities[0]?.phase).toBe('executing')
    expect(assistant.toolActivities[0]?.result).toBeUndefined()
  })

  it('flags the streaming and partial assistant turns', () => {
    const messages: Message[] = [
      { id: 'u1', role: 'user', content: 'hi' },
      { id: 'a1', role: 'assistant', content: 'partial ans' },
    ]
    const streaming = buildChatItems(messages, NO_STATUS, 'a1', NO_PARTIAL)[1]
    if (streaming?.kind !== 'assistant') throw new Error('expected assistant')
    expect(streaming.isStreaming).toBe(true)

    const partial = buildChatItems(messages, NO_STATUS, null, new Set(['a1']))[1]
    if (partial?.kind !== 'assistant') throw new Error('expected assistant')
    expect(partial.isPartial).toBe(true)
  })
})
