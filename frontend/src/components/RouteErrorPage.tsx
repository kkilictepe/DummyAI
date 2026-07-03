import { Button, Result } from 'antd'
import { isRouteErrorResponse, useNavigate, useRouteError } from 'react-router'
import { DEFAULT_FLOW_PATH } from '../app/flows'

// react-router v7 data-mode errorElement: catches render/loader errors thrown within
// the routed subtree (class boundaries don't catch those in data mode).
export function RouteErrorPage() {
  const error = useRouteError()
  const navigate = useNavigate()

  const subTitle = isRouteErrorResponse(error)
    ? `${error.status} ${error.statusText}`
    : 'This page hit an unexpected error. Try again, or head back to Copilot.'

  return (
    <Result
      status="error"
      title="Something went wrong"
      subTitle={subTitle}
      extra={
        <Button type="primary" onClick={() => navigate(DEFAULT_FLOW_PATH)}>
          Go to Copilot
        </Button>
      }
    />
  )
}
