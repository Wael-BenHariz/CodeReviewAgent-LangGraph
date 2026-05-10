from typing import TypedDict, List, Dict, Any, Literal
from langchain_google_genai import ChatGoogleGenerativeAI
import os
import hmac
import hashlib
import json
from dotenv import load_dotenv
from langgraph.graph import StateGraph, END
import httpx
from fastapi import FastAPI, Request, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


load_dotenv()

GITHUB_API_BASE = "https://api.github.com"
REVIEW_COMMENT_MARKER = "<!-- ai-code-review-agent -->"
SUPPORTED_PULL_REQUEST_ACTIONS = {"opened", "synchronize", "reopened"}

class CodeReviewRequest(BaseModel):
    code: str

class CodeDiagnostic(BaseModel):
    line: int
    column: int | None = None
    severity: Literal["info", "warning", "error"] = "warning"
    message: str

class CodeReviewResponse(BaseModel):
    analysis: str
    issues: List[str]
    report: str
    diagnostics: List[CodeDiagnostic]

class CodeReviewState(TypedDict):
    """State that goes through nodes of our graph"""
    code: str
    initial_analysis: str
    issues: List[str]
    final_report: str
    diagnostics: List[Dict[str, Any]]

def extract_json_object(text: str) -> Dict[str, Any] | None:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None

    try:
        parsed = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None

    return parsed if isinstance(parsed, dict) else None

def normalize_diagnostics(raw_diagnostics: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw_diagnostics, list):
        return []

    diagnostics: List[Dict[str, Any]] = []
    for raw in raw_diagnostics:
        if not isinstance(raw, dict):
            continue

        try:
            line = int(raw.get("line", 0))
        except (TypeError, ValueError):
            continue

        if line < 1:
            continue

        column = raw.get("column")
        try:
            normalized_column = int(column) if column is not None else None
        except (TypeError, ValueError):
            normalized_column = None

        severity = str(raw.get("severity", "warning")).lower()
        if severity not in {"info", "warning", "error"}:
            severity = "warning"

        message = str(raw.get("message", "")).strip()
        if not message:
            continue

        diagnostics.append({
            "line": line,
            "column": normalized_column,
            "severity": severity,
            "message": message
        })

    return diagnostics

class SimpleCodeReviewAgent:
    def __init__(self):
        self.llm = ChatGoogleGenerativeAI(
            model="gemini-2.5-flash",  
            google_api_key=os.getenv("GEMINI_API_KEY"),
            temperature=0.3
        )

        self.graph = self._build_graph()

    def _analysis_agent(self, state: CodeReviewState) -> Dict:
        """Step1: Analyse the code"""
        prompt = f"""Analyse the code briefly:
            {state['code']}
        Focus on: purpose, structure and concerns.  
"""
        response = self.llm.invoke(prompt)
        return {"initial_analysis": response.content}
    
    def _find_issues(self, state: CodeReviewState) -> Dict:
        """Step2 : Find the issues in code"""
        prompt = f"""Based on this analysis:
{state["initial_analysis"]}

Review this code and return only JSON:
{state['code']}

Use this exact shape:
{{
  "issues": ["short issue summary"],
  "diagnostics": [
    {{
      "line": 1,
      "column": 1,
      "severity": "info|warning|error",
      "message": "specific issue message"
    }}
  ]
}}

Find 3-5 specific issues when possible. Use 1-based line and column numbers.
"""
        
        response =self.llm.invoke(prompt)
        parsed = extract_json_object(response.content)
        if parsed:
            issues = [
                str(issue).strip()
                for issue in parsed.get("issues", [])
                if str(issue).strip()
            ]
            diagnostics = normalize_diagnostics(parsed.get("diagnostics", []))
        else:
            issues = [
                line.strip().lstrip("-").strip()
                for line in response.content.split('\n')
                if line.strip().startswith('-')
            ]
            diagnostics = []

        return {"issues": issues, "diagnostics": diagnostics}
    
    def _generate_report(self, state: CodeReviewState) -> Dict:
        """Step3: Generate report from the review"""

        prompt = f"""Create a code review report:
        
        Analysis: {state['initial_analysis']}
        Issues: {state['issues']}

        Format Summary, Issues, and Recommendation.
"""
        
        response = self.llm.invoke(prompt)

        return {"final_report": response.content}
    
    def _build_graph(self) -> StateGraph:
        """Build the langgraph workflow"""

        workflow = StateGraph(CodeReviewState)

        #Add nodes 
        workflow.add_node("analyzer", self._analysis_agent)
        workflow.add_node("issue_finder", self._find_issues)
        workflow.add_node("report_generator", self._generate_report)

        # Add edges 
        workflow.set_entry_point("analyzer")
        workflow.add_edge("analyzer", "issue_finder")
        workflow.add_edge("issue_finder", "report_generator")
        workflow.add_edge("report_generator", END)

        return workflow.compile()

