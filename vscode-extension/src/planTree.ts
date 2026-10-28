import * as vscode from 'vscode';
import { Result } from './types';

type Node = vscode.TreeItem & { children?: Node[] };

function item(label: string, opts: Partial<vscode.TreeItem> = {}, children?: Node[]): Node {
  const state = children?.length ? vscode.TreeItemCollapsibleState.Expanded : vscode.TreeItemCollapsibleState.None;
  const node = new vscode.TreeItem(label, state) as Node;
  Object.assign(node, opts);
  node.children = children;
  return node;
}

function section(label: string, icon: string, children: Node[]): Node[] {
  return children.length ? [item(label, { iconPath: new vscode.ThemeIcon(icon) }, children)] : [];
}

export class PlanTreeProvider implements vscode.TreeDataProvider<Node> {
  private readonly emitter = new vscode.EventEmitter<Node | undefined>();
  readonly onDidChangeTreeData = this.emitter.event;
  private result?: Result;
  private status?: string;

  constructor(private readonly root: () => vscode.Uri | undefined) {}

  setStatus(status: string | undefined): void {
    this.status = status;
    this.emitter.fire(undefined);
  }

  setResult(result: Result | undefined): void {
    this.result = result;
    this.status = undefined;
    this.emitter.fire(undefined);
  }

  getTreeItem(node: Node): vscode.TreeItem {
    return node;
  }

  getChildren(node?: Node): Node[] {
    if (node) {
      return node.children ?? [];
    }
    const nodes: Node[] = [];
    if (this.status) {
      nodes.push(item(this.status, { iconPath: new vscode.ThemeIcon('sync~spin') }));
    }
    const r = this.result;
    if (!r) {
      return nodes;
    }
    nodes.push(item(`#${r.issue.number} ${r.issue.title}`, {
      iconPath: new vscode.ThemeIcon('issues'),
      tooltip: r.issue.url,
      command: { command: 'vscode.open', title: 'Open issue', arguments: [vscode.Uri.parse(r.issue.url)] },
    }));
    if (!r.plan) {
      nodes.push(item(`No plan (${r.run.stop_reason})`, { iconPath: new vscode.ThemeIcon('warning') }));
      return nodes;
    }
    const p = r.plan;
    const root = this.root();

    nodes.push(...section('Changes', 'edit', p.changes.map((c) => item(`${c.file}:${c.start_line}`, {
      description: c.what,
      tooltip: new vscode.MarkdownString(`**${c.what}**\n\n${c.why}\n\n*confidence: ${c.confidence}*`),
      iconPath: new vscode.ThemeIcon(c.confidence === 'high' ? 'pass-filled' : c.confidence === 'medium' ? 'circle-large-outline' : 'question'),
      command: root ? {
        command: 'vscode.open', title: 'Open',
        arguments: [vscode.Uri.joinPath(root, c.file), { selection: new vscode.Range(c.start_line - 1, 0, c.end_line - 1, 0) }],
      } : undefined,
    }))));

    nodes.push(...section('Open questions', 'comment-discussion', (p.open_questions ?? []).map((q) =>
      item(q, { tooltip: q }))));

    nodes.push(...section('Related PRs', 'git-pull-request', (p.related_prs ?? []).map((pr) =>
      item(`#${pr.number} ${pr.title}`, {
        description: pr.merged_at ? `merged ${pr.merged_at}` : '',
        tooltip: new vscode.MarkdownString(
          `**${pr.title}** by @${pr.author}\n\n${pr.relevance}` +
          (pr.review_notes?.length ? `\n\n**Reviewer notes**\n${pr.review_notes.map((n) => `- ${n}`).join('\n')}` : '')),
        command: { command: 'vscode.open', title: 'Open PR', arguments: [vscode.Uri.parse(pr.url)] },
      }, (pr.review_notes ?? []).map((n) => item(n, { iconPath: new vscode.ThemeIcon('comment'), tooltip: n }))))));

    nodes.push(...section('Tests', 'beaker', (p.tests ?? []).map((t) =>
      item(t.command || t.file || 'test', { description: t.why, tooltip: t.why }))));

    nodes.push(...section('Conventions', 'law', (p.conventions ?? []).map((c) => item(c, { tooltip: c }))));

    nodes.push(...section('Suggested reviewers', 'person', (p.reviewers ?? []).map((rv) =>
      item(`@${rv.login}`, {
        description: rv.why, tooltip: rv.why,
        command: { command: 'vscode.open', title: 'Profile', arguments: [vscode.Uri.parse(`https://github.com/${rv.login}`)] },
      }))));
    return nodes;
  }
}
