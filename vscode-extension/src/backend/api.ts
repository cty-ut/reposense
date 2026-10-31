// Typed facade over the backend RPC methods. Features call this, never RpcClient directly.
import * as fs from 'fs';
import * as path from 'path';
import * as vscode from 'vscode';
import {
  CommitDetail, Dependencies, FileHistory, FileInsight, FileSummaries, Opportunities, ReadingPath, PlanEvent, PlanResult, RepoStructure,
} from '../shared/protocol';
import { RpcClient } from './rpcClient';

export const SECRET_GITHUB = 'reposense.githubToken';
export const SECRET_DEEPSEEK = 'reposense.deepseekKey';

type Listener<T> = (value: T) => void;

export class Backend implements vscode.Disposable {
  private readonly rpc: RpcClient;
  private readonly progressListeners = new Set<Listener<{ task: string; message: string; percent?: number }>>();
  private readonly planListeners = new Set<Listener<PlanEvent>>();
  private openedRoot?: string;
  private keys: { github?: string; deepseek?: string } = {};
  readonly output = vscode.window.createOutputChannel('RepoSense');

  /** The executable built by packaging/build_backend.py, shipped inside platform-specific .vsix files. */
  readonly bundledPath: string;

  constructor(private readonly secrets: vscode.SecretStorage, extensionPath: string) {
    this.bundledPath = path.join(extensionPath, 'bin', process.platform === 'win32' ? 'reposense-backend.exe' : 'reposense-backend');
    this.rpc = new RpcClient(
      () => {
        const env: NodeJS.ProcessEnv = {};
        if (this.keys.github) env.GITHUB_TOKEN = this.keys.github;
        if (this.keys.deepseek) env.DEEPSEEK_API_KEY = this.keys.deepseek;
        // Never run inside the opened repository: nothing in it (e.g. a .env) may configure the backend.
        // The repository is passed explicitly with `repo.open`.
        return { ...this.program(), cwd: extensionPath, env };
      },
      (method, params) => {
        if (method === 'progress') this.progressListeners.forEach((l) => l(params));
        if (method === 'plan.event') this.planListeners.forEach((l) => l(params.event));
      },
      (line) => this.output.appendLine(line),
    );
  }

  /** An explicit `reposense.pythonPath` wins (running from source); otherwise the bundled backend. */
  private program(): { program: string; args: string[] } {
    const python = vscode.workspace.getConfiguration('reposense').get<string>('pythonPath', '').trim();
    if (python) return { program: python, args: ['-m', 'reposense', 'serve'] };
    if (this.usesBundled) {
      if (process.platform !== 'win32') fs.chmodSync(this.bundledPath, 0o755);  // unzipping may drop the exec bit
      return { program: this.bundledPath, args: [] };
    }
    return { program: 'python', args: ['-m', 'reposense', 'serve'] };
  }

  get usesBundled(): boolean {
    return !vscode.workspace.getConfiguration('reposense').get<string>('pythonPath', '').trim() && fs.existsSync(this.bundledPath);
  }

  async loadKeys(): Promise<void> {
    this.keys = {
      github: await this.secrets.get(SECRET_GITHUB),
      deepseek: await this.secrets.get(SECRET_DEEPSEEK),
    };
  }

  hasKeys(): boolean {
    return !!(this.keys.github && this.keys.deepseek);
  }

  async restart(): Promise<void> {
    await this.loadKeys();
    this.rpc.stop();
    this.openedRoot = undefined;
  }

  onProgress(l: Listener<{ task: string; message: string; percent?: number }>): vscode.Disposable {
    this.progressListeners.add(l);
    return new vscode.Disposable(() => this.progressListeners.delete(l));
  }

  onPlanEvent(l: Listener<PlanEvent>): vscode.Disposable {
    this.planListeners.add(l);
    return new vscode.Disposable(() => this.planListeners.delete(l));
  }

  // ------------------------------------------------------------- methods

  checkConfig(): Promise<{ missing: string[]; model: string }> {
    return this.rpc.request('config.check');
  }

  clone(url: string, parentDir: string): Promise<{ repo: string; root: string }> {
    return this.rpc.request('repo.clone', { url, parent_dir: parentDir });
  }

  /** Opens the workspace repository once per backend process. */
  async ensureRepo(root: string): Promise<{ repo: string; root: string; head: string }> {
    const lang = vscode.workspace.getConfiguration('reposense').get<string>('language', 'Chinese');
    const info = await this.rpc.request<{ repo: string; root: string; head: string }>('repo.open', { path: root, lang });
    this.openedRoot = root;
    return info;
  }

  structure(refresh = false): Promise<RepoStructure> {
    return this.rpc.request('repo.structure', { refresh });
  }

  dependencies(refresh = false): Promise<Dependencies> {
    return this.rpc.request('repo.dependencies', { refresh });
  }

  readingPath(refresh = false): Promise<ReadingPath> {
    return this.rpc.request('repo.readingPath', { refresh });
  }

  summaries(refresh = false): Promise<FileSummaries> {
    return this.rpc.request('repo.summaries', { refresh });
  }

  fileHistory(path: string, limit: number): Promise<FileHistory> {
    return this.rpc.request('file.history', { path, limit });
  }

  commitDetail(sha: string, path: string): Promise<CommitDetail> {
    return this.rpc.request('commit.detail', { sha, path });
  }

  fileInsight(path: string, refresh = false): Promise<FileInsight> {
    return this.rpc.request('file.insight', { path, refresh });
  }

  opportunities(path: string, symbols: string[], refresh = false): Promise<Opportunities> {
    return this.rpc.request('file.opportunities', { path, symbols, refresh });
  }

  issuePlan(number: number): Promise<PlanResult> {
    return this.rpc.request('issue.plan', { number });
  }

  get repoRoot(): string | undefined {
    return this.openedRoot;
  }

  dispose(): void {
    this.rpc.stop();
    this.output.dispose();
  }
}
