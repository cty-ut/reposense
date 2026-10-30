// HTML shell for both webviews: loads the bundled React app (out/webview.js + out/webview.css).
import * as vscode from 'vscode';

export type ViewKind = 'map' | 'file';

export function webviewHtml(webview: vscode.Webview, extensionUri: vscode.Uri, view: ViewKind): string {
  const nonce = Array.from({ length: 32 }, () => Math.floor(Math.random() * 36).toString(36)).join('');
  const script = webview.asWebviewUri(vscode.Uri.joinPath(extensionUri, 'out', 'webview.js'));
  const style = webview.asWebviewUri(vscode.Uri.joinPath(extensionUri, 'out', 'webview.css'));
  return `<!DOCTYPE html>
<html lang="zh-CN"><head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src ${webview.cspSource} https: data:; style-src ${webview.cspSource} 'unsafe-inline'; script-src 'nonce-${nonce}';">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link rel="stylesheet" href="${style}">
</head><body>
<div id="root" data-view="${view}"></div>
<script nonce="${nonce}" src="${script}"></script>
</body></html>`;
}

export function webviewOptions(extensionUri: vscode.Uri): vscode.WebviewOptions {
  return { enableScripts: true, localResourceRoots: [vscode.Uri.joinPath(extensionUri, 'out')] };
}
