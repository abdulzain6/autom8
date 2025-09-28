import atexit
import asyncio
import concurrent.futures
import threading
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


class EventLoopManager:
    """Manages a single asyncio event loop running in a background thread."""
    def __init__(self):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        atexit.register(self.stop_loop)

    def stop_loop(self):
        self._loop.call_soon_threadsafe(self._loop.stop)

    @property
    def loop(self):
        return self._loop
    

# Global thread pool for browser automation to avoid thread creation overhead
_event_loop_manager = EventLoopManager()

# Use the standard ThreadPoolExecutor for blocking tasks if needed, but not for asyncio.
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
        """
        logger.info(f"Running browser automation: {task[:100]}...")
        instructions = "Be super quick and use as few actions as possible. Complete the task efficiently with minimal steps. If you encounter a captcha, immediately end the automation and report 'captcha encountered'. At the end, return a detailed report of what has been done, including any requested information."
        api_key = config.OPENROUTER_API_KEY
        flash_mode = True
        use_vision = True
        images_per_step = 1
        steel_base_url = config.STEEL_BASE_URL

        def _setup_and_run_agent():
            client = Steel(base_url=config.STEEL_BASE_URL)
            session = None
            try:
                assert config.HTTP_PROXY is not None, "HTTP_PROXY must be set in config"
                
                session = client.sessions.create(solve_captcha=True, block_ads=True, proxy_url=config.HTTP_PROXY, use_proxy=True)
                cdp_url = f"ws://{steel_base_url.replace('http://', '').replace('https://', '')}?sessionId={session.id}"

                llm = ChatOpenAI(
                    model="x-ai/grok-4-fast:free", 
                    temperature=0.3, 
                    api_key=api_key, 
                    base_url=config.OPENROUTER_BASE_URL
                )

                page_extraction_llm = ChatOpenAI(
                    model="openai/gpt-oss-20b:free",
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
                    display_files_in_done_text=True,
                    images_per_step=images_per_step,
                    page_extraction_llm=page_extraction_llm,
                )

                # Define the async part
                async def _run_agent_async():
                    result = await agent.run(max_steps=7) 
                    return result.final_result() if result else None

                # Submit the async function to the shared event loop from this thread
                future = asyncio.run_coroutine_threadsafe(_run_agent_async(), _event_loop_manager.loop)
                
                # Wait for the result from the event loop
                result = future.result(timeout=300) # 5 minute timeout
                
                return {"result": result, "success": True}

            except Exception as e:
                logger.error(f"Browser automation failed: {e}")
                return {"result": None, "error": str(e), "success": False}
            finally:
                if session:
                    try:
                        client.sessions.release(session.id)
                    except Exception as e:
                        logger.error(f"Failed to release session: {e}")
        
        # Submit the synchronous wrapper to the thread pool
        future = _browser_executor.submit(_setup_and_run_agent)
        return future.result()



atexit.register(_browser_executor.shutdown, wait=True)
