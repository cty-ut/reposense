import * as vscode from 'vscode';
import { BlameBlock, Change, RelatedPr, Result } from './types';

const suggestionType = vscode.window.createTextEditorDecorationType({
  isWholeLine: true,
  backgroundColor: 'rgba(255, 196, 0, 0.10)',
  borderWidth: '0 0 0 3px',
  borderStyle: 'solid',
  borderColor: 'rgba(255, 196, 0, 0.85)',
  overviewRulerColor: 'rgba(255, 196, 0, 0.85)',
  overviewRulerLane: vscode.OverviewRulerLane.Left,
});

const historyType = vscode.window.createTextEditorDecorationType({
  after: { color: new vscode.ThemeColor('editorCodeLens.foreground'), fontStyle: 'italic', margin: '0 0 0 2em' },
});

const suggestionLabelType = vscode.window.createTextEditorDecorationType({
  after: { color: 'rgba(230, 170, 0, 0.95)', margin: '0 0 0 2em' },
});

const norm = (s: string) => s.replace(/\s+/g, ' ').trim();

/** Line offset between the analyzed commit and the open document, found via the change's anchor text. */
function lineShift(doc: vscode.TextDocument, c: Change): number | undefined {
  const anchor = norm(c.anchor);
  for (let d = 0; d <= 60; d++) {
    for (const delta of d === 0 ? [0] : [d, -d]) {
      for (let ln = c.start_line + delta; ln <= c.end_line + delta; ln++) {
        if (ln >= 1 && ln <= doc.lineCount && norm(doc.lineAt(ln - 1).text).includes(anchor)) {
          return delta;
        }
      }
    }
  }
  return undefined;
}

function prHover(block: BlameBlock, pr: RelatedPr | undefined, repo: string): vscode.MarkdownString {
  const md = new vscode.MarkdownString(undefined, true);
  md.isTrusted = true;
  const prLink = block.pr ? `[PR #${block.pr}](${block.pr_url ?? `https://github.com/${repo}/pull/${block.pr}`})` : '';
  md.appendMarkdown(`$(git-commit) **Last changed** ${block.date ?? ''} by **${block.author ?? '?'}** ${prLink}\n\n`);
  md.appendMarkdown(`${block.pr_title ?? block.summary ?? ''}\n\n`);
  md.appendMarkdown(`[\`${block.sha}\`](https://github.com/${repo}/commit/${block.sha})`);
  if (pr) {
    md.appendMarkdown(`\n\n---\n**Why it matters:** ${pr.relevance}`);
    if (pr.review_notes?.length) {
      md.appendMarkdown(`\n\n**Reviewer feedback in #${pr.number}:**\n${pr.review_notes.map((n) => `- ${n}`).join('\n')}`);
    }
  }
  return md;
}

export class Annotator {
  private result?: Result;

  constructor(private readonly root: () => vscode.Uri | undefined) {}

  set(result: Result | undefined): void {
    this.result = result;
    this.refreshAll();
  }

  refreshAll(): void {
    vscode.window.visibleTextEditors.forEach((e) => this.refresh(e));
  }

  refresh(editor: vscode.TextEditor): void {
    const root = this.root();
    const r = this.result;
    const empty = () => [suggestionType, historyType, suggestionLabelType].forEach((t) => editor.setDecorations(t, []));
    if (!r?.plan || !root) {
      return empty();
    }
    const rel = vscode.workspace.asRelativePath(editor.document.uri, false).replace(/\\/g, '/');
    const changes = r.plan.changes.filter((c) => c.file === rel);
    const blocks = r.history[rel] ?? [];
    if (!changes.length && !blocks.length) {
      return empty();
    }
    const doc = editor.document;
    const prs = new Map((r.plan.related_prs ?? []).map((p) => [p.number, p]));
    const shift = changes.length ? lineShift(doc, changes[0]) ?? 0 : 0;
    const line = (n: number) => Math.min(Math.max(n + shift, 1), doc.lineCount) - 1;

    const suggestions: vscode.DecorationOptions[] = [];
    const labels: vscode.DecorationOptions[] = [];
    for (const c of changes) {
      const hover = new vscode.MarkdownString(undefined, true);
      hover.appendMarkdown(`$(lightbulb) **RepoSense suggestion** · confidence: ${c.confidence}\n\n**${c.what}**\n\n${c.why}`);
      suggestions.push({ range: new vscode.Range(line(c.start_line), 0, line(c.end_line), 0), hoverMessage: hover });
      const first = line(c.start_line);
      labels.push({
        range: new vscode.Range(first, doc.lineAt(first).text.length, first, doc.lineAt(first).text.length),
        renderOptions: { after: { contentText: `💡 ${c.what.length > 70 ? c.what.slice(0, 70) + '…' : c.what}` } },
      });
    }

    const history: vscode.DecorationOptions[] = blocks.map((b) => {
      const first = line(b.start);
      const end = doc.lineAt(first).text.length;
      const who = `${b.author ?? '?'}, ${b.date ?? ''}`;
      const text = b.pr ? `PR #${b.pr} · ${who}` : `${b.sha} · ${who}`;
      return {
        range: new vscode.Range(first, end, first, end),
        hoverMessage: prHover(b, b.pr ? prs.get(b.pr) : undefined, r.repo),
        renderOptions: { after: { contentText: text } },
      };
    });

    editor.setDecorations(suggestionType, suggestions);
    editor.setDecorations(suggestionLabelType, labels);
    editor.setDecorations(historyType, history);
  }
}
