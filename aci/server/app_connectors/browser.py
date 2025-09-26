import atexit
import asyncio
import concurrent.futures
from typing import Optional

from aci.common.db.sql_models import LinkedAccount
from aci.common.logging_setup import get_logger
from aci.common.schemas.security_scheme import NoAuthScheme, NoAuthSchemeCredentials
from aci.server import config
from aci.server.app_connectors.base import AppConnectorBase
from browser_use import Agent, BrowserSession
from browser_use.llm import ChatOpenAI
from steel import Steel

logger = get_logger(__name__)

# Global thread pool for browser automation to avoid thread creation overhead
_browser_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=config.BROWSER_MAX_WORKERS, thread_name_prefix="browser-automation"
)


class Browser(AppConnectorBase):
    """
    A connector for browser automation using browser-use library with Steel browsers.
    """

    def __init__(
        self,
        linked_account: LinkedAccount,
        security_scheme: NoAuthScheme,
        security_credentials: NoAuthSchemeCredentials,
        run_id: Optional[str] = None,
    ):
        """
        Initializes the Browser connector.
        """
        super().__init__(
            linked_account, security_scheme, security_credentials, run_id=run_id
        )
        logger.info("Browser connector initialized.")

    def _before_execute(self) -> None:
        pass

    def run_browser_automation(self, task: str) -> dict:
        """
        Runs browser automation using browser-use with Steel browser session.

        Args:
            task: The task description for the AI agent.

        Returns:
            A dictionary containing the automation result and session viewer URL.
        """
        logger.info(f"Running browser automation: {task[:100]}...")

        # Configuration from config
        instructions = "Be super quick and use as few actions as possible. Complete the task efficiently with minimal steps. At the end, return a detailed report of what has been done, including any requested information."
        api_key = config.OPENROUTER_API_KEY
        max_steps = 10
        flash_mode = True
        use_vision = True
        use_vision_for_planner = True
        images_per_step = 1
        steel_base_url = config.STEEL_BASE_URL

        client = Steel(base_url=steel_base_url)

        try:
            session = client.sessions.create()
            cdp_url = f"ws://{steel_base_url.replace('http://', '').replace('https://', '')}?sessionId={session.id}"

            llm = ChatOpenAI(
                model="x-ai/grok-4-fast:free", 
                temperature=0.3, 
                api_key=api_key, 
                base_url=config.OPENROUTER_BASE_URL
            )

            agent = Agent(
                task=task,
                llm=llm,
                browser_session=BrowserSession(cdp_url=cdp_url),
                flash_mode=flash_mode,
                extend_system_message=instructions,
                use_thinking=False,
                use_vision=use_vision,
                use_vision_for_planner=use_vision_for_planner,
                display_files_in_done_text=True,
                images_per_step=images_per_step,
            )

            async def _run_agent():
                result = await agent.run(max_steps=max_steps)
                return result.final_result() if result else None

            future = _browser_executor.submit(asyncio.run, _run_agent())
            try:
                result = future.result(timeout=300)  # 5 minute timeout
            except concurrent.futures.TimeoutError:
                future.cancel()
                raise Exception("Browser automation timed out after 5 minutes")
            except Exception as e:
                logger.error(f"Browser automation execution failed: {e}")
                raise

            return {"result": result, "success": True}

        except Exception as e:
            logger.error(f"Browser automation failed: {e}")
            return {"result": None, "error": str(e), "success": False}
        finally:
            try:
                if "session" in locals():
                    client.sessions.release(session.id)
            except Exception as e:
                logger.error(f"Failed to release session: {e}")


atexit.register(_browser_executor.shutdown, wait=True)
