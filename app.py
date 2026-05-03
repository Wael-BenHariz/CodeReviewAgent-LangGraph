from typing import TypedDict, List, Dict
from langchain_google_genai import ChatGoogleGenerativeAI
import os
from dotenv import load_dotenv
from langgraph.graph import StateGraph, END
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


load_dotenv()

class CodeReviewRequest(BaseModel):
    code: str

class CodeReviewState(TypedDict):
    """State that goes through nodes of our graph"""
    code: str
    initial_analysis: str
    issues: List[str]
    final_report: str

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
        prompt = f"""Based on:{state["initial_analysis"]}
        Code: {state['code']}

        List 3-5 specific issues. Format each as "-issue".
"""
        
        response =self.llm.invoke(prompt)
        issues = [line.strip() for line in response.content.split('\n') if line.strip().startswith('-')]

        return {"issues": issues}
    
    def _generate_report(self, state: CodeReviewState) -> Dict:
        """Step3: Generate report from the review"""

        issues_text = '\n'.join(state['issues'])

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
    
agent = SimpleCodeReviewAgent()
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins =["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/review")
async def review_code(request: CodeReviewRequest):

    initial_state = {
        "code": request.code,
        "initial_analysis": "",
        "issues":[] ,
        "final_report": ""
    }

    result = agent.graph.invoke(initial_state)

    return {
        "analysis": result['initial_analysis'],
        "issues": result["issues"],
        "report": result["final_report"]
    }

