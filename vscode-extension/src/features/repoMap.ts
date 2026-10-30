// Repository map panel: loads the structure from the backend and opens files the user clicks.
import * as vscode from 'vscode';
import { Backend } from '../backend/api';
import { FromMapView, RepoStructure, ToMapView } from '../shared/protocol';
import { webviewHtml, webviewOptions } from './webviewHtml';

export class RepoMapPanel implements vscode.Disposable {
  private panel?: vscode.WebviewPanel;
  private structure?: RepoStructure;
  private readonly subs: vscode.Disposable[] = [];

  constructor(
    private readonly extensionUri: vscode.Uri,
    private readonly backend: Backend,
    private readonly root: () => string | undefined,
  ) {
    this.subs.push(vscode.window.onDidChangeActiveTextEditor((e) => this.post({ type: 'activeFile', path: this.rel(e) })));
  }

  async show(refresh = false): Promise<void> {
    const root = this.root();
    if (!root) {
      vscode.window.showInformationMessage('Open a repository folder first, or run "RepoSense: Open a GitHub Repository…".');
      return;
    }
    if (this.panel) {
      this.panel.reveal();
    } else {
      this.panel = vscode.window.createWebviewPanel('reposense.map', 'Repository Map', vscode.ViewColumn.Active,
        { ...webviewOptions(this.extensionUri), retainContextWhenHidden: true });
      this.panel.iconPath = vscode.Uri.joinPath(this.extensionUri, 'media', 'icon.svg');
      this.panel.webview.html = webviewHtml(this.panel.webview, this.extensionUri, 'map');
      this.panel.webview.onDidReceiveMessage((m: FromMapView) => this.onMessage(m), null, this.subs);
      this.panel.onDidDispose(() => (this.panel = undefined), null, this.subs);
    }
    if (refresh || !this.structure) await this.load(refresh);
  }

  private async load(refresh: boolean): Promise<void> {
    const root = this.root()!;
    this.post({ type: 'structure', value: { state: 'loading', message: '正在读取文件和提交历史，并让模型归类架构层…' } });
    try {
      await this.backend.ensureRepo(root);
      this.structure = await this.backend.structure(refresh);
      this.post({ type: 'structure', value: { state: 'ready', data: this.structure } });
    } catch (err) {
      this.post({ type: 'structure', value: { state: 'error', message: (err as Error).message } });
    }
  }

  private async onMessage(m: FromMapView): Promise<void> {
    if (m.type === 'ready') {
      if (this.structure) this.post({ type: 'structure', value: { state: 'ready', data: this.structure } });
      this.post({ type: 'activeFile', path: this.rel(vscode.window.activeTextEditor) });
    } else if (m.type === 'refresh') {
      await this.load(true);
    } else if (m.type === 'openFile') {
      const uri = vscode.Uri.joinPath(vscode.Uri.file(this.root()!), m.path);
      await vscode.window.showTextDocument(uri, { viewColumn: vscode.ViewColumn.Beside, preview: false });
      await vscode.commands.executeCommand('reposense.file.focus');
    }
  }

  private rel(editor: vscode.TextEditor | undefined): string | null {
    const root = this.root();
    if (!editor || !root) return null;
    const rel = vscode.workspace.asRelativePath(editor.document.uri, false).replace(/\\/g, '/');
    return rel === editor.document.uri.fsPath ? null : rel;
  }

  private post(msg: ToMapView): void {
    this.panel?.webview.postMessage(msg);
  }

  dispose(): void {
    this.panel?.dispose();
    this.subs.forEach((s) => s.dispose());
  }
}
