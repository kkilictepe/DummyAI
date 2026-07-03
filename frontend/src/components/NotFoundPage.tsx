import { Button, Result } from 'antd'
import { useNavigate } from 'react-router'
import { DEFAULT_FLOW_PATH } from '../app/flows'

export function NotFoundPage() {
  const navigate = useNavigate()
  return (
    <Result
      status="404"
      title="Page not found"
      subTitle="That flow doesn't exist. Pick one from the menu on the left."
      extra={
        <Button type="primary" onClick={() => navigate(DEFAULT_FLOW_PATH)}>
          Go to Copilot
        </Button>
      }
    />
  )
}
