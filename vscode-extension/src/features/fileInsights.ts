// "File Insights" side view: history graph, commit details, file analysis and contribution ideas
// for the file in the active editor. Also drives the editor decorations for that file.
import * as vscode from 'vscode';
import { Backend } from '../backend/api';
import { HistoryDecorator } from '../editor/historyDecorations';
import { PlanDecorator } from '../editor/planDecorations';
import { FileInsight, FromFileView, PlanEvent, ToFileView } from '../shared/protocol';
import { webviewHtml, webviewOptions } from './webviewHtml';

interface FileState {
  path: string;
  uri: vscode.Uri;
  insight?: FileInsight;
}

export class FileInsightsView implements vscode.WebviewViewProvider, vscode.Disposable {
  private view?: vscode.WebviewView;
  private current?: FileState;
  private repo: string | null = null;
  private readonly subs: vscode.Disposable[] = [];

  constructor(
    private readonly extensionUri: vscode.Uri,
    private readonly backend: Backend,
    private readonly history: HistoryDecorator,
    private readonly plans: PlanDecorator,
    private readonly root: () => string | undefined,
  ) {
    this.subs.push(vscode.window.onDidChangeActiveTextEditor((e) => this.track(e)));
  }

  resolveWebviewView(view: vscode.WebviewView): void {
    this.view = view;
    view.webview.options = webviewOptions(this.extensionUri);
    view.webview.html = webviewHtml(view.webview, this.extensionUri, 'file');
    view.webview.onDidReceiveMessage((m: FromFileView) => this.onMessage(m), null, this.subs);
  }

  /** Called by the hover's "查看详情" link. */
  async showCommit(sha: string): Promise<void> {
    await vscode.commands.executeCommand('reposense.file.focus');
    this.post({ type: 'focus', sha });
    await this.focusCommit(sha);
    await this.loadDetail(sha);
  }

  // ------------------------------------------------------------- active file

  private async track(editor: vscode.TextEditor | undefined): Promise<void> {
    const root = this.root();
    if (!editor || !root || editor.document.uri.scheme !== 'file') return;
    const rel = vscode.workspace.asRelativePath(editor.document.uri, false).replace(/\\/g, '/');
    if (rel === editor.document.uri.fsPath || this.current?.path === rel) return; // outside the repo / unchanged
    this.current = { path: rel, uri: editor.document.uri };
    await this.sendFile();
  }

  private async sendFile(): Promise<void> {
    const file = this.current;
    if (!file) {
      this.post({ type: 'file', path: null, repo: this.repo });
      return;
    }
    try {
      this.repo = (await this.backend.ensureRepo(this.root()!)).repo;
    } catch (err) {
      this.post({ type: 'history', value: { state: 'error', message: (err as Error).message } });
      return;
    }
    this.post({ type: 'file', path: file.path, repo: this.repo });
    await Promise.all([this.loadHistory(file), this.loadInsight(file, false)]);
  }

  private isCurrent(file: FileState): boolean {
    return this.current === file;
  }

  private async loadHistory(file: FileState): Promise<void> {
    const limit = vscode.workspace.getConfiguration('reposense').get<number>('historyLimit', 10);
    this.post({ type: 'history', value: { state: 'loading' } });
    try {
      const data = await this.backend.fileHistory(file.path, limit);
      if (!this.isCurrent(file)) return;
      this.history.set(file.uri, data);
      this.post({ type: 'history', value: { state: 'ready', data } });
    } catch (err) {
      if (this.isCurrent(file)) this.post({ type: 'history', value: { state: 'error', message: (err as Error).message } });
    }
  }

  private async loadInsight(file: FileState, refresh: boolean): Promise<void> {
    this.post({ type: 'insight', value: { state: 'loading', message: '模型正在阅读这个文件…' } });
    try {
      const data = await this.backend.fileInsight(file.path, refresh);
      if (!this.isCurrent(file)) return;
      file.insight = data;
      this.post({ type: 'insight', value: { state: 'ready', data } });
    } catch (err) {
      if (this.isCurrent(file)) this.post({ type: 'insight', value: { state: 'error', message: (err as Error).message } });
    }
  }

