# File: samples/python/agents/langgraph/brave_search.py

import requests
from langchain.agents import AgentExecutor, create_react_agent
from langchain.prompts import PromptTemplate
from langchain_core.tools import Tool
from langchain_google_genai import ChatGoogleGenerativeAI
from common.types import TaskResponse, TaskRequest

class BraveSearchAgent:
    SUPPORTED_CONTENT_TYPES = ["text"]

    def __init__(self):
        self.llm = ChatGoogleGenerativeAI(model="gemini-1.5-pro", google_api_key=os.getenv("GOOGLE_API_KEY"), temperature=0.3)
        self.tools = [
            Tool(
                name="brave_search",
                func=self._search,
                description="Search the web for information using Brave Search API."
            )
        ]
        prompt = PromptTemplate.from_template("""
        You are a helpful search assistant. Answer queries using the brave_search tool when needed. If the query is incomplete, ask for clarification.
        Query: {input}
        {agent_scratchpad}
        """)
        agent = create_react_agent(self.llm, self.tools, prompt)
        self.executor = AgentExecutor(agent=agent, tools=self.tools, verbose=True)

    def _search(self, query: str) -> str:
        try:
            url = "https://api.search.brave.com/res/v1/web/search"
            headers = {"Accept": "application/json", "X-Subscription-Token": os.getenv("BRAVE_SEARCH_API_KEY")}
            params = {"q": query, "count": 3, "search_lang": "en"}
            response = requests.get(url, headers=headers, params=params)
            response.raise_for_status()
            data = response.json()
            results = []
            if "web" in data and "results" in data["web"]:
                for i, result in enumerate(data["web"]["results"], 1):
                    title = result.get("title", "No title")
                    description = result.get("description", "No description")
                    url = result.get("url", "No URL")
                    results.append(f"{i}. {title}\nURL: {url}\nDescription: {description}\n")
            return f"Search results for '{query}':\n\n" + "\n".join(results) if results else f"No results for '{query}'."
        except Exception as e:
            return f"Error searching for '{query}': {str(e)}"

    async def process_task(self, request: TaskRequest) -> TaskResponse:
        message = request.message.parts[0].text
        try:
            if not message.startswith("search for "):
                return TaskResponse(
                    id=request.id,
                    status={"state": "input-required", "message": {"role": "agent", "parts": [{"type": "text", "text": "Please specify a search query starting with 'search for'."}]}}
                )
            query = message.replace("search for ", "").strip()
            result = await asyncio.to_thread(self.executor.invoke, {"input": query})
            output = result["output"]
            return TaskResponse(
                id=request.id,
                status={"state": "completed"},
                artifacts=[{"parts": [{"type": "text", "text": output}], "index": 0}]
            )
        except Exception as e:
            return TaskResponse(
                id=request.id,
                status={"state": "error", "message": {"role": "agent", "parts": [{"type": "text", "text": f"Error: {str(e)}"}]}}
            )