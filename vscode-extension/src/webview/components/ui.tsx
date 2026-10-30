// Small presentational building blocks shared by the map and the file view.
import { ReactNode } from 'react';
import { Load } from '../../shared/protocol';

export function Spinner({ message }: { message?: string }) {
  return (
    <div className="state">
      <div className="spinner" />
      {message ?? '加载中…'}
    </div>
  );
}

export function ErrorBox({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="state error">
      {message}
      {onRetry && <div style={{ marginTop: 8 }}><button className="btn" onClick={onRetry}>重试</button></div>}
    </div>
  );
}

/** Renders the right UI for each loading state; `render` only runs when data is ready. */
export function Loadable<T>({ load, render, idle, onRetry }: {
  load: Load<T>;
  render: (data: T) => ReactNode;
  idle?: ReactNode;
  onRetry?: () => void;
}) {
  switch (load.state) {
    case 'idle':
      return <>{idle ?? null}</>;
    case 'loading':
      return <Spinner message={load.message} />;
    case 'error':
      return <ErrorBox message={load.message} onRetry={onRetry} />;
    case 'ready':
      return <>{render(load.data)}</>;
  }
}

export function SectionTitle({ children, right }: { children: ReactNode; right?: ReactNode }) {
  return (
    <div className="row section-title">
      <span className="grow">{children}</span>
      {right}
    </div>
  );
}

export function Link({ href, onOpen, children }: { href: string; onOpen: (url: string) => void; children: ReactNode }) {
  return <a onClick={(e) => { e.stopPropagation(); onOpen(href); }} title={href}>{children}</a>;
}
