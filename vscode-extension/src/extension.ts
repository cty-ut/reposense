// Composition root: creates the backend, editor decorators and features, and registers commands.
import * as vscode from 'vscode';
import { Backend } from './backend/api';
import { HistoryDecorator } from './editor/historyDecorations';
import { PlanDecorator } from './editor/planDecorations';
import { FileInsightsView } from './features/fileInsights';
import { Onboarding } from './features/onboarding';
import { RepoMapPanel } from './features/repoMap';

export async function activate(context: vscode.ExtensionContext): Promise<void> {
  const root = () => vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;

  const backend = new Backend(context.secrets, context.extensionPath);
  await backend.loadKeys();
  const history = new HistoryDecorator();
  const plans = new PlanDecorator();
  const onboarding = new Onboarding(context, backend);
  const file = new FileInsightsView(context.extensionUri, backend, history, plans, root);
  const map = new RepoMapPanel(context.extensionUri, backend, root, () => file.showTab('insight'));

  const needsKeys = async () => (await onboarding.ensureBackend()) && (backend.hasKeys() || onboarding.ensureKeys());

  context.subscriptions.push(
    backend, history, plans, map, file,
    vscode.window.registerWebviewViewProvider('reposense.file', file, { webviewOptions: { retainContextWhenHidden: true } }),
    vscode.window.onDidChangeVisibleTextEditors(() => {
      history.refreshAll();
      plans.refreshAll();
    }),

    vscode.commands.registerCommand('reposense.start', () => onboarding.start()),
    vscode.commands.registerCommand('reposense.openMap', async () => (await needsKeys()) && map.show()),
    vscode.commands.registerCommand('reposense.refreshMap', async () => (await needsKeys()) && map.show(true)),
    vscode.commands.registerCommand('reposense.setKeys', () => onboarding.ensureKeys(true)),
    vscode.commands.registerCommand('reposense.restartBackend', () => backend.restart()),
    vscode.commands.registerCommand('reposense.showCommit', (sha: string) => file.showCommit(sha)),
  );

  await onboarding.onActivate(() => map.show());
}

export function deactivate(): void {}
