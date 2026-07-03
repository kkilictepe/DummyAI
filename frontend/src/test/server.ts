import { setupServer } from 'msw/node'
import { defaultHandlers } from './handlers'

// Singleton MSW server. Tests add handlers with `server.use(...)`; setupTests.ts
// owns the listen/reset/close lifecycle. Default handlers survive resetHandlers().
export const server = setupServer(...defaultHandlers)
