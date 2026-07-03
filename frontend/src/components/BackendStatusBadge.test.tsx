import { screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import { renderWithProviders } from '../test/render'
import { server } from '../test/server'
import { BackendStatusBadge } from './BackendStatusBadge'

describe('BackendStatusBadge', () => {
  it('shows online when the backend health is ok', async () => {
    renderWithProviders(<BackendStatusBadge />)
    expect(await screen.findByText('Backend online')).toBeInTheDocument()
  })

  it('shows unreachable when the health request fails', async () => {
    server.use(http.get(/\/health$/, () => new HttpResponse(null, { status: 500 })))
    renderWithProviders(<BackendStatusBadge />)
    expect(await screen.findByText('Backend unreachable')).toBeInTheDocument()
  })
})
