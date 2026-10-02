import { Component, type ReactNode } from "react";

/** Keeps a throw below it from unmounting the whole page: shows a message and a way back. */
export class ErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true };
  }

  render(): ReactNode {
    if (!this.state.failed) return this.props.children;
    return (
      <main className="mx-auto max-w-3xl space-y-3 p-6">
        <p>Could not show this replay</p>
        <a className="underline" href="#/">
          Back to all replays
        </a>
      </main>
    );
  }
}
