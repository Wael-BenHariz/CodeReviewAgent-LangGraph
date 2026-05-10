# Repository Guidelines

## Project Structure & Module Organization

This repository contains a small Python backend for an AI code review service.
The FastAPI application, request model, LangGraph workflow, and agent logic live
in `app.py`. Runtime dependencies are pinned in `requirements.txt`.
`.github/workflows/code-review.yml` defines the pull request automation that
posts review output back to GitHub. `.env.example` documents expected local
configuration; keep real secrets only in `.env`.

The `codereview/` directory is a local virtual environment, not application
source. Avoid editing or importing code from it, and do not add generated files
such as `__pycache__/` to commits.

## Build, Test, and Development Commands

Create and activate an isolated environment before installing dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Run the API locally with reload enabled:

```powershell
uvicorn app:app --reload --host 127.0.0.1 --port 8000
```

Exercise the review endpoint with a JSON body containing `code`:

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8000/review -ContentType application/json -Body '{"code":"print(1)"}'
```

## Coding Style & Naming Conventions

Use Python 3.14-compatible syntax unless the project is downgraded explicitly.
Follow PEP 8: four-space indentation, `snake_case` for functions and variables,
`PascalCase` for classes and Pydantic models, and clear type annotations for
request/response state. Keep FastAPI route handlers thin; place reusable review
or graph behavior in methods or separate modules as the service grows.

## Testing Guidelines

No test suite is currently checked in. Add tests under `tests/` when changing
behavior, preferably with `pytest` and FastAPI's `TestClient`. Name files
`test_*.py` and cover route responses, graph state transitions, and failure
cases for missing or invalid model configuration. Once tests exist, run:

```powershell
pytest
```

## Commit & Pull Request Guidelines

The current history uses informal commit subjects. Keep new commits short,
imperative, and specific, for example `Add review endpoint validation`.
Pull requests should describe the behavior change, list manual or automated
tests run, link related issues, and mention any environment variables or
deployment settings that changed. For workflow updates, include the expected
GitHub Actions behavior and any required secret names.

## Security & Configuration Tips

Never commit `.env` or API keys. `app.py` reads `GEMINI_API_KEY`, while the
sample environment file currently documents Azure OpenAI variables; update
`.env.example` whenever configuration expectations change. Restrict CORS origins
to known frontend URLs before deploying beyond local development.
