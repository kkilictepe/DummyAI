import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { type RouteObject } from 'react-router'
import { describe, expect, it, vi } from 'vitest'
import { RouteErrorPage } from '../components/RouteErrorPage'
import { renderAtRoute } from '../test/render'

describe('router', () => {
  it('redirects / to the default flow (/copilot)', async () => {
    const { router } = renderAtRoute('/')
    await waitFor(() => expect(router.state.location.pathname).toBe('/copilot'))
    expect(screen.getByTestId('copilot-page')).toBeInTheDocument()
  })

  it('renders the 404 page inside the shell for unknown routes', () => {
    renderAtRoute('/does-not-exist')
    expect(screen.getByText('Page not found')).toBeInTheDocument()
    // The app shell (header + menu) is still present around the 404.
    expect(screen.getByRole('heading', { name: 'Rasyona' })).toBeInTheDocument()
  })

  it('navigates to a flow when its menu item is clicked', async () => {
    const user = userEvent.setup()
    const { router } = renderAtRoute('/does-not-exist')
    await user.click(screen.getByRole('menuitem', { name: /Copilot/ }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/copilot'))
    expect(screen.getByTestId('copilot-page')).toBeInTheDocument()
  })

  it('surfaces render errors through the route errorElement', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const Boom = () => {
      throw new Error('boom')
    }
    const routes: RouteObject[] = [
      { path: '/', element: <Boom />, errorElement: <RouteErrorPage /> },
    ]
    renderAtRoute('/', routes)
    expect(screen.getByText('Something went wrong')).toBeInTheDocument()
    spy.mockRestore()
  })
})
