import { Component } from 'react';
import type { ErrorInfo, ReactNode } from 'react';

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Unhandled error in component tree:', error, info.componentStack);
  }

  handleReset = () => {
    this.setState({ error: null });
    window.location.assign('/');
  };

  render() {
    if (this.state.error) {
      return (
        <div className="card" style={{ margin: '2rem auto', maxWidth: 480, textAlign: 'center' }}>
          <h2>Something went wrong</h2>
          <p>This page hit an unexpected error. You can try going back to the home page.</p>
          <button className="btn btn-primary" onClick={this.handleReset}>
            Back to Today's Picks
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
