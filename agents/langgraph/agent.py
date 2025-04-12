from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import AIMessage, ToolMessage, HumanMessage
import httpx, traceback
from typing import Any, Dict, AsyncIterable, Literal
from pydantic import BaseModel
from dotenv import load_dotenv
import os, json, tweepy, worldnewsapi
import system_prompts
load_dotenv()

gemini_chat = ChatGoogleGenerativeAI(
    model="gemini-2.0-flash",
    google_api_key=os.getenv("GOOGLE_API_KEY")
)

twitter_object = tweepy.Client(
    consumer_key=os.getenv("TWITTER_API_KEY"),
    consumer_secret=os.getenv("TWITTER_API_KEY_SECRET"),
    access_token=os.getenv("TWITTER_ACCESS_TOKEN"),
    access_token_secret=os.getenv("TWITTER_ACCESS_TOKEN_SECRET")
)

news_configuration = worldnewsapi.Configuration(
        host="https://api.worldnewsapi.com"
    )
news_configuration.api_key['apiKey'] = os.getenv('WORLD_NEWS_API')

memory = MemorySaver()

# @tool
# def get_exchange_rate(
#     currency_from: str = "USD",
#     currency_to: str = "EUR",
#     currency_date: str = "latest",
# ):
#     """Use this to get current exchange rate.

#     Args:
#         currency_from: The currency to convert from (e.g., "USD").
#         currency_to: The currency to convert to (e.g., "EUR").
#         currency_date: The date for the exchange rate or "latest". Defaults to "latest".

#     Returns:
#         A dictionary containing the exchange rate data, or an error message if the request fails.
#     """    
#     try:
#         response = httpx.get(
#             f"https://api.frankfurter.app/{currency_date}",
#             params={"from": currency_from, "to": currency_to},
#         )
#         response.raise_for_status()

#         data = response.json()
#         if "rates" not in data:
#             return {"error": "Invalid API response format."}
#         return data
#     except httpx.HTTPError as e:
#         return {"error": f"API request failed: {e}"}
#     except ValueError:
#         return {"error": "Invalid JSON response from API."}

# @tool
# def calculate_math(
#     expression: str
# ):
#     """Use this to perform mathematical calculations.

#     Args:
#         expression: A string containing a mathematical expression (e.g., "2 + 3", "5 * 4").

#     Returns:
#         The result of the calculation as a string, or an error message if the expression is invalid.
#     """
#     try:
#         # Basic safety check to prevent dangerous inputs
#         allowed_chars = set("0123456789 +-*/(). ")
#         if not all(c in allowed_chars for c in expression):
#             return {"error": "Invalid characters in expression. Use numbers, +, -, *, /, (), and spaces only."}
#         result = eval(expression, {"__builtins__": {}})
#         return {"result": str(result)}
#     except Exception as e:
#         return {"error": f"Calculation failed: {str(e)}"}
    
@tool
def TopicGenerator(
    text: str,
    text_match_indexes: str = 'title,content',
    source_country: str = 'us',
    language: str = 'en',
    sort: str = 'publish-time',
    sort_direction: str = 'ASC',
    offset: int = 0,
    number: int = 1
):
    """
    Search articles from WorldNewsAPI.

    Args:
        text: Required search query string (keywords, phrases)
        text_match_indexes: Where to search for the text (default: 'title,content')
        source_country: Country of news articles (default: 'us')
        language: Language of news articles (default: 'en')
        min_sentiment: Minimum sentiment of the news (default: -0.8)
        max_sentiment: Maximum sentiment of the news (default: 0.8)
        earliest_publish_date: News must be published after this date
        latest_publish_date: News must be published before this date
        news_sources: Comma-separated list of news sources
        authors: Comma-separated list of author names
        categories: Comma-separated list of categories
        entities: Filter news by entities
        location_filter: Filter news by radius around a certain location
        sort: Sorting criteria (default: 'publish-time')
        sort_direction: Sort direction (default: 'ASC')
        offset: Number of news to skip (default: 0)
        number: Number of news to return (default: 10)

    Returns:
        str: Markdown formatted string containing articles and metadata
    """
    try:
        with worldnewsapi.ApiClient(news_configuration) as api_client:
            api_instance = worldnewsapi.NewsApi(api_client)
            api_response = api_instance.search_news(
                text=text,
                text_match_indexes=text_match_indexes,
                source_country=source_country,
                language=language,
                sort=sort,
                sort_direction=sort_direction,
                offset=offset,
                number=number
            )
            articles = api_response.news
            articles = api_response.news
            news = "\n".join(
                f"""
            ### Title: {getattr(article, 'title', 'No title')}

            **URL:** [{getattr(article, 'url', 'No URL')}]({getattr(article, 'url', 'No URL')})

            **Date:** {getattr(article, 'publish_date', 'No date')}

            **Text:** {getattr(article, 'text', 'No description')}

            ------------------
            """ for article in articles
            )
            return {"result": str(news)}
    except Exception as e:
        print(f"Failed to generate news with error: {traceback.format_exc()}")
        return {"result": f"Failed to generate news with error: {str(e)}"}

@tool
def TweetCrafter(
    tweet_content: str
):
    """This tool generates a tweet based on the provided context
    
    Args:
        tweet_content: A string containing a news thread.

    Returns:
        The context of tweet to be posted on x.com   
    """
    try:
        prompt = [
            HumanMessage(content=system_prompts.tweet_generation_prompt.format(context=tweet_content))
        ]
        response = gemini_chat(prompt)
        tweet_data = json.loads(response.content)
        print(f"Tone selected: {tweet_data.get('tone')}")
        print(f"Format selected: {tweet_data.get('format')}")
        tweet = tweet_data['tweet']
        return {"result": str(tweet)}
    except Exception as e:
        print(f"Failed to generate tweet with error: {traceback.format_exc()}")
        return {"error": f"Unable to generate tweet with error: {str(e)}"}

@tool
def TweetSender(
    tweet_content: str
):
    """This tool posts a tweet on x.compile
    
    Args:
        tweet_content: A string which contains context of the tweet
        """
    try:
        twitter_object.create_tweet(text=tweet_content)
        return {"result": "successful"}
    except Exception as e:
        return {"result": f"unsuccessful with error: {str(e)}"}

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
        self.tools = [TweetCrafter, TweetSender]

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