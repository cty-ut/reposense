import * as vscode from 'vscode';
import { AgentEvent, Result } from './types';

/** Webview with the live agent trace, the plan, and the related-PR timeline. */
export class DetailsPanel {
  private panel?: vscode.WebviewPanel;
  private events: AgentEvent[] = [];

  constructor(private readonly onOpenFile: (file: string, line: number) => void) {}

  reset(): void {
    this.events = [];
    this.post({ type: 'reset' });
  }

  push(e: AgentEvent): void {
    this.events.push(e);
    this.post({ type: 'event', event: e });
  }

  showResult(result: Result): void {
    this.events = [{ type: 'result', result }];
    this.reveal();
    this.post({ type: 'reset' });
    this.post({ type: 'event', event: this.events[0] });
  }

  reveal(): void {
    if (this.panel) {
      this.panel.reveal(vscode.ViewColumn.Beside, true);
      return;
    }
    this.panel = vscode.window.createWebviewPanel('reposense.details', 'RepoSense', {
      viewColumn: vscode.ViewColumn.Beside, preserveFocus: true,
    }, { enableScripts: true, retainContextWhenHidden: true });
    this.panel.webview.html = html(nonce());
    this.panel.onDidDispose(() => (this.panel = undefined));
    this.panel.webview.onDidReceiveMessage((m) => {
      if (m.type === 'open' && typeof m.file === 'string') {
        this.onOpenFile(m.file, Number(m.line) || 1);
      } else if (m.type === 'link' && typeof m.url === 'string') {
        vscode.env.openExternal(vscode.Uri.parse(m.url));
      } else if (m.type === 'ready') {
        this.events.forEach((event) => this.post({ type: 'event', event }));
      }
    });
  }

  private post(msg: unknown): void {
    this.panel?.webview.postMessage(msg);
  }
}

function nonce(): string {
  return Array.from({ length: 24 }, () => Math.floor(Math.random() * 36).toString(36)).join('');
}

