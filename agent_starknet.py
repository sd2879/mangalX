import os
import json
import time
import asyncio
from typing import Any, Dict, AsyncIterable, Literal
from dotenv import load_dotenv
from pydantic import BaseModel
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.tools import tool
from langchain_core.messages import AIMessage, ToolMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import MemorySaver
from tweepy import TweepyException
from worldnewsapi import ApiException, Configuration, NewsApi
from starknet_py.net.account.account import Account
from starknet_py.net.full_node_client import FullNodeClient
from starknet_py.net.signer.stark_curve_signer import KeyPair
from starknet_py.contract import Contract
import system_prompts
import os, json, tweepy, worldnewsapi

# Load environment variables
load_dotenv()

# Required environment variables
required_env_vars = [
    "GOOGLE_API_KEY",
    "TWITTER_API_KEY",
    "TWITTER_API_KEY_SECRET",
    "TWITTER_ACCESS_TOKEN",
    "TWITTER_ACCESS_TOKEN_SECRET",
    "WORLD_NEWS_API",
    "STARKNET_PRIVATE_KEY",
    "STARKNET_ACCOUNT_ADDRESS",
]
for var in required_env_vars:
    if not os.getenv(var):
        raise ValueError(f"Missing environment variable: {var}")

# Initialize Google Gemini
gemini_chat = ChatGoogleGenerativeAI(
    model="gemini-1.5-flash",
    google_api_key=os.getenv("GOOGLE_API_KEY")
)

# Initialize Twitter client
twitter_object = tweepy.Client(
    consumer_key=os.getenv("TWITTER_API_KEY"),
    consumer_secret=os.getenv("TWITTER_API_KEY_SECRET"),
    access_token=os.getenv("TWITTER_ACCESS_TOKEN"),
    access_token_secret=os.getenv("TWITTER_ACCESS_TOKEN_SECRET")
)


# Initialize WorldNewsAPI
news_configuration = Configuration(host="https://api.worldnewsapi.com")
news_configuration.api_key["apiKey"] = os.getenv("WORLD_NEWS_API")

# Initialize Starknet client and account
STARKNET_NODE_URL = "https://starknet-sepolia.public.blastapi.io/rpc/v0_7"
client = FullNodeClient(node_url=STARKNET_NODE_URL)
account = Account(
    address=int(os.getenv("STARKNET_ACCOUNT_ADDRESS"), 16),
    client=client,
    key_pair=KeyPair.from_private_key(int(os.getenv("STARKNET_PRIVATE_KEY"), 16)),
    chain="starknet_sepolia"
)

# Contract addresses (Sepolia testnet)
USDC_ADDRESS = "0x05a9b4b0c1d1d02c1a04a8f6dd88c7e1b8f547a8421a2c3595d81c6d42e3d65"  # Mock USDC
STRK_ADDRESS = "0x04718f5a0fc34cc1af16a1cdee98ffb20c31f5cd61d6ab07201858f4287c938d"  # STRK
JEDISWAP_ROUTER_ADDRESS = "0x041fd22b238fa21cfcf5dd45a8548974d8263b3b7ec0cb6b6dd9bd6692e76e11"  # JediSwap router

# Tool costs in USDC
TOOL_COSTS = {
    "TopicGenerator": 1.0,  # 1 USDC
    "TweetCrafter": 0.5,    # 0.5 USDC
    "TweetSender": 0.2      # 0.2 USDC
}

# Memory for agent state
memory = MemorySaver()

async def perform_usdc_to_strk_swap(amount_usdc: float, tool_name: str) -> bool:
    """
    Perform a USDC to STRK swap on Starknet using JediSwap.
    
    Args:
        amount_usdc: Amount of USDC to swap (in USDC).
        tool_name: Name of the tool triggering the swap.
    
    Returns:
        bool: True if swap succeeds, False otherwise.
    """
    try:
        # Convert USDC amount to wei (6 decimals)
        amount_usdc_wei = int(amount_usdc * 10**6)
        
        # Check USDC balance
        usdc_contract = await Contract.from_address(USDC_ADDRESS, account)
        balance_result = await usdc_contract.functions["balanceOf"].call(account.address)
        balance_usdc = balance_result.balance / 10**6
        if balance_usdc < amount_usdc:
            print(f"Insufficient USDC balance for {tool_name}: {balance_usdc} < {amount_usdc}")
            return False
        
        # Approve USDC for JediSwap router
        approve_call = usdc_contract.functions["approve"].prepare(
            JEDISWAP_ROUTER_ADDRESS,
            amount_usdc_wei
        )
        
        # Mock price: 1 USDC = 10 STRK (replace with Pragma oracle in production)
        amount_strk_min = int(amount_usdc * 10 * 10**18)  # STRK has 18 decimals
        
        # Prepare swap call
        jediswap_contract = await Contract.from_address(JEDISWAP_ROUTER_ADDRESS, account)
        swap_call = jediswap_contract.functions["swap_exact_tokens_for_tokens"].prepare(
            amountIn=amount_usdc_wei,
            amountOutMin=amount_strk_min,
            path=[USDC_ADDRESS, STRK_ADDRESS],
            to=account.address,
            deadline=int(time.time()) + 3600
        )
        
        # Execute transaction
        transaction = await account.execute_v3(
            calls=[approve_call, swap_call],
            auto_estimate=True
        )
        receipt = await client.wait_for_tx(transaction.transaction_hash)
        
        if receipt.status.is_accepted:
            print(f"Swap successful for {tool_name}: {amount_usdc} USDC -> ~{amount_usdc * 10} STRK")
            return True
        else:
            print(f"Swap failed for {tool_name}: Transaction rejected")
            return False
            
    except Exception as e:
        print(f"Swap error for {tool_name}: {str(e)}")
        return False

