import * as vscode from 'vscode';

type ReviewSeverity = 'info' | 'warning' | 'error';

interface ReviewDiagnostic {
  line: number;
  column?: number | null;
  severity: ReviewSeverity;
  message: string;
}

interface ReviewResponse {
  analysis: string;
  issues: string[];
  report: string;
  diagnostics: ReviewDiagnostic[];
}

let reportPanel: vscode.WebviewPanel | undefined;

export function activate(context: vscode.ExtensionContext) {
  const diagnosticCollection = vscode.languages.createDiagnosticCollection('codeReviewAgent');

  const reviewCommand = vscode.commands.registerCommand('codeReviewAgent.reviewCurrentFile', async () => {
    await reviewCurrentFile(context, diagnosticCollection);
  });

  context.subscriptions.push(diagnosticCollection, reviewCommand);
}

export function deactivate() {
  reportPanel?.dispose();
}

async function reviewCurrentFile(
  context: vscode.ExtensionContext,
  diagnosticCollection: vscode.DiagnosticCollection
) {
  const editor = vscode.window.activeTextEditor;
  if (!editor) {
    vscode.window.showErrorMessage('Open a file before running Code Review Agent.');
    return;
  }

  const document = editor.document;
  if (document.isUntitled) {
    vscode.window.showErrorMessage('Save the file before running Code Review Agent.');
    return;
  }

  const backendUrl = getBackendUrl();
  showReportPanel(context, 'Reviewing...', 'Running AI code review.', []);
  diagnosticCollection.delete(document.uri);

  try {
    const response = await vscode.window.withProgress(
      {
        location: vscode.ProgressLocation.Notification,
        title: 'Code Review Agent',
        cancellable: false
      },
      async () => requestReview(backendUrl, document.getText())
    );

    diagnosticCollection.set(document.uri, toVsCodeDiagnostics(document, response.diagnostics));
    showReportPanel(context, response.report, response.analysis, response.issues);
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Review failed.';
    showReportPanel(context, 'Review failed', message, []);
    vscode.window.showErrorMessage(`Code Review Agent: ${message}`);
  }
}

function getBackendUrl(): string {
  const configuredUrl = vscode.workspace
    .getConfiguration('codeReviewAgent')
    .get<string>('backendUrl', 'http://127.0.0.1:8000');

  return configuredUrl.replace(/\/+$/, '');
}

async function requestReview(backendUrl: string, code: string): Promise<ReviewResponse> {
  const response = await fetch(`${backendUrl}/review`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({ code })
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`Backend returned ${response.status}: ${errorText || response.statusText}`);
  }

  return await response.json() as ReviewResponse;
}

function toVsCodeDiagnostics(
  document: vscode.TextDocument,
  reviewDiagnostics: ReviewDiagnostic[]
): vscode.Diagnostic[] {
  return reviewDiagnostics.map((reviewDiagnostic) => {
    const lineIndex = clamp(reviewDiagnostic.line - 1, 0, Math.max(document.lineCount - 1, 0));
    const line = document.lineAt(lineIndex);
    const startColumn = clamp((reviewDiagnostic.column ?? 1) - 1, 0, line.text.length);
    const endColumn = line.text.length === 0
      ? 0
      : clamp(startColumn + 1, startColumn, line.text.length);
    const range = new vscode.Range(lineIndex, startColumn, lineIndex, endColumn);
    const diagnostic = new vscode.Diagnostic(
      range,
      reviewDiagnostic.message,
      toDiagnosticSeverity(reviewDiagnostic.severity)
    );

    diagnostic.source = 'Code Review Agent';
    return diagnostic;
  });
}

function toDiagnosticSeverity(severity: ReviewSeverity): vscode.DiagnosticSeverity {
  switch (severity) {
    case 'error':
      return vscode.DiagnosticSeverity.Error;
    case 'info':
      return vscode.DiagnosticSeverity.Information;
    case 'warning':
    default:
      return vscode.DiagnosticSeverity.Warning;
  }
}

function showReportPanel(
  context: vscode.ExtensionContext,
  report: string,
  analysis: string,
  issues: string[]
) {
  if (!reportPanel) {
    reportPanel = vscode.window.createWebviewPanel(
      'codeReviewAgentReport',
      'Code Review',
      vscode.ViewColumn.Beside,
      {
        enableScripts: false,
        localResourceRoots: [context.extensionUri]
      }
    );

    reportPanel.onDidDispose(() => {
      reportPanel = undefined;
    });
  }

  reportPanel.webview.html = renderReportHtml(report, analysis, issues);
  reportPanel.reveal(vscode.ViewColumn.Beside);
}

function renderReportHtml(report: string, analysis: string, issues: string[]): string {
  const issueItems = issues.length
    ? issues.map((issue) => `<li>${escapeHtml(issue)}</li>`).join('')
    : '<li>No specific issues returned.</li>';

  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <style>
    :root {
      --bg: #171717;
      --panel: #202020;
      --line: #3a332b;
      --text: #f3eee5;
      --muted: #b8ada0;
      --accent: #e0a84b;
      --accent-2: #6bb6a8;
      --danger: #ee6f68;
    }

    body {
      margin: 0;
      color: var(--text);
      background:
        linear-gradient(135deg, rgba(224, 168, 75, 0.08), transparent 32%),
        linear-gradient(315deg, rgba(107, 182, 168, 0.07), transparent 28%),
        var(--bg);
      font-family: "Aptos", "Segoe UI", sans-serif;
      font-size: 14px;
      line-height: 1.55;
    }

    main {
      max-width: 860px;
      margin: 0 auto;
      padding: 22px;
    }

    header {
      border-bottom: 1px solid var(--line);
      margin-bottom: 18px;
      padding-bottom: 16px;
    }

    h1 {
      margin: 0;
      font-family: "Georgia", serif;
      font-size: 28px;
      font-weight: 600;
      letter-spacing: 0;
    }

    .subtitle {
      color: var(--muted);
      margin: 6px 0 0;
    }

    section {
      margin: 18px 0;
    }

    h2 {
      color: var(--accent);
      font-size: 13px;
      letter-spacing: 0;
      margin: 0 0 8px;
      text-transform: uppercase;
    }

    .summary,
    .issues,
    .report {
      background: rgba(32, 32, 32, 0.86);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px 16px;
    }

    ul {
      margin: 0;
      padding-left: 20px;
    }

    li {
      margin: 7px 0;
    }

    pre {
      margin: 0;
      white-space: pre-wrap;
      word-break: break-word;
      font-family: "Cascadia Code", "Consolas", monospace;
      font-size: 13px;
    }

    .status {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      color: var(--accent-2);
      font-size: 12px;
      margin-top: 12px;
    }

    .status::before {
      background: var(--accent-2);
      border-radius: 50%;
      content: "";
      height: 8px;
      width: 8px;
    }
  </style>
</head>
<body>
  <main>
    <header>
      <h1>Code Review</h1>
      <p class="subtitle">AI review from the local backend.</p>
      <span class="status">${issues.length} issue${issues.length === 1 ? '' : 's'} returned</span>
    </header>

    <section>
      <h2>Analysis</h2>
      <div class="summary">${escapeHtml(analysis)}</div>
    </section>

    <section>
      <h2>Issues</h2>
      <div class="issues"><ul>${issueItems}</ul></div>
    </section>

    <section>
      <h2>Full Report</h2>
      <div class="report"><pre>${escapeHtml(report)}</pre></div>
    </section>
  </main>
</body>
</html>`;
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}
