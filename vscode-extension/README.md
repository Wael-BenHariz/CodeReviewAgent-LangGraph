# Code Review Agent VS Code Extension

Reviews the active editor file with the local FastAPI backend and shows both a full report panel and inline diagnostics.

## Development

1. Start the backend from the repository root:

   ```powershell
   uvicorn app:app --reload --host 127.0.0.1 --port 8000
   ```

2. Install and compile the extension:

   ```powershell
   cd vscode-extension
   npm install
   npm run compile
   ```

3. Open `vscode-extension/` in VS Code and press `F5` to launch an Extension Development Host.

The backend URL defaults to `http://127.0.0.1:8000` and can be changed with the `codeReviewAgent.backendUrl` setting.
