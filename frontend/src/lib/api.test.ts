import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import { server } from '../test/server'
import { getHealth } from './api'

describe('getHealth', () => {
  it('returns the parsed health payload', async () => {
    server.use(
      http.get(/\/health$/, () =>
        HttpResponse.json({ status: 'ok', environment: 'test' }),
      ),
    )
    await expect(getHealth()).resolves.toEqual({ status: 'ok', environment: 'test' })
  })

  it('rejects on a server error', async () => {
    server.use(http.get(/\/health$/, () => new HttpResponse('boom', { status: 500 })))
    await expect(getHealth()).rejects.toThrow()
  })
})
