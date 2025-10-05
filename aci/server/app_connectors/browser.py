import atexit
import asyncio
import concurrent.futures
import os
import threading
import redis
from redis_semaphore import Semaphore
from typing import Optional
from aci.common.db.sql_models import LinkedAccount
from aci.common.logging_setup import get_logger
from aci.common.schemas.security_scheme import NoAuthScheme, NoAuthSchemeCredentials
from aci.server import config
from aci.server.app_connectors.base import AppConnectorBase
from browser_use import Agent, BrowserSession
from browser_use.llm import ChatOpenAI
from steel import Steel
from skyvern import Skyvern
from crawl4ai import AsyncWebCrawler
from crawl4ai.async_configs import BrowserConfig, CrawlerRunConfig, CacheMode, LLMConfig
from crawl4ai import UndetectedAdapter
from crawl4ai.async_crawler_strategy import AsyncPlaywrightCrawlerStrategy
from crawl4ai.extraction_strategy import LLMExtractionStrategy

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

_browser_semaphore = Semaphore(
    client=redis.from_url(config.REDIS_URL),
    count=config.BROWSER_MAX_WORKERS,
    namespace="browser_semaphore",
    stale_client_timeout=600, # 10 minutes
)

DISABLE_FINGERPRINT_SITES = [
    "tankiforum.com",
    "forum.tankiforum.com",
    "www.tankiforum.com",
]

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
        Runs a browser automation task concurrently and safely.
        """
        logger.info(f"Submitting browser task to executor: {task[:100]}...")

        if config.USE_SKYVERN:
            # Use Skyvern for browser automation
            async def _run_skyvern_task():
                skyvern = Skyvern(
                    base_url=config.SKYVERN_BASE_URL,
                    api_key=config.SKYVERN_API_KEY
                )
                task_result = await skyvern.run_task(
                    prompt=task,
                    wait_for_completion=True,
                    max_steps=10,
                    model={"reasoning": {"enabled": False}},
                )
                return {"result": task_result.output, "success": True}

            async def _run_skyvern():
                try:
                    return await _run_skyvern_task()
                except Exception as e:
                    logger.error(f"Skyvern task failed: {e}", exc_info=True)
                    return {"result": None, "error": str(e), "success": False}

            with _browser_semaphore:
                future = asyncio.run_coroutine_threadsafe(_run_skyvern(), _event_loop_manager.loop)
                return future.result(timeout=300)
        else:
            # Use browser-use with Steel
            # This nested function encapsulates the entire lifecycle of a single browser task.
            # It's what will be executed by a thread from the thread pool.
            def _setup_and_run_agent():
                process_id = os.getpid()
                thread_id = threading.get_ident()
                logger.info(f"[PID: {process_id} | Thread: {thread_id}] Starting setup for a new browser task.")

                # All clients and sessions are created inside this function, not outside.
                client = Steel(base_url=config.STEEL_BASE_URL)
                session = None
                try:
                    with _browser_semaphore:
                        # 1. Create a new remote browser session for this task only.
                        assert config.HTTP_PROXY is not None, "HTTP_PROXY must be set in config"
                        disable_fingerprint = any(site in task for site in DISABLE_FINGERPRINT_SITES)

                        stealth_settings = {
                            "humanize_interactions": True,
                            "skip_fingerprint_injection": disable_fingerprint
                        }
                        logger.info(f"[PID: {process_id} | Thread: {thread_id}] Stealth settings configured: {stealth_settings}")


                        session = client.sessions.create(block_ads=True, use_proxy=True, proxy_url=config.HTTP_PROXY, stealth_config=stealth_settings) # type: ignore
                        cdp_url = f"ws://{config.STEEL_BASE_URL.replace('http://', '').replace('https://', '')}?sessionId={session.id}"
                        
                        logger.info(f"[PID: {process_id} | Thread: {thread_id}] Created unique Steel Session ID: {session.id}")

                        # LLM and Agent configuration
                        api_key = config.OPENROUTER_API_KEY
                        llm = ChatOpenAI(model="x-ai/grok-4-fast", temperature=0.3, api_key=api_key, base_url=config.OPENROUTER_BASE_URL)
                        page_extraction_llm = ChatOpenAI(model="openai/gpt-oss-20b:free", temperature=0.3, api_key=api_key, base_url=config.OPENROUTER_BASE_URL, reasoning_effort="minimal")

                        # 2. Create a new BrowserSession instance for this task only.
                        browser_session_for_this_agent = BrowserSession(cdp_url=cdp_url)
                        logger.info(f"[PID: {process_id} | Thread: {thread_id}] Created unique BrowserSession object ID: {id(browser_session_for_this_agent)}")

                        # 3. Create a new Agent instance tied to the unique session.
                        # NOTE: Set use_vision=False as the Groq model doesn't support it.
                        agent = Agent(
                            task=task,
                            llm=llm,
                            browser_session=browser_session_for_this_agent,
                            flash_mode=True,
                            extend_system_message="Be super quick and use as few actions as possible. If you encounter a captcha, report 'captcha encountered' and end.",
                            use_thinking=False,
                            use_vision=False, # Corrected based on logs
                            display_files_in_done_text=True,
                            images_per_step=1,
                            page_extraction_llm=page_extraction_llm,
                        )
                        agent.settings.use_vision = True
                        
                        logger.info(f"[PID: {process_id} | Thread: {thread_id}] Created unique Agent object ID: {id(agent)}")
                        
                        # The async part of the task
                        async def _run_agent_async():
                            result = await agent.run(max_steps=7) 
                            return result.final_result() if result else None

                        # 4. Safely submit the async task to the shared event loop.
                        future = asyncio.run_coroutine_threadsafe(_run_agent_async(), _event_loop_manager.loop)
                        result = future.result(timeout=300) # 5 minute timeout
                        
                        logger.info(f"[PID: {process_id} | Thread: {thread_id}] Task finished successfully.")
                        return {"result": result, "success": True}

                except Exception as e:
                    logger.error(f"[PID: {process_id} | Thread: {thread_id}] Browser automation failed: {e}", exc_info=True)
                    return {"result": None, "error": str(e), "success": False}
                finally:
                    # 5. Always clean up and release the remote browser session.
                    if session:
                        try:
                            client.sessions.release(session.id)
                            logger.info(f"[PID: {process_id} | Thread: {thread_id}] Released Steel Session ID: {session.id}")
                        except Exception as e:
                            logger.error(f"[PID: {process_id} | Thread: {thread_id}] Failed to release session {session.id}: {e}")
            
            # Submit the entire encapsulated function to the thread pool.
            future = _browser_executor.submit(_setup_and_run_agent)
            return future.result()

    def scrape_with_browser(self, url: str, extraction_instructions: Optional[str] = None) -> dict:
        """
        Scrapes a website using crawl4ai with Steel browser via CDP and returns markdown content.
        Optionally uses LLM extraction strategy with custom instructions.
        Uses semaphore locking to manage concurrent browser sessions.
        
        Args:
            url (str): The URL to scrape
            extraction_instructions (Optional[str]): LLM instructions for data extraction. If provided,
                                                   uses GPT-OSS model to extract structured data according to instructions.
            
        Returns:
            dict: Contains 'markdown' content and 'success' status, or 'error' on failure.
                  If extraction_instructions provided, also contains 'extracted_content' with LLM results.
        """
        logger.info(f"Starting browser scraping for URL: {url}")
        
        def _setup_and_run_crawler():
            process_id = os.getpid()
            thread_id = threading.get_ident()
            logger.info(f"[PID: {process_id} | Thread: {thread_id}] Starting browser scraping setup.")
            
            client = Steel(base_url=config.STEEL_BASE_URL)
            session = None
            
            try:
                with _browser_semaphore:
                    # Create Steel browser session
                    assert config.HTTP_PROXY is not None, "HTTP_PROXY must be set in config"
                    
                    disable_fingerprint = any(site in url for site in DISABLE_FINGERPRINT_SITES)
                    stealth_settings = {
                        "humanize_interactions": True,
                        "skip_fingerprint_injection": disable_fingerprint
                    }
                    
                    session = client.sessions.create(
                        block_ads=True, 
                        use_proxy=True, 
                        proxy_url=config.HTTP_PROXY, 
                        stealth_config=stealth_settings  # type: ignore
                    )
                    
                    # Build CDP URL for Steel browser
                    cdp_url = f"ws://{config.STEEL_BASE_URL.replace('http://', '').replace('https://', '')}?sessionId={session.id}"
                    logger.info(f"[PID: {process_id} | Thread: {thread_id}] Created Steel Session ID: {session.id}")
                    
                    # Setup crawl4ai with Steel browser via CDP
                    async def _run_crawler_async():
                        undetected_adapter = UndetectedAdapter()
                        browser_config = BrowserConfig(
                            cdp_url=cdp_url,
                            browser_mode="cdp",
                            proxy=config.HTTP_PROXY or "",
                            enable_stealth=True,
                        )
                        crawler_strategy = AsyncPlaywrightCrawlerStrategy(
                            browser_config=browser_config,
                            browser_adapter=undetected_adapter
                        )

                        # Setup crawler run config with optional LLM extraction
                        crawl_config = None
                        if extraction_instructions:
                            # Setup LLM extraction strategy with GPT-OSS
                            llm_config = LLMConfig(
                                provider="openrouter/openai/gpt-oss-20b:free",
                                api_token=config.OPENROUTER_API_KEY,
                                base_url=config.OPENROUTER_BASE_URL,
                            )
                            
                            llm_strategy = LLMExtractionStrategy(
                                llm_config=llm_config,
                                extraction_type="block",
                                instruction=extraction_instructions,
                                overlap_rate=0.1,
                                chunk_token_threshold=75000,
                                apply_chunking=True,
                                input_format="markdown",
                                reasoning_effort="minimal",
                                force_json_response=True
                            )
                            
                            crawl_config = CrawlerRunConfig(
                                extraction_strategy=llm_strategy,
                                cache_mode=CacheMode.BYPASS
                            )

                        async with AsyncWebCrawler(
                            crawler_strategy=crawler_strategy,
                            config=browser_config
                        ) as crawler:
                            # Run the crawler on the URL
                            if crawl_config:
                                result = await crawler.arun(url=url, config=crawl_config)
                                return {
                                    "extracted_content": getattr(result, 'extracted_content', None) if result else None  # type: ignore
                                }
                            else:
                                result = await crawler.arun(url=url)
                                return {
                                    "markdown": getattr(result, 'markdown', None) if result else None,  # type: ignore
                                }
                    
                    # Submit to event loop and get result
                    future = asyncio.run_coroutine_threadsafe(_run_crawler_async(), _event_loop_manager.loop)
                    crawler_result = future.result(timeout=300)  # 5 minute timeout
                    
                    logger.info(f"[PID: {process_id} | Thread: {thread_id}] Browser scraping completed successfully.")
                    return {
                        "markdown": crawler_result["markdown"],
                        "extracted_content": crawler_result["extracted_content"],
                        "success": True
                    }
                    
            except Exception as e:
                logger.error(f"[PID: {process_id} | Thread: {thread_id}] Browser scraping failed: {e}", exc_info=True)
                return {"markdown": None, "extracted_content": None, "error": str(e), "success": False}
            finally:
                # Clean up Steel session
                if session:
                    try:
                        client.sessions.release(session.id)
                        logger.info(f"[PID: {process_id} | Thread: {thread_id}] Released Steel Session ID: {session.id}")
                    except Exception as e:
                        logger.error(f"[PID: {process_id} | Thread: {thread_id}] Failed to release session {session.id}: {e}")
        
        # Submit to thread pool
        future = _browser_executor.submit(_setup_and_run_crawler)
        return future.result()



atexit.register(_browser_executor.shutdown, wait=True)