agent: SimpleCodeReviewAgent | None = None

def get_agent() -> SimpleCodeReviewAgent:
    global agent
    if agent is None:
        agent = SimpleCodeReviewAgent()
    return agent

def run_review(code: str) -> Dict[str, Any]:
    initial_state = {
        "code": code,
        "initial_analysis": "",
        "issues": [],
        "final_report": "",
        "diagnostics": []
    }

    result = get_agent().graph.invoke(initial_state)

    return {
        "analysis": result["initial_analysis"],
        "issues": result["issues"],
        "report": result["final_report"],
        "diagnostics": normalize_diagnostics(result.get("diagnostics", []))
    }

def verify_github_signature(body: bytes, signature: str | None) -> bool:
    secret = os.getenv("GITHUB_WEBHOOK_SECRET")
    if not secret or not signature:
        return False

    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)

def github_headers(accept: str = "application/vnd.github+json") -> Dict[str, str]:
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        raise HTTPException(status_code=500, detail="GITHUB_TOKEN is not configured")

    return {
        "Accept": accept,
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "code-review-agent"
    }

async def fetch_pull_request_diff(
    client: httpx.AsyncClient,
    owner: str,
    repo: str,
    pull_number: int
) -> str:
    response = await client.get(
        f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{pull_number}",
        headers=github_headers("application/vnd.github.diff")
    )
    response.raise_for_status()
    return response.text

async def upsert_review_comment(
    client: httpx.AsyncClient,
    owner: str,
    repo: str,
    pull_number: int,
    report: str
) -> Dict[str, Any]:
    comments_url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/issues/{pull_number}/comments"
    body = f"{REVIEW_COMMENT_MARKER}\n## AI Code Review\n\n{report}"

    comments_response = await client.get(comments_url, headers=github_headers())
    comments_response.raise_for_status()

    for comment in comments_response.json():
        if REVIEW_COMMENT_MARKER in comment.get("body", ""):
            update_response = await client.patch(
                comment["url"],
                headers=github_headers(),
                json={"body": body}
            )
            update_response.raise_for_status()
            return {"action": "updated", "comment_id": update_response.json().get("id")}

    create_response = await client.post(
        comments_url,
        headers=github_headers(),
        json={"body": body}
    )
    create_response.raise_for_status()
    return {"action": "created", "comment_id": create_response.json().get("id")}

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins =["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/review", response_model=CodeReviewResponse)
async def review_code(request: CodeReviewRequest):
    return run_review(request.code)

@app.post("/github/webhook")
async def github_webhook(
    request: Request,
    x_github_event: str | None = Header(default=None),
    x_hub_signature_256: str | None = Header(default=None)
):
    body = await request.body()
    if not verify_github_signature(body, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="Invalid GitHub webhook signature")

    if x_github_event != "pull_request":
        return {"status": "ignored", "reason": "unsupported event"}

    payload = await request.json()
    action = payload.get("action")
    pull_request = payload.get("pull_request") or {}
    repository = payload.get("repository") or {}

    if action not in SUPPORTED_PULL_REQUEST_ACTIONS:
        return {"status": "ignored", "reason": "unsupported action"}

    if pull_request.get("state") != "open":
        return {"status": "ignored", "reason": "pull request is not open"}

    owner = repository.get("owner", {}).get("login")
    repo = repository.get("name")
    pull_number = pull_request.get("number")
    if not owner or not repo or not pull_number:
        raise HTTPException(status_code=400, detail="Missing pull request repository data")

    async with httpx.AsyncClient(timeout=30.0) as client:
        diff = await fetch_pull_request_diff(client, owner, repo, pull_number)
        review = run_review(diff)
        comment = await upsert_review_comment(
            client,
            owner,
            repo,
            pull_number,
            review["report"]
        )

    return {"status": "reviewed", "comment": comment}

