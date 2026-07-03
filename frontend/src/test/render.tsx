import { render, type RenderResult } from '@testing-library/react'
import type { ReactElement } from 'react'
import { createMemoryRouter, RouterProvider, type RouteObject } from 'react-router'
import { AppProviders } from '../app/providers'
import { routes as appRoutes } from '../app/router'

/** Render a component tree inside the app's AntD providers (no router). */
export function renderWithProviders(ui: ReactElement): RenderResult {
  return render(<AppProviders>{ui}</AppProviders>)
}

/** Render the app at a route via an in-memory router (providers included). */
export function renderAtRoute(
  initialPath = '/',
  routes: RouteObject[] = appRoutes,
): RenderResult & { router: ReturnType<typeof createMemoryRouter> } {
  const router = createMemoryRouter(routes, { initialEntries: [initialPath] })
  const result = render(
    <AppProviders>
      <RouterProvider router={router} />
    </AppProviders>,
  )
  return { ...result, router }
}
