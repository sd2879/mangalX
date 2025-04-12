# File: __main__.py

from common.server import A2AServer
from common.types import AgentCard, AgentCapabilities, AgentSkill, MissingAPIKeyError
from common.utils.push_notification_auth import PushNotificationSenderAuth
from agents.langgraph.task_manager import AgentTaskManager
from agents.langgraph.agent import CurrencyAgent
from agents.langgraph.brave_search import BraveSearchAgent
import click
import os
import logging
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class MultiAgentTaskManager(AgentTaskManager):
    def __init__(self, notification_sender_auth):
        self.currency_agent = CurrencyAgent()
        self.brave_search_agent = BraveSearchAgent()
        self.notification_sender_auth = notification_sender_auth

    async def process_task(self, request):
        # Route task based on message content or skill
        message = request.message.parts[0].text.lower()
        if "search for" in message:
            return await self.brave_search_agent.process_task(request)
        else:
            return await self.currency_agent.process_task(request)

@click.command()
@click.option("--host", "host", default="localhost")
@click.option("--port", "port", default=10000)
def main(host, port):
    """Starts the Multi-Agent server."""
    try:
        if not os.getenv("GOOGLE_API_KEY"):
            raise MissingAPIKeyError("GOOGLE_API_KEY environment variable not set.")
        if not os.getenv("BRAVE_SEARCH_API_KEY"):
            raise MissingAPIKeyError("BRAVE_SEARCH_API_KEY environment variable not set.")

        capabilities = AgentCapabilities(streaming=True, pushNotifications=True)
        currency_skill = AgentSkill(
            id="convert_currency",
            name="Currency Exchange Rates Tool",
            description="Helps with exchange values between various currencies",
            tags=["currency conversion", "currency exchange"],
            examples=["What is exchange rate between USD and GBP?"],
        )
        search_skill = AgentSkill(
            id="brave_search",
            name="Web Search Tool",
            description="Searches the web for information using Brave Search API",
            tags=["web search", "information retrieval"],
            examples=["search for AI trends"],
        )
        agent_card = AgentCard(
            name="Multi-Agent",
            description="Handles currency exchange and web search queries",
            url=f"http://{host}:{port}/",
            version="1.0.0",
            defaultInputModes=["text"],
            defaultOutputModes=["text"],
            capabilities=capabilities,
            skills=[currency_skill, search_skill],
        )

        notification_sender_auth = PushNotificationSenderAuth()
        notification_sender_auth.generate_jwk()
        server = A2AServer(
            agent_card=agent_card,
            task_manager=MultiAgentTaskManager(notification_sender_auth=notification_sender_auth),
            host=host,
            port=port,
        )

        server.app.add_route(
            "/.well-known/jwks.json", notification_sender_auth.handle_jwks_endpoint, methods=["GET"]
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