function html(n: string): string {
  return /* html */ `<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-${n}';">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
  :root { --muted: var(--vscode-descriptionForeground); --border: var(--vscode-panel-border); --accent: #e6aa00; }
  body { font-family: var(--vscode-font-family); font-size: 13px; color: var(--vscode-foreground); padding: 0 16px 32px; line-height: 1.5; }
  h1 { font-size: 16px; margin: 16px 0 4px; }
  h2 { font-size: 12px; text-transform: uppercase; letter-spacing: .06em; color: var(--muted); margin: 22px 0 8px; }
  a { color: var(--vscode-textLink-foreground); cursor: pointer; text-decoration: none; }
  a:hover { text-decoration: underline; }
  .muted { color: var(--muted); }
  .meta { color: var(--muted); font-size: 12px; }
  .card { border: 1px solid var(--border); border-radius: 6px; padding: 10px 12px; margin: 8px 0; }
  .change { border-left: 3px solid var(--accent); }
  .tag { font-size: 11px; padding: 1px 6px; border-radius: 10px; border: 1px solid var(--border); color: var(--muted); margin-left: 6px; }
  .q { border-left: 3px solid var(--vscode-editorWarning-foreground); }
  code { font-family: var(--vscode-editor-font-family); font-size: 12px; background: var(--vscode-textCodeBlock-background); padding: 1px 4px; border-radius: 3px; }
  ol.trace { list-style: none; padding: 0; margin: 0; font-family: var(--vscode-editor-font-family); font-size: 12px; }
  ol.trace li { padding: 3px 0; border-bottom: 1px dashed var(--border); }
  .tool { color: var(--vscode-symbolIcon-functionForeground, #4fc1ff); font-weight: 600; }
  .ok { color: var(--vscode-testing-iconPassed, #73c991); }
  .bad { color: var(--vscode-errorForeground); }
  .reason { color: var(--muted); }
  .timeline { position: relative; margin-left: 8px; padding-left: 18px; border-left: 2px solid var(--border); }
  .timeline .card { position: relative; }
  .timeline .card::before { content: ''; position: absolute; left: -25px; top: 14px; width: 10px; height: 10px; border-radius: 50%; background: var(--vscode-gitDecoration-addedResourceForeground, #73c991); }
  ul.notes { margin: 6px 0 0; padding-left: 18px; }
  details summary { cursor: pointer; color: var(--muted); }
  #status { margin-top: 8px; }
</style></head>
<body>
<div id="header"><h1 class="muted">RepoSense</h1><div class="meta">Run “RepoSense: Analyze Issue” to start.</div></div>
<div id="status"></div>
<div id="plan"></div>
<h2>Agent trace</h2>
<ol class="trace" id="trace"></ol>
<script nonce="${n}">
const vscode = acquireVsCodeApi();
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fileLink = (f, l) => '<a data-file="' + esc(f) + '" data-line="' + l + '"><code>' + esc(f) + ':' + l + '</code></a>';
const extLink = (url, text) => '<a data-url="' + esc(url) + '">' + esc(text) + '</a>';
const brief = (args) => Object.entries(args || {}).map(([k, v]) => k + '=' + JSON.stringify(v)).join(', ').slice(0, 140);

document.addEventListener('click', (e) => {
  const a = e.target.closest('a');
  if (!a) return;
  if (a.dataset.file) vscode.postMessage({ type: 'open', file: a.dataset.file, line: a.dataset.line });
  if (a.dataset.url) vscode.postMessage({ type: 'link', url: a.dataset.url });
});

function onEvent(e) {
  if (e.type === 'start') {
    $('header').innerHTML = '<h1>#' + e.issue.number + ' ' + esc(e.issue.title) + '</h1><div class="meta">' + esc(e.repo) +
      ' · ' + esc(e.model) + (e.use_history ? ' · with project history' : ' · code only') + ' · ' + extLink(e.issue.url, 'open issue') + '</div>';
    $('status').innerHTML = '<span class="meta">Exploring…</span>';
  } else if (e.type === 'tool_call') {
    const li = document.createElement('li');
    li.id = 'call-' + e.step + '-' + $('trace').children.length;
    li.innerHTML = '<span class="muted">' + e.step + '</span> <span class="tool">' + esc(e.name) + '</span>(' + esc(brief(e.args)) + ')' +
      (e.reason ? ' <span class="reason">— ' + esc(e.reason) + '</span>' : '') + '<div class="res muted">…</div>';
    $('trace').appendChild(li);
  } else if (e.type === 'tool_result') {
    const pending = [...document.querySelectorAll('#trace .res')].find((d) => d.textContent === '…');
    if (pending) { pending.className = 'res ' + (e.ok ? 'ok' : 'bad'); pending.textContent = '→ ' + e.summary; }
  } else if (e.type === 'plan_rejected') {
    const li = document.createElement('li');
    li.innerHTML = '<span class="bad">verifier rejected the plan:</span><ul class="notes">' + e.errors.map((x) => '<li>' + esc(x) + '</li>').join('') + '</ul>';
    $('trace').appendChild(li);
  } else if (e.type === 'error') {
    $('status').innerHTML = '<div class="card bad">' + esc(e.message) + '</div>';
  } else if (e.type === 'result') {
    renderResult(e.result);
  }
}

function renderResult(r) {
  if (!$('trace').children.length) {
    $('header').innerHTML = '<h1>#' + r.issue.number + ' ' + esc(r.issue.title) + '</h1><div class="meta">' + esc(r.repo) + ' · ' + extLink(r.issue.url, 'open issue') + '</div>';
  }
  const run = r.run;
  $('status').innerHTML = '<div class="meta">' + run.steps + ' rounds · ' + run.tool_calls + ' tool calls · ' + run.tokens.toLocaleString() +
    ' tokens · ' + run.seconds + 's · ' + esc(run.stop_reason) + (run.plan_rejections ? ' · verifier rejections: ' + run.plan_rejections : '') + '</div>';
  const p = r.plan;
  if (!p) { $('plan').innerHTML = '<div class="card bad">No plan produced (' + esc(run.stop_reason) + ').</div>'; return; }
  let h = '<h2>Summary</h2><div>' + esc(p.summary) + '</div>';
  if (p.open_questions?.length) {
    h += '<h2>Confirm with maintainers first</h2>' + p.open_questions.map((q) => '<div class="card q">' + esc(q) + '</div>').join('');
  }
  h += '<h2>Where to change</h2>' + p.changes.map((c) =>
    '<div class="card change"><div>' + fileLink(c.file, c.start_line) + '<span class="tag">' + esc(c.confidence) + '</span></div>' +
    '<div><b>' + esc(c.what) + '</b></div><div class="muted">' + esc(c.why) + '</div></div>').join('');
  const prs = [...(p.related_prs || [])].sort((a, b) => (b.merged_at || '').localeCompare(a.merged_at || ''));
  if (prs.length) {
    h += '<h2>What changed here before</h2><div class="timeline">' + prs.map((pr) =>
      '<div class="card"><div>' + extLink(pr.url, '#' + pr.number + ' ' + pr.title) + '</div><div class="meta">@' + esc(pr.author) +
      (pr.merged_at ? ' · merged ' + esc(pr.merged_at) : '') + '</div><div>' + esc(pr.relevance) + '</div>' +
      (pr.review_notes?.length ? '<ul class="notes">' + pr.review_notes.map((n) => '<li>' + esc(n) + '</li>').join('') + '</ul>' : '') +
      '</div>').join('') + '</div>';
  }
  if (p.tests?.length) h += '<h2>Tests</h2>' + p.tests.map((t) => '<div class="card">' + (t.command ? '<code>' + esc(t.command) + '</code> ' : '') +
    (t.file ? fileLink(t.file, 1) + ' ' : '') + '<div class="muted">' + esc(t.why) + '</div></div>').join('');
  if (p.conventions?.length) h += '<h2>Project conventions</h2><ul>' + p.conventions.map((c) => '<li>' + esc(c) + '</li>').join('') + '</ul>';
  if (p.reviewers?.length) h += '<h2>Suggested reviewers</h2>' + p.reviewers.map((rv) =>
    '<div>' + extLink('https://github.com/' + rv.login, '@' + rv.login) + ' <span class="muted">— ' + esc(rv.why) + '</span></div>').join('');
  if (p.dropped?.length) h += '<h2>Removed by verifier</h2><details><summary>' + p.dropped.length + ' unverified item(s)</summary><ul>' +
    p.dropped.map((d) => '<li>' + esc(d) + '</li>').join('') + '</ul></details>';
  $('plan').innerHTML = h;
}

window.addEventListener('message', (ev) => {
  const m = ev.data;
  if (m.type === 'reset') { $('trace').innerHTML = ''; $('plan').innerHTML = ''; $('status').innerHTML = ''; }
  if (m.type === 'event') onEvent(m.event);
});
vscode.postMessage({ type: 'ready' });
</script></body></html>`;
}
