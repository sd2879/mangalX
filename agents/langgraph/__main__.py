from common.server import A2AServer
from common.types import AgentCard, AgentCapabilities, AgentSkill, MissingAPIKeyError
from common.utils.push_notification_auth import PushNotificationSenderAuth
from agents.langgraph.task_manager import AgentTaskManager
# from agents.langgraph.agent import CurrencyAgent
from agents.langgraph.agent import NewsTweetAgent  # Note: Should be renamed to NewsTweetAgent
import click
import os
import logging
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@click.command()
@click.option("--host", "host", default="localhost")
@click.option("--port", "port", default=10000)
def main(host, port):
    """Starts the News Tweet Agent server."""
    try:
        # Validate all required environment variables
        required_env_vars = {
            "GOOGLE_API_KEY": "Google API key for LLM",
            "TWITTER_API_KEY": "Twitter API key",
            "TWITTER_API_KEY_SECRET": "Twitter API key secret",
            "TWITTER_ACCESS_TOKEN": "Twitter access token",
            "TWITTER_ACCESS_TOKEN_SECRET": "Twitter access token secret",
            "WORLD_NEWS_API": "WorldNewsAPI key",
        }
        for var, desc in required_env_vars.items():
            if not os.getenv(var):
                raise MissingAPIKeyError(f"{var} environment variable not set. Required for {desc}.")

        capabilities = AgentCapabilities(streaming=True, pushNotifications=True)
        skill_news = AgentSkill(
            id="search_news",
            name="News Search Tool",
            description="Searches for news articles based on topics or keywords",
            tags=["news", "articles", "search"],
            examples=["Find news about AI advancements", "Search for recent tech news"],
        )
        skill_tweet = AgentSkill(
            id="generate_tweet",
            name="Tweet Generation Tool",
            description="Generates tweets based on news content or context",
            tags=["tweets", "social media", "content creation"],
            examples=["Create a tweet about a recent tech event"],
        )
        skill_post = AgentSkill(
            id="post_tweet",
            name="Tweet Posting Tool",
            description="Posts tweets to X.com",
            tags=["tweets", "social media", "posting"],
            examples=["Post this tweet: AI is transforming tech!"],
        )
        agent_card = AgentCard(
            name="News Tweet Agent",
            description="Searches news articles, generates tweets, and posts to X.com",
            url=f"http://{host}:{port}/",
            version="1.0.0",
            defaultInputModes=NewsTweetAgent.SUPPORTED_CONTENT_TYPES,
            defaultOutputModes=NewsTweetAgent.SUPPORTED_CONTENT_TYPES,
            capabilities=capabilities,
            skills=[skill_news, skill_tweet, skill_post],
        )

        notification_sender_auth = PushNotificationSenderAuth()
        notification_sender_auth.generate_jwk()
        server = A2AServer(
            agent_card=agent_card,
            task_manager=AgentTaskManager(
                agent=NewsTweetAgent(),
                notification_sender_auth=notification_sender_auth
            ),
            host=host,
            port=port,
        )

        server.app.add_route(
            "/.well-known/jwks.json",
            notification_sender_auth.handle_jwks_endpoint,
            methods=["GET"],
        )

        logger.info(f"Starting server on {host}:{port}")
        server.start()
    except MissingAPIKeyError as e:
        logger.error(f"Error: {e}")
        exit(1)
    except Exception as e:
        logger.error(f"An error occurred during server startup: {e}")
        exit(1)


if __name__ == "__main__":
    main()