  private async loadOpportunities(refresh: boolean): Promise<void> {
    const file = this.current;
    if (!file) return;
    this.post({ type: 'opportunities', value: { state: 'loading', message: '正在搜索相关 Issue，并让模型阅读代码找可贡献的点…' } });
    try {
      const symbols = (file.insight?.symbols ?? []).filter((s) => s.kind === 'class' || s.kind === 'function')
        .map((s) => s.name.split('.').pop()!).slice(0, 3);
      const data = await this.backend.opportunities(file.path, symbols, refresh);
      if (this.isCurrent(file)) this.post({ type: 'opportunities', value: { state: 'ready', data } });
    } catch (err) {
      if (this.isCurrent(file)) this.post({ type: 'opportunities', value: { state: 'error', message: (err as Error).message } });
    }
  }

  private async loadDetail(sha: string): Promise<void> {
    const file = this.current;
    if (!file) return;
    this.post({ type: 'detail', sha, value: { state: 'loading' } });
    try {
      const data = await this.backend.commitDetail(sha, file.path);
      this.post({ type: 'detail', sha, value: { state: 'ready', data } });
    } catch (err) {
      this.post({ type: 'detail', sha, value: { state: 'error', message: (err as Error).message } });
    }
  }

  private async focusCommit(sha: string | null): Promise<void> {
    const file = this.current;
    if (!file) return;
    this.history.setFocus(file.uri, sha);
    const commit = sha ? this.history.get(file.uri)?.commits.find((c) => c.sha === sha) : undefined;
    if (commit?.ranges.length) {
      const [s] = commit.ranges[0];
      await this.reveal(file.path, s, s);
    }
  }

  private async planIssue(number: number): Promise<void> {
    const events: PlanEvent[] = [];
    const sub = this.backend.onPlanEvent((e) => {
      events.push(e);
      this.post({ type: 'plan', number, value: { state: 'loading', message: e.summary }, events: [...events] });
    });
    this.post({ type: 'plan', number, value: { state: 'loading' }, events });
    try {
      const data = await this.backend.issuePlan(number);
      this.plans.set(data.plan?.changes ?? []);
      this.post({ type: 'plan', number, value: { state: 'ready', data }, events });
    } catch (err) {
      this.post({ type: 'plan', number, value: { state: 'error', message: (err as Error).message }, events });
    } finally {
      sub.dispose();
    }
  }

  // ------------------------------------------------------------- messages

  private async onMessage(m: FromFileView): Promise<void> {
    switch (m.type) {
      case 'ready':
        if (!this.current) await this.track(vscode.window.activeTextEditor);
        else await this.sendFile();
        break;
      case 'focusCommit':
        await this.focusCommit(m.sha);
        break;
      case 'loadDetail':
        await this.loadDetail(m.sha);
        break;
      case 'loadInsight':
        if (this.current) await this.loadInsight(this.current, !!m.refresh);
        break;
      case 'loadOpportunities':
        await this.loadOpportunities(!!m.refresh);
        break;
      case 'planIssue':
        await this.planIssue(m.number);
        break;
      case 'reveal':
        await this.reveal(m.path, m.line, m.endLine ?? m.line);
        break;
      case 'openExternal':
        await vscode.env.openExternal(vscode.Uri.parse(m.url));
        break;
      case 'openMap':
        await vscode.commands.executeCommand('reposense.openMap');
        break;
    }
  }

  private async reveal(path: string, line: number, endLine: number): Promise<void> {
    const root = this.root();
    if (!root) return;
    const uri = vscode.Uri.joinPath(vscode.Uri.file(root), path);
    const range = new vscode.Range(Math.max(line - 1, 0), 0, Math.max(endLine - 1, 0), 0);
    const existing = vscode.window.visibleTextEditors.find((e) => e.document.uri.fsPath === uri.fsPath);
    const editor = await vscode.window.showTextDocument(uri, {
      viewColumn: existing?.viewColumn ?? vscode.ViewColumn.Active, preserveFocus: true, preview: false,
    });
    editor.selection = new vscode.Selection(range.start, range.start);
    editor.revealRange(range, vscode.TextEditorRevealType.InCenterIfOutsideViewport);
  }

  private post(msg: ToFileView): void {
    this.view?.webview.postMessage(msg);
  }

  dispose(): void {
    this.subs.forEach((s) => s.dispose());
  }
}
