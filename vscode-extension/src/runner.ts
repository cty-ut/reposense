import { spawn, ChildProcess, execFile } from 'child_process';
import * as vscode from 'vscode';
import { AgentEvent } from './types';

/** Resolve "#123", "123" or a full URL into an issue URL for the workspace's GitHub repo. */
export async function resolveIssueUrl(input: string, cwd: string): Promise<string> {
  const text = input.trim();
  if (/^https:\/\/github\.com\/[\w.-]+\/[\w.-]+\/issues\/\d+/.test(text)) {
    return text;
  }
  const num = text.replace(/^#/, '');
  if (!/^\d+$/.test(num)) {
    throw new Error('Enter an issue number (e.g. 3652) or a GitHub issue URL.');
  }
  const remote = await new Promise<string>((resolve, reject) =>
    execFile('git', ['remote', 'get-url', 'origin'], { cwd }, (err, out) => (err ? reject(err) : resolve(out.trim()))),
  );
  const m = remote.match(/github\.com[/:]([\w.-]+)\/([\w.-]+?)(?:\.git)?$/);
  if (!m) {
    throw new Error(`The workspace remote is not a GitHub repository: ${remote}`);
  }
  return `https://github.com/${m[1]}/${m[2]}/issues/${num}`;
}

/** Run the Python agent and stream its JSON-line events. */
export function runAgent(
  issueUrl: string,
  cwd: string,
  onEvent: (e: AgentEvent) => void,
): { process: ChildProcess; done: Promise<number> } {
  const cfg = vscode.workspace.getConfiguration('reposense');
  const args = ['-m', 'reposense', 'plan', issueUrl, '--repo-path', cwd, '--jsonl',
    '--lang', cfg.get<string>('language', 'Chinese')];
  if (!cfg.get<boolean>('useHistory', true)) {
    args.push('--no-history');
  }
  const proc = spawn(cfg.get<string>('pythonPath', 'python'), args, {
    cwd,
    env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
  });

  let buffer = '';
  let stderr = '';
  proc.stdout.setEncoding('utf8');
  proc.stdout.on('data', (chunk: string) => {
    buffer += chunk;
    let nl: number;
    while ((nl = buffer.indexOf('\n')) >= 0) {
      const line = buffer.slice(0, nl).trim();
      buffer = buffer.slice(nl + 1);
      if (!line) {
        continue;
      }
      try {
        onEvent(JSON.parse(line) as AgentEvent);
      } catch {
        // non-JSON output (warnings from libraries) is ignored
      }
    }
  });
  proc.stderr.setEncoding('utf8');
  proc.stderr.on('data', (chunk: string) => (stderr += chunk));

  const done = new Promise<number>((resolve) => {
    proc.on('error', (err) => {
      onEvent({ type: 'error', message: `Cannot start Python (${err.message}). Set "reposense.pythonPath".` });
      resolve(-1);
    });
    proc.on('close', (code) => {
      if (code && code !== 0 && stderr.trim()) {
        onEvent({ type: 'error', message: stderr.trim().split('\n').slice(-3).join('\n') });
      }
      resolve(code ?? 0);
    });
  });
  return { process: proc, done };
}
