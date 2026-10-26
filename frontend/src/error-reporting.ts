/* 浏览器异常只记录到当前 Peach 主机。失败不再生成新的错误请求。 */
let installed = false;

export function installErrorReporting(): void {
  if (installed || typeof window === 'undefined') return;
  installed = true;
  let start = Date.now();
  let count = 0;
  const seen = new Set<string>();
  const send = (kind: string, cause: unknown, fallback = '', source?: ErrorEvent) => {
    const errorName = cause instanceof Error || cause instanceof DOMException ? cause.name : '';
    if (errorName === 'AbortError') return;
    if (Date.now() - start >= 60_000) {
      start = Date.now(); count = 0; seen.clear();
    }
    if (count >= 10) return;
    const message = (cause instanceof Error ? cause.message : typeof cause === 'string' ? cause : fallback).slice(0, 2000);
    if (!message) return;
    const stack = cause instanceof Error ? (cause.stack ?? '').slice(0, 6000) : '';
    const key = `${kind}:${message}:${stack}`;
    if (seen.has(key)) return;
    seen.add(key); count += 1;
    void fetch('/api/diagnostics/client-error', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ kind, message, stack, error_name: errorName, page: window.location.pathname,
        source_file: source?.filename?.split('/').pop()?.split('?')[0],
        line: source?.lineno, column: source?.colno }), credentials: 'same-origin', keepalive: true,
    }).catch(() => {});
  };
  window.addEventListener('error', event => send('error', event.error, event.message, event));
  window.addEventListener('unhandledrejection', event => send('unhandledrejection', event.reason, 'Promise 未处理的异常'));
}
