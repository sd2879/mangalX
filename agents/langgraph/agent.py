from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import AIMessage, ToolMessage
import httpx
from typing import Any, Dict, AsyncIterable, Literal
from pydantic import BaseModel

memory = MemorySaver()

@tool
def get_exchange_rate(
    currency_from: str = "USD",
    currency_to: str = "EUR",
    currency_date: str = "latest",
):
    """Use this to get current exchange rate.

    Args:
        currency_from: The currency to convert from (e.g., "USD").
        currency_to: The currency to convert to (e.g., "EUR").
        currency_date: The date for the exchange rate or "latest". Defaults to "latest".

    Returns:
        A dictionary containing the exchange rate data, or an error message if the request fails.
    """    
    try:
        response = httpx.get(
            f"https://api.frankfurter.app/{currency_date}",
            params={"from": currency_from, "to": currency_to},
        )
        response.raise_for_status()

        data = response.json()
        if "rates" not in data:
            return {"error": "Invalid API response format."}
        return data
    except httpx.HTTPError as e:
        return {"error": f"API request failed: {e}"}
    except ValueError:
        return {"error": "Invalid JSON response from API."}

@tool
def calculate_math(
    expression: str
):
    """Use this to perform mathematical calculations.

    Args:
        expression: A string containing a mathematical expression (e.g., "2 + 3", "5 * 4").

    Returns:
        The result of the calculation as a string, or an error message if the expression is invalid.
    """
    try:
        # Basic safety check to prevent dangerous inputs
        allowed_chars = set("0123456789 +-*/(). ")
        if not all(c in allowed_chars for c in expression):
            return {"error": "Invalid characters in expression. Use numbers, +, -, *, /, (), and spaces only."}
        result = eval(expression, {"__builtins__": {}})
        return {"result": str(result)}
    except Exception as e:
        return {"error": f"Calculation failed: {str(e)}"}

class ResponseFormat(BaseModel):
    """Respond to the user in this format."""
    status: Literal["input_required", "completed", "error"] = "input_required"
    message: str

class CurrencyAgent:

    SYSTEM_INSTRUCTION = (
        "You are a specialized assistant for currency conversions and mathematical calculations. "
        "Your purpose is to: "
        "1. Use the 'get_exchange_rate' tool to answer questions about currency exchange rates. "
        "2. Use the 'calculate_math' tool to perform mathematical calculations when asked to compute expressions like '2 + 3' or '5 * 4'. "
        "If the user asks about anything other than currency conversion, exchange rates, or mathematical calculations, "
        "politely state that you cannot help with that topic and can only assist with currency-related queries or math. "
        "Do not attempt to answer unrelated questions or use tools for other purposes. "
        "Examples: "
        "- Query: 'How much is 1 USD to EUR?' -> Use get_exchange_rate. "
        "- Query: 'Calculate 2 + 3' -> Use calculate_math to return '5'. "
        "- Query: 'What's the weather?' -> Respond: 'I can only assist with currency conversions and mathematical calculations.' "
        "Set response status to input_required if the user needs to provide more information (e.g., missing currency or incomplete math expression). "
        "Set response status to error if there is an error while processing the request. "
        "Set response status to completed if the request is complete."
    )
     
    def __init__(self):
        self.model = ChatGoogleGenerativeAI(model="gemini-2.0-flash")
        self.tools = [get_exchange_rate, calculate_math]

        self.graph = create_react_agent(
            self.model, tools=self.tools, checkpointer=memory, prompt=self.SYSTEM_INSTRUCTION, response_format=ResponseFormat
        )

    def invoke(self, query, sessionId) -> str:
        config = {"configurable": {"thread_id": sessionId}}
        self.graph.invoke({"messages": [("user", query)]}, config)        
        return self.get_agent_response(config)

    async def stream(self, query, sessionId) -> AsyncIterable[Dict[str, Any]]:
        inputs = {"messages": [("user", query)]}
        config = {"configurable": {"thread_id": sessionId}}

        for item in self.graph.stream(inputs, config, stream_mode="values"):
            message = item["messages"][-1]
            if (
                isinstance(message, AIMessage)
                and message.tool_calls
                and len(message.tool_calls) > 0
            ):
                tool_name = message.tool_calls[0]["name"]
                if tool_name == "get_exchange_rate":
                    yield {
                        "is_task_complete": False,
                        "require_user_input": False,
                        "content": "Looking up the exchange rates...",
                    }
                elif tool_name == "calculate_math":
                    yield {
                        "is_task_complete": False,
                        "require_user_input": False,
                        "content": "Performing the calculation...",
                    }
            elif isinstance(message, ToolMessage):
                yield {
                    "is_task_complete": False,
                    "require_user_input": False,
                    "content": "Processing the results...",
                }            
        
        yield self.get_agent_response(config)

        
    def get_agent_response(self, config):
        current_state = self.graph.get_state(config)        
        structured_response = current_state.values.get('structured_response')
        if structured_response and isinstance(structured_response, ResponseFormat): 
            if structured_response.status == "input_required":
                return {
                    "is_task_complete": False,
                    "require_user_input": True,
                    "content": structured_response.message
                }
            elif structured_response.status == "error":
                return {
                    "is_task_complete": False,
                    "require_user_input": True,
                    "content": structured_response.message
                }
            elif structured_response.status == "completed":
                return {
                    "is_task_complete": True,
                    "require_user_input": False,
                    "content": structured_response.message
                }

        return {
            "is_task_complete": False,
            "require_user_input": True,
            "content": "We are unable to process your request at the moment. Please try again.",
        }

    SUPPORTED_CONTENT_TYPES = ["text", "text/plain"]