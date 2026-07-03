import { fireEvent, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { renderWithProviders } from '../../../test/render'
import { Composer } from './Composer'

const ta = () => screen.getByLabelText('Message the Copilot')

describe('Composer', () => {
  it('sends on Enter and clears the input', async () => {
    const user = userEvent.setup()
    const onSend = vi.fn()
    renderWithProviders(<Composer onSend={onSend} onStop={() => {}} isRunning={false} />)
    await user.type(ta(), 'how is KHP')
    await user.keyboard('{Enter}')
    expect(onSend).toHaveBeenCalledWith('how is KHP')
    expect(ta()).toHaveValue('')
  })

  it('inserts a newline on Shift+Enter without sending', async () => {
    const user = userEvent.setup()
    const onSend = vi.fn()
    renderWithProviders(<Composer onSend={onSend} onStop={() => {}} isRunning={false} />)
    await user.type(ta(), 'line1')
    await user.keyboard('{Shift>}{Enter}{/Shift}')
    await user.type(ta(), 'line2')
    expect(onSend).not.toHaveBeenCalled()
    expect(ta()).toHaveValue('line1\nline2')
  })

  it('does not send on the Enter that commits an IME composition', () => {
    const onSend = vi.fn()
    renderWithProviders(<Composer onSend={onSend} onStop={() => {}} isRunning={false} />)
    const el = ta()
    fireEvent.change(el, { target: { value: 'にほんご' } })
    // Enter while composing (CJK candidate commit) must NOT send.
    fireEvent.keyDown(el, { key: 'Enter', isComposing: true })
    expect(onSend).not.toHaveBeenCalled()
    // A normal Enter (composition finished) sends.
    fireEvent.keyDown(el, { key: 'Enter' })
    expect(onSend).toHaveBeenCalledWith('にほんご')
  })

  it('shows Stop while running and calls onStop', async () => {
    const user = userEvent.setup()
    const onStop = vi.fn()
    renderWithProviders(<Composer onSend={() => {}} onStop={onStop} isRunning />)
    expect(screen.queryByRole('button', { name: /send/i })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /stop/i }))
    expect(onStop).toHaveBeenCalled()
  })
})
