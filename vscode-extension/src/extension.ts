import * as fs from 'fs';
import * as vscode from 'vscode';
import { ChildProcess } from 'child_process';
import { Annotator } from './decorations';
import { DetailsPanel } from './panel';
import { PlanTreeProvider } from './planTree';
import { resolveIssueUrl, runAgent } from './runner';
import { Result } from './types';

const STATE_KEY = 'reposense.lastResult';

export function activate(context: vscode.ExtensionContext): void {
  const root = () => vscode.workspace.workspaceFolders?.[0]?.uri;
  const tree = new PlanTreeProvider(root);
  const annotator = new Annotator(root);
  const openFile = async (file: string, line: number) => {
    const r = root();
    if (!r) return;
    const doc = await vscode.workspace.openTextDocument(vscode.Uri.joinPath(r, file));
    const pos = new vscode.Position(Math.max(line - 1, 0), 0);
    await vscode.window.showTextDocument(doc, { viewColumn: vscode.ViewColumn.One, selection: new vscode.Range(pos, pos) });
  };
  const panel = new DetailsPanel(openFile);
  let running: ChildProcess | undefined;

  const apply = (result: Result) => {
    tree.setResult(result);
    annotator.set(result);
    context.workspaceState.update(STATE_KEY, result);
  };

  context.subscriptions.push(
    vscode.window.registerTreeDataProvider('reposense.plan', tree),
    vscode.window.onDidChangeVisibleTextEditors(() => annotator.refreshAll()),
    vscode.workspace.onDidChangeTextDocument((e) => {
      const ed = vscode.window.visibleTextEditors.find((x) => x.document === e.document);
      if (ed) annotator.refresh(ed);
    }),

    vscode.commands.registerCommand('reposense.analyzeIssue', async () => {
      const cwd = root()?.fsPath;
      if (!cwd) {
        vscode.window.showErrorMessage('Open the repository folder you want to contribute to first.');
        return;
      }
      if (running) {
        const stop = await vscode.window.showWarningMessage('An analysis is already running.', 'Stop it');
        if (stop) running.kill();
        return;
      }
      const input = await vscode.window.showInputBox({
        title: 'RepoSense: Analyze Issue',
        prompt: 'Issue number in this repository, or a GitHub issue URL',
        placeHolder: '3652',
      });
      if (!input) return;

      let url: string;
      try {
        url = await resolveIssueUrl(input, cwd);
      } catch (err) {
        vscode.window.showErrorMessage(String((err as Error).message ?? err));
        return;
      }

      panel.reveal();
      panel.reset();
      tree.setResult(undefined);
      annotator.set(undefined);
      tree.setStatus('Exploring code and history…');

      await vscode.window.withProgress({
        location: vscode.ProgressLocation.Notification, title: 'RepoSense', cancellable: true,
      }, async (progress, token) => {
        const job = runAgent(url, cwd, (e) => {
          panel.push(e);
          if (e.type === 'tool_call') {
            const msg = `${e.name}${e.reason ? ': ' + e.reason : ''}`;
            progress.report({ message: msg.slice(0, 90) });
            tree.setStatus(`Step ${e.step}: ${e.name}`);
          } else if (e.type === 'result') {
            apply(e.result);
          } else if (e.type === 'error') {
            tree.setStatus(undefined);
            vscode.window.showErrorMessage(`RepoSense: ${e.message}`);
          }
        });
        running = job.process;
        token.onCancellationRequested(() => job.process.kill());
        await job.done;
        running = undefined;
        tree.setStatus(undefined);
      });
    }),

    vscode.commands.registerCommand('reposense.openResult', async () => {
      const picked = await vscode.window.showOpenDialog({ filters: { 'RepoSense result': ['json'] }, canSelectMany: false });
      if (!picked?.[0]) return;
      try {
        const result = JSON.parse(fs.readFileSync(picked[0].fsPath, 'utf8')) as Result;
        apply(result);
        panel.showResult(result);
      } catch (err) {
        vscode.window.showErrorMessage(`Not a RepoSense result: ${(err as Error).message}`);
      }
    }),

    vscode.commands.registerCommand('reposense.showPanel', () => {
      const last = context.workspaceState.get<Result>(STATE_KEY);
      if (last) panel.showResult(last);
      else panel.reveal();
    }),

    vscode.commands.registerCommand('reposense.clear', () => {
      tree.setResult(undefined);
      annotator.set(undefined);
      context.workspaceState.update(STATE_KEY, undefined);
    }),
  );

  const last = context.workspaceState.get<Result>(STATE_KEY);
  if (last) {
    tree.setResult(last);
    annotator.set(last);
  }
}

export function deactivate(): void {}
