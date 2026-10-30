// Starts `python -m reposense serve` and talks JSON-RPC 2.0 over its stdin/stdout (one JSON per line).
import { ChildProcessWithoutNullStreams, spawn } from 'child_process';

type Pending = { resolve: (v: unknown) => void; reject: (e: Error) => void; method: string };
type NotificationHandler = (method: string, params: any) => void;

export class RpcClient {
  private proc?: ChildProcessWithoutNullStreams;
  private nextId = 1;
  private pending = new Map<number, Pending>();
  private buffer = '';
  private stderrTail = '';

  constructor(
    private readonly command: () => { python: string; cwd: string; env: NodeJS.ProcessEnv },
    private readonly onNotification: NotificationHandler,
    private readonly log: (line: string) => void,
  ) {}

  get running(): boolean {
    return !!this.proc && this.proc.exitCode === null;
  }

  start(): void {
    const { python, cwd, env } = this.command();
    this.log(`starting backend: ${python} -m reposense serve (cwd ${cwd})`);
    const proc = spawn(python, ['-m', 'reposense', 'serve'], {
      cwd, env: { ...process.env, ...env, PYTHONIOENCODING: 'utf-8', PYTHONUNBUFFERED: '1' },
    });
    this.proc = proc;
    proc.stdout.setEncoding('utf8');
    proc.stdout.on('data', (chunk: string) => this.onData(chunk));
    proc.stderr.setEncoding('utf8');
    proc.stderr.on('data', (chunk: string) => {
      this.stderrTail = (this.stderrTail + chunk).slice(-4000);
      chunk.split('\n').filter(Boolean).forEach((l) => this.log(`[backend] ${l}`));
    });
    const fail = (why: string) => {
      for (const p of this.pending.values()) p.reject(new Error(`${why}\n${this.stderrTail.trim().split('\n').slice(-4).join('\n')}`));
      this.pending.clear();
      if (this.proc === proc) this.proc = undefined;
    };
    proc.on('error', (err) => fail(`Cannot start Python "${python}": ${err.message}`));
    proc.on('exit', (code) => fail(`Backend exited (code ${code}).`));
  }

  stop(): void {
    this.proc?.kill();
    this.proc = undefined;
  }

  request<T>(method: string, params: Record<string, unknown> = {}): Promise<T> {
    if (!this.running) this.start();
    const id = this.nextId++;
    return new Promise<T>((resolve, reject) => {
      this.pending.set(id, { resolve: resolve as (v: unknown) => void, reject, method });
      this.proc!.stdin.write(JSON.stringify({ jsonrpc: '2.0', id, method, params }) + '\n');
    });
  }

  private onData(chunk: string): void {
    this.buffer += chunk;
    let nl: number;
    while ((nl = this.buffer.indexOf('\n')) >= 0) {
      const line = this.buffer.slice(0, nl).trim();
      this.buffer = this.buffer.slice(nl + 1);
      if (line) this.dispatch(line);
    }
  }

  private dispatch(line: string): void {
    let msg: any;
    try {
      msg = JSON.parse(line);
    } catch {
      this.log(`[backend:stdout] ${line}`);
      return;
    }
    if (msg.id === undefined || msg.id === null) {
      if (msg.method) this.onNotification(msg.method, msg.params);
      return;
    }
    const p = this.pending.get(msg.id);
    if (!p) return;
    this.pending.delete(msg.id);
    if (msg.error) p.reject(new Error(msg.error.message ?? 'Backend error'));
    else p.resolve(msg.result);
  }
}
