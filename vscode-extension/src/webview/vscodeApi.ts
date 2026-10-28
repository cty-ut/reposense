// Bridge between a webview and the extension host.
import { useEffect } from 'react';

interface VsCodeApi {
  postMessage(msg: unknown): void;
}

declare function acquireVsCodeApi(): VsCodeApi;

const api: VsCodeApi = acquireVsCodeApi();

export function post<T>(msg: T): void {
  api.postMessage(msg);
}

/** Subscribe to messages from the host for the lifetime of the component. */
export function useHostMessages<T>(handler: (msg: T) => void): void {
  useEffect(() => {
    const listener = (e: MessageEvent) => handler(e.data as T);
    window.addEventListener('message', listener);
    return () => window.removeEventListener('message', listener);
  }, [handler]);
}
