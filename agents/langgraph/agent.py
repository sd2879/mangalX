from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import AIMessage, ToolMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate
from typing import Any, Dict, AsyncIterable, Literal
from pydantic import BaseModel
from dotenv import load_dotenv
import os, json, tweepy, worldnewsapi
import system_prompts
from tweepy import TweepyException
from worldnewsapi import ApiException

load_dotenv()

required_env_vars = [
    "GOOGLE_API_KEY",
    "TWITTER_API_KEY",
    "TWITTER_API_KEY_SECRET",
    "TWITTER_ACCESS_TOKEN",
    "TWITTER_ACCESS_TOKEN_SECRET",
    "WORLD_NEWS_API",
]
for var in required_env_vars:
    if not os.getenv(var):
        raise ValueError(f"Missing environment variable: {var}")

gemini_chat = ChatGoogleGenerativeAI(
    model="gemini-1.5-flash",  # Updated to a valid model (verify with Google API)
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
news_configuration.api_key["apiKey"] = os.getenv("WORLD_NEWS_API")

memory = MemorySaver()

@tool
def TopicGenerator(
    text: str,
    text_match_indexes: str = "title,content",
    source_country: str = "us",
    language: str = "en",
    sort: str = "publish-time",
    sort_direction: str = "ASC",
    offset: int = 0,
    number: int = 1,
):
    """
    Search articles from WorldNewsAPI.

    Args:
        text: Required search query string (keywords, phrases)
        text_match_indexes: Where to search for the text (default: 'title,content')
        source_country: Country of news articles (default: 'UK')
        language: Language of news articles (default: 'en')
        sort: Sorting criteria (default: 'publish-time')
        sort_direction: Sort direction (default: 'ASC')
        offset: Number of news to skip (default: 0)
        number: Number of news to return (default: 1)

    Returns:
        dict: Contains 'result' key with Markdown formatted string of articles or an error message
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
                number=number,
            )
            articles = api_response.news
            news = "\n".join(
                f"""
            ### Title: {getattr(article, 'title', 'No title')}

            **URL:** [{getattr(article, 'url', 'No URL')}]({getattr(article, 'url', 'No URL')})

            **Date:** {getattr(article, 'publish_date', 'No date')}

            **Text:** {getattr(article, 'text', 'No description')}

            ------------------
            """
                for article in articles
            )
            return {"result": str(news)}
    except ApiException as e:
        return {"result": f"News API error: {str(e)}"}
    except Exception as e:
        return {"result": f"Unexpected error: {str(e)}"}

@tool
def TweetCrafter(tweet_content: str):
    """
    Generates a tweet based on the provided context.

    Args:
        tweet_content: A string containing a news thread or context.

    Returns:
        dict: Contains 'result' key with the tweet or 'error' key with an error message.
    """
    try:
        if len(tweet_content) == 0:
            return {"error": "Tweet content cannot be empty"}
        prompt = HumanMessage(content=system_prompts.tweet_generation_prompt.format(context=tweet_content))
        response = gemini_chat([prompt])
        tweet_data = json.loads(response.content)
        if not isinstance(tweet_data, dict) or "tweet" not in tweet_data:
            return {"error": "Invalid tweet data format"}
        tweet = tweet_data["tweet"]
        if len(tweet) > 280:
            return {"error": "Generated tweet exceeds 280 characters"}
        print(f"Tone selected: {tweet_data.get('tone', 'unknown')}")
        print(f"Format selected: {tweet_data.get('format', 'unknown')}")
        return {"result": str(tweet)}
    except AttributeError:
        return {"error": "Tweet generation prompt is not configured"}
    except json.JSONDecodeError:
        return {"error": "Failed to parse tweet response"}
    except Exception as e:
        return {"error": f"Unable to generate tweet: {str(e)}"}

@tool
def TweetSender(tweet_content: str):
    """
    Posts a tweet on X.com.

    Args:
        tweet_content: A string containing the tweet text.

    Returns:
        dict: Contains 'result' key indicating 'successful' or an error message.
    """
    try:
        if len(tweet_content) > 280:
            return {"result": "Tweet exceeds 280 characters"}
        if len(tweet_content) == 0:
            return {"result": "Tweet content cannot be empty"}
        twitter_object.create_tweet(text=tweet_content)
        return {"result": "successful"}
    except TweepyException as e:
        return {"result": f"Failed to post tweet: {str(e)}"}
    except Exception as e:
        return {"result": f"Unexpected error: {str(e)}"}

class ResponseFormat(BaseModel):
    """Respond to the user in this format."""
    status: Literal["input_required", "completed", "error"] = "input_required"
    message: str

class NewsTweetAgent:
    SYSTEM_INSTRUCTION = (
        "You are a specialized assistant for generating and posting tweets based on news topics. "
        "Your purpose is to: "
        "1. Use the 'TopicGenerator' tool to search for news articles based on user-provided topics or keywords. "
        "2. Use the 'TweetCrafter' tool to create tweets based on provided news content or context. "
        "3. Use the 'TweetSender' tool to post tweets to X.com when explicitly requested. "
        "If the user asks about anything other than news searching, tweet generation, or tweet posting, "
        "politely state that you cannot help with that topic and can only assist with news-related queries or tweeting tasks. "
        "Do not attempt to answer unrelated questions or use tools for other purposes. "
        "Examples: "
        "- Query: 'Find news about AI advancements' -> Use TopicGenerator to return relevant articles. "
        "- Query: 'Create a tweet about a recent tech event' -> Use TweetCrafter to generate a tweet. "
        "- Query: 'Post this tweet: AI is cool' -> Use TweetSender to post the tweet. "
        "- Query: 'What's the weather?' -> Respond: 'I can only assist with news searching, tweet generation, or posting tweets.' "
        "Set response status to input_required if the user needs to provide more information (e.g., missing topic or tweet content). "
        "Set response status to error if there is an error while processing the request. "
        "Set response status to completed if the request is complete."
    )

    def __init__(self):
        self.model = ChatGoogleGenerativeAI(model="gemini-1.5-flash")  # Verify model name
        self.tools = [TopicGenerator, TweetCrafter, TweetSender]
        system_prompt = ChatPromptTemplate.from_messages([
            ("system", self.SYSTEM_INSTRUCTION),
            ("human", "{input}")
        ])
        self.model = self.model.bind(prompt=system_prompt)
        self.graph = create_react_agent(
            self.model, tools=self.tools, checkpointer=memory, response_format=ResponseFormat
        )

    def invoke(self, query, sessionId) -> Dict[str, Any]:
        if not isinstance(query, str):
            return {
                "is_task_complete": False,
                "require_user_input": True,
                "content": "Query must be a string"
            }
        config = {"configurable": {"thread_id": sessionId}}
        try:
            self.graph.invoke({"messages": [("user", query)]}, config)
            return self.get_agent_response(config)
        except Exception as e:
            return {
                "is_task_complete": False,
                "require_user_input": True,
                "content": f"Error processing query: {str(e)}"
            }

    async def stream(self, query, sessionId) -> AsyncIterable[Dict[str, Any]]:
        if not isinstance(query, str):
            yield {
                "is_task_complete": False,
                "require_user_input": True,
                "content": "Query must be a string"
            }
            return
        inputs = {"messages": [("user", query)]}
        config = {"configurable": {"thread_id": sessionId}}
        try:
            for item in self.graph.stream(inputs, config, stream_mode="values"):
                message = item["messages"][-1]
                if (
                    isinstance(message, AIMessage)
                    and message.tool_calls
                    and len(message.tool_calls) > 0
                ):
                    tool_name = message.tool_calls[0]["name"]
                    if tool_name == "TopicGenerator":
                        yield {
                            "is_task_complete": False,
                            "require_user_input": False,
                            "content": "Searching for news articles..."
                        }
                    elif tool_name == "TweetCrafter":
                        yield {
                            "is_task_complete": False,
                            "require_user_input": False,
                            "content": "Crafting a tweet..."
                        }
                    elif tool_name == "TweetSender":
                        yield {
                            "is_task_complete": False,
                            "require_user_input": False,
                            "content": "Posting the tweet..."
                        }
                elif isinstance(message, ToolMessage):
                    yield {
                        "is_task_complete": False,
                        "require_user_input": False,
                        "content": "Processing the results..."
                    }
            yield self.get_agent_response(config)
        except Exception as e:
            yield {
                "is_task_complete": False,
                "require_user_input": True,
                "content": f"Streaming error: {str(e)}"
            }

    def get_agent_response(self, config):
        current_state = self.graph.get_state(config)
        structured_response = current_state.values.get("structured_response")
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
            "content": "Unable to process your request at the moment. Please try again."
        }

    SUPPORTED_CONTENT_TYPES = ["text", "text/plain"]