import { createBrowserRouter, Navigate, type RouteObject } from 'react-router'
import { NotFoundPage } from '../components/NotFoundPage'
import { RouteErrorPage } from '../components/RouteErrorPage'
import { CopilotPage } from '../features/copilot/page'
import { DEFAULT_FLOW_PATH } from './flows'
import { AppLayout } from './layout'

// Exported for tests (createMemoryRouter). The single app shell wraps every flow;
// `/` redirects to the default flow, unknown paths render the 404 inside the shell,
// and render/loader errors surface through the route errorElement.
export const routes: RouteObject[] = [
  {
    path: '/',
    element: <AppLayout />,
    errorElement: <RouteErrorPage />,
    children: [
      { index: true, element: <Navigate to={DEFAULT_FLOW_PATH} replace /> },
      { path: 'copilot', element: <CopilotPage /> },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
]

export const router = createBrowserRouter(routes)
