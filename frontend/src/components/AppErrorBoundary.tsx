import { Button, Result } from 'antd'
import { Component, type ErrorInfo, type ReactNode } from 'react'

interface Props {
  children: ReactNode
}
interface State {
  error: Error | null
}

// Top-level safety net for failures OUTSIDE the router subtree (e.g. providers,
// RouterProvider itself). Router render errors are handled by RouteErrorPage.
export class AppErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Log for diagnostics; never surface raw error detail to the user.
    console.error('App crashed:', error, info.componentStack)
  }

  render(): ReactNode {
    if (this.state.error) {
      return (
        <Result
          status="error"
          title="Rasyona failed to load"
          subTitle="Reload the page to try again. If it keeps happening, contact your platform team."
          extra={
            <Button type="primary" onClick={() => window.location.reload()}>
              Reload
            </Button>
          }
        />
      )
    }
    return this.props.children
  }
}
