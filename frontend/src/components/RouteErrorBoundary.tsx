import { Component, type ErrorInfo, type ReactNode } from 'react'

interface Props {
  children: ReactNode
  destination: string
}

interface State {
  failed: boolean
}

export default class RouteErrorBoundary extends Component<Props, State> {
  state: State = { failed: false }

  static getDerivedStateFromError(): State {
    return { failed: true }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Route render failed', error, info)
  }

  render() {
    if (!this.state.failed) return this.props.children
    return (
      <section className="route-state" role="alert" aria-labelledby="route-error-title">
        <p className="mono text-muted">ROUTE ERROR</p>
        <h2 id="route-error-title">Page could not be rendered</h2>
        <p className="text-muted">The application shell is still available. Destination: {this.props.destination}</p>
        <div className="actions">
          <button className="btn btn-secondary" onClick={() => this.setState({ failed: false })}>Retry</button>
          <a className="btn btn-secondary" href="/">Return to Overview</a>
        </div>
      </section>
    )
  }
}
