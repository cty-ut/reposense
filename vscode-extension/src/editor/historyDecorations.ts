// Colors each line of a file by the recent commit that last changed it, and shows a hover card.
import * as vscode from 'vscode';
import { COMMIT_COLORS, withAlpha } from '../shared/palette';
import { FileCommit, FileHistory } from '../shared/protocol';

function makeTypes(bg: number, border: string) {
  return COMMIT_COLORS.map((c) => vscode.window.createTextEditorDecorationType({
    isWholeLine: true,
    backgroundColor: withAlpha(c, bg),
    borderWidth: `0 0 0 ${border}`,
    borderStyle: 'solid',
    borderColor: c,
    overviewRulerColor: c,
    overviewRulerLane: vscode.OverviewRulerLane.Left,
  }));
}

export class HistoryDecorator implements vscode.Disposable {
  private readonly normal = makeTypes(0.07, '2px');
  private readonly focused = makeTypes(0.22, '4px');
  private readonly byFile = new Map<string, FileHistory>(); // key: absolute file path
  private focus: { file: string; sha: string } | null = null;
  private readonly hover: vscode.Disposable;

  constructor() {
    this.hover = vscode.languages.registerHoverProvider({ scheme: 'file' }, {
      provideHover: (doc, pos) => this.hoverFor(doc, pos.line + 1),
    });
  }

  set(file: vscode.Uri, history: FileHistory | undefined): void {
    if (history) this.byFile.set(file.fsPath, history);
    else this.byFile.delete(file.fsPath);
    this.refreshAll();
  }

  get(file: vscode.Uri): FileHistory | undefined {
    return this.byFile.get(file.fsPath);
  }

  setFocus(file: vscode.Uri, sha: string | null): void {
    this.focus = sha ? { file: file.fsPath, sha } : null;
    this.refreshAll();
  }

  commitAt(file: vscode.Uri, line: number): FileCommit | undefined {
    return this.byFile.get(file.fsPath)?.commits.find((c) => c.ranges.some(([s, e]) => s <= line && line <= e));
  }

  refreshAll(): void {
    vscode.window.visibleTextEditors.forEach((e) => this.apply(e));
  }

  apply(editor: vscode.TextEditor): void {
    const history = this.byFile.get(editor.document.uri.fsPath);
    const focusSha = this.focus?.file === editor.document.uri.fsPath ? this.focus.sha : null;
    COMMIT_COLORS.forEach((_, i) => {
      const ranges = (commit?: FileCommit) => (commit?.ranges ?? []).map(([s, e]) =>
        new vscode.Range(s - 1, 0, Math.min(e, editor.document.lineCount) - 1, 0));
      const commitsForColor = history?.commits.filter((c) => c.index % COMMIT_COLORS.length === i) ?? [];
      const normal = focusSha ? [] : commitsForColor.flatMap((c) => ranges(c));
      const strong = focusSha ? commitsForColor.filter((c) => c.sha === focusSha).flatMap((c) => ranges(c)) : [];
      editor.setDecorations(this.normal[i], normal);
      editor.setDecorations(this.focused[i], strong);
    });
  }

  private hoverFor(doc: vscode.TextDocument, line: number): vscode.Hover | undefined {
    const c = this.commitAt(doc.uri, line);
    if (!c) return undefined;
    const md = new vscode.MarkdownString(undefined, true);
    md.isTrusted = { enabledCommands: ['reposense.showCommit'] };
    const color = COMMIT_COLORS[c.index % COMMIT_COLORS.length];
    md.appendMarkdown(`<span style="color:${color};">●</span> **${escape(c.subject)}**\n\n`);
    md.supportHtml = true;
    md.appendMarkdown(`${escape(c.author)} · ${c.date} · <span style="color:#3fb950;">+${c.additions}</span> `
      + `<span style="color:#f85149;">−${c.deletions}</span>`);
    if (c.pr) md.appendMarkdown(` · [PR #${c.pr.number}](${c.pr.url})`);
    md.appendMarkdown(`\n\n第 ${c.index + 1} 近的改动，当前文件中有 ${c.owned_lines} 行来自这次提交。\n\n`);
    const args = encodeURIComponent(JSON.stringify([c.sha]));
    md.appendMarkdown(`[$(git-commit) 查看详情](command:reposense.showCommit?${args}) · [在 GitHub 打开](${c.url})`);
    return new vscode.Hover(md);
  }

  dispose(): void {
    [...this.normal, ...this.focused].forEach((t) => t.dispose());
    this.hover.dispose();
  }
}

function escape(s: string): string {
  return s.replace(/[\\`*_{}[\]()#+\-.!|<>]/g, '\\$&');
}
