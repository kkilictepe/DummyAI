import { screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { renderAtRoute } from '../test/render'

describe('AppLayout', () => {
  it('renders the Rasyona brand heading', () => {
    renderAtRoute('/copilot')
    expect(screen.getByRole('heading', { name: 'Rasyona' })).toBeInTheDocument()
  })

  it('shows one menu item per flow, with the three unbuilt flows disabled', () => {
    renderAtRoute('/copilot')
    const items = screen.getAllByRole('menuitem')
    expect(items).toHaveLength(4)
    const disabled = items.filter((el) => el.getAttribute('aria-disabled') === 'true')
    expect(disabled).toHaveLength(3)
  })

  it('marks the active flow (Copilot) as selected', () => {
    renderAtRoute('/copilot')
    const copilot = screen.getByRole('menuitem', { name: /Copilot/ })
    expect(copilot.className).toContain('ant-menu-item-selected')
  })
})
