import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http } from 'msw'
import { describe, expect, it } from 'vitest'
import { AppProviders } from '../../app/providers'
import { happyPathWithTool, textOnly } from '../../test/aguiEvents'
import { server } from '../../test/server'
import { sseResponse } from '../../test/sse'
import { CopilotPage } from './page'

function renderPage() {
  return render(
    <AppProviders>
      <CopilotPage />
    </AppProviders>,
  )
}

const composer = () => screen.getByLabelText('Message the Copilot')

describe('CopilotPage', () => {
  it('starts on the empty state and sends a clicked starter prompt', async () => {
    const user = userEvent.setup()
    let body: { messages?: Array<{ content?: string }> } | undefined
    server.use(
      http.post(/\/copilot$/, async ({ request }) => {
        body = (await request.json()) as typeof body
        return sseResponse(textOnly('We monitor KHP and KBP.'))
      }),
    )
    renderPage()
    expect(screen.getByText('Ask about your SAP systems')).toBeInTheDocument()

    await user.click(screen.getByText('Which SAP systems do you monitor?'))
    await waitFor(() => expect(screen.getByText('We monitor KHP and KBP.')).toBeInTheDocument())
    expect(body?.messages?.[0]?.content).toBe('Which SAP systems do you monitor?')
  })

  it('renders the tool activity timeline and the markdown answer', async () => {
    const user = userEvent.setup()
    server.use(
      http.post(/\/copilot$/, () =>
        sseResponse(happyPathWithTool({ answer: '**KHP** is healthy.' })),
      ),
    )
    renderPage()
    await user.type(composer(), 'How is KHP?')
    await user.keyboard('{Enter}')

    // Friendly tool label appears, and expanding the step reveals the raw args/result.
    const toolStep = await screen.findByText('Listing SAP systems')
    expect(screen.getByText('is healthy.', { exact: false })).toBeInTheDocument()
    await user.click(toolStep)
    expect(await screen.findByText('Arguments')).toBeInTheDocument()
    expect(screen.getByText('Result')).toBeInTheDocument()
  })

  it('new conversation returns to the empty state', async () => {
    const user = userEvent.setup()
    server.use(http.post(/\/copilot$/, () => sseResponse(textOnly('Answer.'))))
    renderPage()
    await user.type(composer(), 'hello')
    await user.keyboard('{Enter}')
    await waitFor(() => expect(screen.getByText('Answer.')).toBeInTheDocument())

    await user.click(screen.getByRole('button', { name: /new conversation/i }))
    // Confirm in the Popconfirm popup.
    const confirm = await screen.findByRole('button', { name: 'Clear chat' })
    await user.click(confirm)
    await waitFor(() =>
      expect(screen.getByText('Ask about your SAP systems')).toBeInTheDocument(),
    )
  })
})
