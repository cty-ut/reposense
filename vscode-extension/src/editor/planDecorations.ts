// Marks the lines a contribution plan (or a suggestion) proposes to change.
import * as vscode from 'vscode';
import { PlanChange } from '../shared/protocol';

const highlight = vscode.window.createTextEditorDecorationType({
  isWholeLine: true,
  backgroundColor: 'rgba(255, 196, 0, 0.12)',
  borderWidth: '1px 0 1px 0',
  borderStyle: 'dashed',
  borderColor: 'rgba(255, 196, 0, 0.7)',
  overviewRulerColor: 'rgba(255, 196, 0, 0.9)',
  overviewRulerLane: vscode.OverviewRulerLane.Right,
});

const label = vscode.window.createTextEditorDecorationType({
  after: { color: 'rgba(230, 170, 0, 0.95)', margin: '0 0 0 2em' },
});

export class PlanDecorator implements vscode.Disposable {
  private changes: PlanChange[] = [];

  set(changes: PlanChange[]): void {
    this.changes = changes;
    this.refreshAll();
  }

  refreshAll(): void {
    vscode.window.visibleTextEditors.forEach((e) => this.apply(e));
  }

  apply(editor: vscode.TextEditor): void {
    const rel = vscode.workspace.asRelativePath(editor.document.uri, false).replace(/\\/g, '/');
    const mine = this.changes.filter((c) => c.file === rel);
    const doc = editor.document;
    const clamp = (n: number) => Math.min(Math.max(n, 1), doc.lineCount) - 1;
    editor.setDecorations(highlight, mine.map((c) => ({
      range: new vscode.Range(clamp(c.start_line), 0, clamp(c.end_line), 0),
      hoverMessage: new vscode.MarkdownString(`$(lightbulb) **贡献计划建议**（置信度 ${c.confidence}）\n\n**${c.what}**\n\n${c.why}`, true),
    })));
    editor.setDecorations(label, mine.map((c) => {
      const line = clamp(c.start_line);
      const end = doc.lineAt(line).text.length;
      const text = c.what.length > 60 ? c.what.slice(0, 60) + '…' : c.what;
      return { range: new vscode.Range(line, end, line, end), renderOptions: { after: { contentText: `💡 ${text}` } } };
    }));
  }

  dispose(): void {
    highlight.dispose();
    label.dispose();
  }
}
