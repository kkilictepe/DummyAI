import { describe, expect, it } from 'vitest'
import { createFlowAgent, EventType, HttpAgent, randomUUID } from './agui'
import type { Message } from './agui'
import { env } from './env'

describe('createFlowAgent', () => {
  it('binds the agent to the flow endpoint under the AG-UI base', () => {
    const agent = createFlowAgent('/copilot')
    expect(agent).toBeInstanceOf(HttpAgent)
    expect(agent.url).toBe(`${env.aguiBaseUrl}/copilot`)
  })

  it('passes threadId and initialMessages through', () => {
    const initial: Message[] = [{ id: 'm1', role: 'user', content: 'hi' }]
    const agent = createFlowAgent('/copilot', { threadId: 't-42', initialMessages: initial })
    expect(agent.threadId).toBe('t-42')
    expect(agent.messages).toHaveLength(1)
  })

  it('re-exports SDK primitives so features never import the SDK directly', () => {
    expect(typeof randomUUID()).toBe('string')
    expect(EventType.RUN_STARTED).toBe('RUN_STARTED')
  })
})
