// First-run flow: API keys → GitHub URL → clone → open the folder → show the repository map.
import * as os from 'os';
import * as path from 'path';
import * as vscode from 'vscode';
import { Backend, SECRET_DEEPSEEK, SECRET_GITHUB } from '../backend/api';

const WELCOMED = 'reposense.welcomed';
const PENDING_MAP = 'reposense.pendingMap';
const REPO_URL = /^https?:\/\/github\.com\/[\w.-]+\/[\w.-]+?(\.git)?\/?$/;

/** VS Code reports `c:\…` while Python reports `C:\…`; Windows paths are case-insensitive anyway. */
function samePath(a: string, b: string): boolean {
  const norm = (p: string) => (process.platform === 'win32' ? path.resolve(p).toLowerCase() : path.resolve(p));
  return norm(a) === norm(b);
}

export class Onboarding {
  constructor(private readonly context: vscode.ExtensionContext, private readonly backend: Backend) {}

  /** Called on activation: greet first-time users, or open the map right after a clone. */
  async onActivate(openMap: () => void): Promise<void> {
    const root = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
    const pending = this.context.globalState.get<string>(PENDING_MAP);
    if (root && pending && samePath(pending, root)) {
      await this.context.globalState.update(PENDING_MAP, undefined);
      openMap();
      return;
    }
    if (!this.context.globalState.get<boolean>(WELCOMED)) {
      await this.context.globalState.update(WELCOMED, true);
      await vscode.commands.executeCommand('workbench.view.extension.reposense');
      if (!root) await this.start();
    }
  }

  /** Make sure the Python backend starts; otherwise let the user pick the interpreter. */
  async ensureBackend(): Promise<boolean> {
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        await this.backend.checkConfig();
        return true;
      } catch (err) {
        this.backend.output.appendLine(String((err as Error).message));
        const choice = await vscode.window.showErrorMessage(this.backend.usesBundled
          ? 'RepoSense could not start its backend (details in the "RepoSense" output). You can instead run it from '
            + 'source: select a Python interpreter where `reposense` is installed.'
          : 'RepoSense could not start its Python backend. Select the Python interpreter where `reposense` is installed '
            + '(pip install -e <reposense repo>).', { modal: true }, 'Select Python…');
        if (choice !== 'Select Python…') return false;
        const picked = await vscode.window.showOpenDialog({
          canSelectFiles: true, canSelectMany: false, openLabel: 'Use this Python',
          filters: process.platform === 'win32' ? { Python: ['exe'] } : undefined,
        });
        if (!picked?.[0]) return false;
        await vscode.workspace.getConfiguration('reposense').update('pythonPath', picked[0].fsPath, vscode.ConfigurationTarget.Global);
        await this.backend.restart();
      }
    }
    return false;
  }

  /** Ask for whatever key is missing. Returns false if the user cancels. */
  async ensureKeys(force = false): Promise<boolean> {
    const secrets = this.context.secrets;
    if (force || !(await secrets.get(SECRET_GITHUB))) {
      const token = await this.githubToken();
      if (!token) return false;
      await secrets.store(SECRET_GITHUB, token);
    }
    if (force || !(await secrets.get(SECRET_DEEPSEEK))) {
      const key = await vscode.window.showInputBox({
        title: 'RepoSense (2/2): DeepSeek API Key',
        prompt: 'Used to summarize code and history. Stored in VS Code secret storage.',
        password: true, ignoreFocusOut: true, placeHolder: 'sk-…',
        validateInput: (v) => (v.trim().startsWith('sk-') ? undefined : 'A DeepSeek key starts with "sk-"'),
      });
      if (!key) return false;
      await secrets.store(SECRET_DEEPSEEK, key.trim());
    }
    await this.backend.restart();
    return true;
  }

  private async githubToken(): Promise<string | undefined> {
    const pick = await vscode.window.showQuickPick([
      { label: '$(github) Sign in with GitHub', detail: 'Use your VS Code GitHub account (recommended)', id: 'session' },
      { label: '$(key) Paste a personal access token', id: 'pat' },
    ], { title: 'RepoSense (1/2): GitHub access — needed to read pull requests and reviews', ignoreFocusOut: true });
    if (pick?.id === 'session') {
      const session = await vscode.authentication.getSession('github', ['repo'], { createIfNone: true });
      return session?.accessToken;
    }
    if (pick?.id === 'pat') {
      return (await vscode.window.showInputBox({ prompt: 'GitHub token', password: true, ignoreFocusOut: true }))?.trim();
    }
    return undefined;
  }

  /** Ask for a GitHub URL, clone it, and reopen VS Code on the clone. */
  async start(): Promise<void> {
    if (!(await this.ensureBackend()) || !(await this.ensureKeys())) return;
    const url = await vscode.window.showInputBox({
      title: 'RepoSense: which repository do you want to contribute to?',
      prompt: 'GitHub repository URL', placeHolder: 'https://github.com/pallets/click', ignoreFocusOut: true,
      validateInput: (v) => (REPO_URL.test(v.trim()) ? undefined : 'Enter a URL like https://github.com/owner/repo'),
    });
    if (!url) return;

    const parent = await this.chooseParent();
    if (!parent) return;

    try {
      const result = await vscode.window.withProgress({
        location: vscode.ProgressLocation.Notification, title: `Cloning ${url.trim()}`, cancellable: false,
      }, async (progress) => {
        let last = 0;
        const sub = this.backend.onProgress((p) => {
          if (p.task !== 'clone' || p.percent === undefined) return;
          progress.report({ message: `${p.message} ${p.percent}%`, increment: Math.max(0, p.percent - last) / 3 });
          last = p.percent;
        });
        try {
          return await this.backend.clone(url.trim(), parent);
        } finally {
          sub.dispose();
        }
      });
      await this.context.globalState.update(PENDING_MAP, result.root);
      await vscode.commands.executeCommand('vscode.openFolder', vscode.Uri.file(result.root), { forceNewWindow: false });
    } catch (err) {
      vscode.window.showErrorMessage(`RepoSense: clone failed — ${(err as Error).message}`);
    }
  }

  private async chooseParent(): Promise<string | undefined> {
    const configured = vscode.workspace.getConfiguration('reposense').get<string>('cloneDirectory');
    const fallback = configured || path.join(os.homedir(), 'RepoSense');
    const pick = await vscode.window.showQuickPick([
      { label: `$(folder) ${fallback}`, id: 'default' },
      { label: '$(folder-opened) Choose another folder…', id: 'pick' },
    ], { title: 'Where should the repository be cloned?', ignoreFocusOut: true });
    if (pick?.id === 'default') return fallback;
    if (pick?.id === 'pick') {
      const folders = await vscode.window.showOpenDialog({ canSelectFolders: true, canSelectFiles: false, openLabel: 'Clone here' });
      return folders?.[0]?.fsPath;
    }
    return undefined;
  }
}