@tool
async def TopicGenerator(
    text: str,
    text_match_indexes: str = "title,content",
    source_country: str = "us",
    language: str = "en",
    sort: str = "publish-time",
    sort_direction: str = "ASC",
    offset: int = 0,
    number: int = 1,
):
    """Search articles from WorldNewsAPI."""
    if not await perform_usdc_to_strk_swap(TOOL_COSTS["TopicGenerator"], "TopicGenerator"):
        return {"result": "Failed to process payment: Insufficient USDC or swap error"}
    
    try:
        with worldnewsapi.ApiClient(news_configuration) as api_client:
            api_instance = NewsApi(api_client)
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
async def TweetCrafter(tweet_content: str):
    """Generates a tweet based on the provided context."""
    if not await perform_usdc_to_strk_swap(TOOL_COSTS["TweetCrafter"], "TweetCrafter"):
        return {"error": "Failed to process payment: Insufficient USDC or swap error"}
    
    try:
        if len(tweet_content) == 0:
            return {"error": "Tweet content cannot be empty"}
        prompt = HumanMessage(content=system_prompts.tweet_generation_prompt.format(context=tweet_content))
        response = await gemini_chat.ainvoke([prompt])
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
async def TweetSender(tweet_content: str):
    """Posts a tweet on X.com."""
    if not await perform_usdc_to_strk_swap(TOOL_COSTS["TweetSender"], "TweetSender"):
        return {"result": "Failed to process payment: Insufficient USDC or swap error"}
    
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
        "Each action requires a USDC payment, swapped to STRK on Starknet: "
        "TopicGenerator (1 USDC), TweetCrafter (0.5 USDC), TweetSender (0.2 USDC). "
        "Your purpose is to: "
        "1. Use 'TopicGenerator' to search for news articles. "
        "2. Use 'TweetCrafter' to create tweets. "
        "3. Use 'TweetSender' to post tweets to X.com. "
        "For unrelated queries, respond: 'I can only assist with news searching, tweet generation, or posting tweets.' "
        "Set response status to 'input_required' if more information is needed, "
        "'error' for processing errors (including payment failures), or 'completed' for successful requests."
    )

    def __init__(self):
        self.model = ChatGoogleGenerativeAI(model="gemini-1.5-flash")
        self.tools = [TopicGenerator, TweetCrafter, TweetSender]
        system_prompt = ChatPromptTemplate.from_messages([
            ("system", self.SYSTEM_INSTRUCTION),
            ("human", "{input}")
        ])
        self.model = self.model.bind(prompt=system_prompt)
        self.graph = create_react_agent(
            model=self.model,
            tools=self.tools,
            checkpointer=memory,
            state_modifier=ResponseFormat
        )

    async def invoke(self, query: str, session_id: str) -> Dict[str, Any]:
        if not isinstance(query, str):
            return {
                "is_task_complete": False,
                "require_user_input": True,
                "content": "Query must be a string"
            }
        config = {"configurable": {"thread_id": session_id}}
        try:
            await self.graph.ainvoke({"messages": [HumanMessage(content=query)]}, config)
            return await self.get_agent_response(config)
        except Exception as e:
            return {
                "is_task_complete": False,
                "require_user_input": True,
                "content": f"Error processing query: {str(e)}"
            }

    async def stream(self, query: str, session_id: str) -> AsyncIterable[Dict[str, Any]]:
        if not isinstance(query, str):
            yield {
                "is_task_complete": False,
                "require_user_input": True,
                "content": "Query must be a string"
            }
            return
        inputs = {"messages": [HumanMessage(content=query)]}
        config = {"configurable": {"thread_id": session_id}}
        try:
            async for item in self.graph.astream(inputs, config, stream_mode="values"):
                message = item["messages"][-1]
                if isinstance(message, AIMessage) and message.tool_calls:
                    tool_name = message.tool_calls[0]["name"]
                    yield {
                        "is_task_complete": False,
                        "require_user_input": False,
                        "content": f"Processing payment and executing {tool_name}..."
                    }
                elif isinstance(message, ToolMessage):
                    yield {
                        "is_task_complete": False,
                        "require_user_input": False,
                        "content": "Processing results..."
                    }
            yield await self.get_agent_response(config)
        except Exception as e:
            yield {
                "is_task_complete": False,
                "require_user_input": True,
                "content": f"Streaming error: {str(e)}"
            }

    async def get_agent_response(self, config: Dict[str, Any]) -> Dict[str, Any]:
        current_state = await self.graph.aget_state(config)
        structured_response = current_state.values.get("structured_response")
        if structured_response and isinstance(structured_response, ResponseFormat):
            return {
                "is_task_complete": structured_response.status == "completed",
                "require_user_input": structured_response.status == "input_required",
                "content": structured_response.message
            }
        return {
            "is_task_complete": False,
            "require_user_input": True,
            "content": "Unable to process your request. Please try again."
        }

    SUPPORTED_CONTENT_TYPES = ["text", "text/plain"]