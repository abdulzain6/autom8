import atexit
import asyncio
import concurrent.futures
import io
import os
import threading
import time
import json
import jsonschema
from playwright_stealth import stealth_async
import requests
from sqlalchemy.orm import Session
from jsonschema import ValidationError
from typing import Any, Optional
from aci.common.db.sql_models import LinkedAccount
from aci.common.logging_setup import get_logger
from aci.common.schemas.security_scheme import NoAuthScheme, NoAuthSchemeCredentials
from aci.common.utils import create_db_session
from aci.server import config
from aci.server.app_connectors.base import AppConnectorBase
from browser_use import Agent, BrowserSession
from browser_use.llm import ChatOpenAI
from playwright.async_api import Browser as PlaywrightBrowser
from skyvern import Skyvern
from crawl4ai import AsyncWebCrawler
from crawl4ai.async_configs import BrowserConfig, CrawlerRunConfig, CacheMode, LLMConfig
from crawl4ai import UndetectedAdapter
from crawl4ai.async_crawler_strategy import AsyncPlaywrightCrawlerStrategy
from crawl4ai.extraction_strategy import LLMExtractionStrategy
from playwright.async_api import async_playwright
from aci.server.browser_pool import ResilientBrowserPool
from aci.server.file_management import FileManager


logger = get_logger(__name__)

_pool: Optional[ResilientBrowserPool] = None
_pool_lock = threading.Lock()


def get_pool() -> ResilientBrowserPool:
    """Initializes and returns the global browser pool instance."""
    global _pool
    with _pool_lock:
        if _pool is None:
            # Assumes config has BROWSER_SERVICE_NAME and BROWSER_POOL_REFRESH_INTERVAL
            _pool = ResilientBrowserPool(
                service_name=config.BROWSER_SERVICE_NAME,
                refresh_interval=config.BROWSER_POOL_REFRESH_INTERVAL,
            )
            _pool.start()
    return _pool


@atexit.register
def _shutdown_pool():
    """Ensures the pool's background thread is stopped on application exit."""
    if _pool:
        _pool.stop()


# Thread pool for executing synchronous browser tasks
_browser_executor = concurrent.futures.ThreadPoolExecutor(
    thread_name_prefix="browser-automation"
)
atexit.register(_browser_executor.shutdown, wait=True)


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
        self.user_id = linked_account.user_id
        logger.info("Browser connector initialized.")

    def _before_execute(self) -> None:
        pass

    def _get_cdp_url_from_worker(self, worker_address: str) -> str:
        """Helper to fork and get CDP URL from a worker."""
        with requests.Session() as session:
            # 1. Fork a new browser on the worker
            fork_resp = session.post(f"{worker_address}/fork", timeout=60)
            fork_resp.raise_for_status()

            # 2. Get the connection details
            version_resp = session.get(f"{worker_address}/json/version", timeout=15)
            version_resp.raise_for_status()
            ws_url = version_resp.json()["webSocketDebuggerUrl"]
            return ws_url

    def _shutdown_worker_browsers(self, worker_address: str):
        """Helper to shut down all browsers on a specific worker."""
        try:
            requests.post(f"{worker_address}/shutdown", timeout=15)
            logger.info(f"Successfully shut down browsers on worker {worker_address}")
        except requests.RequestException as e:
            logger.error(f"Failed to shut down browsers on {worker_address}: {e}")

    def run_browser_automation(self, task: str) -> dict:
        """
        Runs a brower automation task concurrently and safely.
        """
        logger.info(f"Submitting browser task to executor: {task[:100]}...")

        if config.USE_SKYVERN:
            # Use Skyvern for browser automation
            async def _run_skyvern_task():
                skyvern = Skyvern(
                    base_url=config.SKYVERN_BASE_URL, api_key=config.SKYVERN_API_KEY
                )
                task_result = await skyvern.run_task(
                    prompt=task,
                    wait_for_completion=True,
                    max_steps=10,
                    model={"reasoning": {"enabled": False}},
                )
                return {"result": task_result.output, "success": True}

            async def _run_skyvern_with_timeout():
                try:
                    # Wrap the task with a 5-minute timeout
                    return await asyncio.wait_for(_run_skyvern_task(), timeout=300)
                except Exception as e:
                    logger.error(f"Skyvern task failed: {e}", exc_info=True)
                    return {"result": None, "error": str(e), "success": False}

            result = asyncio.run(_run_skyvern_with_timeout())
            # Small delay to prevent connection conflicts when semaphore is released
            time.sleep(1)
            return result
        

        def _setup_and_run_agent():
            async def _run_agent():
                worker_address = None
                try:
                    pool = get_pool()
                    worker_address = pool.acquire()
                    cdp_url = f"ws://{worker_address}"

                    async with async_playwright() as p:
                        # 1️⃣ Connect to existing CDP browser
                        browser = await p.chromium.connect_over_cdp(cdp_url)
                        contexts = browser.contexts
                        context = contexts[0] if contexts else await browser.new_context()

                        page = await context.new_page()
                        # 2️⃣ Apply stealth
                        await stealth_async(page)
                        logger.info(f"Connected to CDP browser with stealth. Proxy={config.HTTP_PROXY}")


                        if not config.HTTP_PROXY:
                            raise ValueError("HTTP_PROXY must be set in config for CDP workers")

                        # 4️⃣ Init LLMs
                        llm = ChatOpenAI(
                            model="x-ai/grok-4-fast",
                            temperature=0.3,
                            api_key=config.OPENROUTER_API_KEY,
                            base_url=config.OPENROUTER_BASE_URL,
                        )
                        page_extraction_llm = ChatOpenAI(
                            model="openai/gpt-oss-120b",
                            temperature=0.3,
                            api_key=config.DEEPINFRA_API_KEY,
                            base_url=config.DEEPINFRA_BASE_URL,
                            reasoning_effort="minimal",
                        )
                        browser_session_for_this_agent = BrowserSession(browser=browser)

                        # 5️⃣ Run the Agent
                        agent = Agent(
                            task=task,
                            llm=llm,
                            browser_session=browser_session_for_this_agent,
                            flash_mode=True,
                            extend_system_message="Be super quick and use as few actions as possible. If you encounter a captcha, report 'captcha encountered' and end.",
                            use_thinking=False,
                            use_vision=True,
                            display_files_in_done_text=True,
                            images_per_step=1,
                            page_extraction_llm=page_extraction_llm,
                        )

                        result = await asyncio.wait_for(agent.run(max_steps=7), timeout=300)
                        logger.info("Task completed successfully.")
                        await browser.close()

                        return {"result": result.final_result() if result else None, "success": True}

                except Exception as e:
                    logger.error(f"Browser automation failed: {e}", exc_info=True)
                    return {"result": None, "error": str(e), "success": False}
                finally:
                    if worker_address:
                        self._shutdown_worker_browsers(worker_address)
                        pool.release(worker_address)

            return asyncio.run(_run_agent())

        future = _browser_executor.submit(_setup_and_run_agent)
        return future.result()

    def scrape_with_browser(
        self, url: str, extraction_instructions: str, output_schema: dict
    ) -> dict:
        """
        Scrapes a website using crawl4ai with Steel browser via CDP and returns structured data.
        Uses LLM extraction strategy with custom instructions and validates output against provided schema.
        Uses semaphore locking to manage concurrent browser sessions.

        Args:
            url (str): The URL to scrape
            extraction_instructions (str): LLM instructions for data extraction using GPT-OSS model.
                                         Must be specific about what data to extract and how to format it.
            output_schema (dict): Required JSON schema defining the expected output structure.
                                Must be a valid JSON schema with type definitions and properties.
                                Example: {"type": "object", "properties": {"title": {"type": "string"}, "price": {"type": "number"}}}

        Returns:
            dict: Contains 'extracted_content' with validated structured data and 'success' status.
                  On validation failure, includes 'validation_errors' with details.
                  On scraping failure, includes 'error' with error message.
        """
        logger.info(f"Starting browser scraping for URL: {url}")

        # Validate the output schema before proceeding
        try:
            # Ensure schema is a valid JSON schema structure
            if not isinstance(output_schema, dict):
                raise ValueError("output_schema must be a dictionary")

            # Basic schema validation - check for required fields
            if "type" not in output_schema:
                raise ValueError("output_schema must include 'type' field")

            # Test schema validity by creating a validator
            jsonschema.Draft7Validator.check_schema(output_schema)
            logger.info(f"Output schema validation passed: {output_schema}")

        except jsonschema.SchemaError as e:
            logger.error(f"Invalid output schema provided: {e}")
            raise ValueError(f"Invalid schema: {str(e)}")

        def _setup_and_run_crawler():
            process_id = os.getpid()
            thread_id = threading.get_ident()
            logger.info(
                f"[PID: {process_id} | Thread: {thread_id}] Starting browser scraping setup."
            )

            pool = get_pool()
            worker_address = None
            try:
                worker_address = pool.acquire(timeout=300)
                cdp_url = self._get_cdp_url_from_worker(worker_address)
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
                        browser_adapter=undetected_adapter,
                    )

                    # Setup LLM extraction strategy with required schema
                    llm_config = LLMConfig(
                        provider="deepinfra/openai/gpt-oss-120b",
                        api_token=config.DEEPINFRA_API_KEY,
                        base_url=config.DEEPINFRA_BASE_URL,
                    )

                    # Build extraction strategy with required schema
                    extraction_kwargs = {
                        "llm_config": llm_config,
                        "extraction_type": "schema",
                        "instruction": extraction_instructions,
                        "schema": output_schema,
                        "overlap_rate": 0.1,
                        "chunk_token_threshold": 10000,
                        "apply_chunking": True,
                        "input_format": "markdown",
                        "reasoning_effort": "minimal",
                    }

                    logger.info(
                        f"[PID: {process_id} | Thread: {thread_id}] Using output schema: {output_schema}"
                    )
                    llm_strategy = LLMExtractionStrategy(**extraction_kwargs)

                    crawl_config = CrawlerRunConfig(
                        extraction_strategy=llm_strategy,
                        cache_mode=CacheMode.BYPASS,
                        delay_before_return_html=12,
                        max_scroll_steps=5,
                        scroll_delay=2,
                    )

                    async with AsyncWebCrawler(
                        crawler_strategy=crawler_strategy, config=browser_config
                    ) as crawler:
                        # Run the crawler with LLM extraction
                        result: Any = await crawler.arun(
                            url=url, config=crawl_config
                        )

                        logger.info(
                            f"[PID: {process_id} | Thread: {thread_id}] Crawler run completed. Result: {result.markdown}"
                        )

                        # Validate extracted content against schema
                        if result.success:
                            try:
                                # Parse JSON if it's a string
                                if isinstance(result.extracted_content, str):
                                    extracted_data = json.loads(
                                        result.extracted_content
                                    )
                                else:
                                    extracted_data = result.extracted_content

                                logger.info(
                                    f"[PID: {process_id} | Thread: {thread_id}] Extracted content validation passed"
                                )

                                return {
                                    "extracted_content": extracted_data,
                                    "validation_errors": None,
                                }

                            except json.JSONDecodeError as e:
                                logger.error(
                                    f"[PID: {process_id} | Thread: {thread_id}] JSON parsing failed: {e}"
                                )
                                return {
                                    "extracted_content": result.extracted_content,
                                    "validation_errors": f"JSON parsing failed: {str(e)}",
                                }

                            except ValidationError as e:
                                logger.error(
                                    f"[PID: {process_id} | Thread: {thread_id}] Schema validation failed: {e.message}"
                                )
                                return {
                                    "extracted_content": (
                                        extracted_data
                                        if "extracted_data" in locals()
                                        else result.extracted_content
                                    ),
                                    "validation_errors": f"Schema validation failed: {e.message}",
                                }
                        else:
                            return {
                                "extracted_content": None,
                                "validation_errors": "No content extracted from the webpage",
                            }

                # Submit to event loop and get result
                crawler_result = asyncio.run(_run_crawler_async())

                logger.info(
                    f"[PID: {process_id} | Thread: {thread_id}] Browser scraping completed successfully."
                )

                # Determine success based on validation results
                has_validation_errors = (
                    crawler_result.get("validation_errors") is not None
                )
                success = not has_validation_errors

                return {
                    "extracted_content": crawler_result.get("extracted_content"),
                    "validation_errors": crawler_result.get("validation_errors"),
                    "success": success,
                }

            except Exception as e:
                logger.error(
                    f"[PID: {process_id} | Thread: {thread_id}] Browser scraping failed: {e}",
                    exc_info=True,
                )
                raise RuntimeError(f"Browser scraping failed: {str(e)}")
            finally:
                if worker_address:
                    self._shutdown_worker_browsers(worker_address)
                    pool.release(worker_address)

        # Submit to thread pool
        future = _browser_executor.submit(_setup_and_run_crawler)
        return future.result()

    def run_js_and_return_result(
        self,
        url: str,
        js_code: str,
        output_filename: Optional[str] = None,
        delay_seconds: float = 0.0,
    ) -> dict:
        """
        Navigates to a URL, executes JavaScript, and returns the result or saves it as an artifact.
        If the JavaScript execution fails, this function will raise an exception.

        Args:
            url (str): The URL to navigate to for establishing browser context and cookies.
            js_code (str): The JavaScript code to execute. This code MUST return a value.
            output_filename (Optional[str]): If provided, the JSON result is saved as an
                                             artifact with this filename. If None, the
                                             raw data is returned directly.
            delay_seconds (float): Optional delay in seconds to wait after page load
                                 before executing JavaScript. Useful for dynamic content
                                 that needs time to load. Default is 0.0 (no delay).

        Returns:
            dict: On success, returns either the artifact ID or the raw JSON result.
        """
        logger.info(f"Submitting JS execution task for URL: {url}")

        def _setup_and_run_js():
            process_id = os.getpid()
            thread_id = threading.get_ident()
            logger.info(
                f"[PID: {process_id} | Thread: {thread_id}] Starting JS execution setup."
            )

            pool = get_pool()
            worker_address = None
            try:
                worker_address = pool.acquire(timeout=300)
                cdp_url = self._get_cdp_url_from_worker(worker_address)

                async def _run_js_in_browser():
                    async with async_playwright() as p:
                        browser = await p.chromium.connect_over_cdp(cdp_url)
                        context = browser.contexts[0]
                        page = context.pages[0]

                        await stealth_async(page)
                        await page.goto(url, wait_until="load", timeout=60000)

                        # Optional delay for dynamic content loading
                        if delay_seconds > 0:
                            await asyncio.sleep(delay_seconds)

                        # Execute the JS code directly. Playwright will raise an
                        # exception if the JS code fails, which will be caught below.
                        result = await page.evaluate(js_code)
                        return result

                result = asyncio.run(_run_js_in_browser())
                # If a filename is provided, save the result as an artifact
                if output_filename is not None:
                    filename = output_filename
                    if not filename.lower().endswith(".json"):
                        filename = filename + ".json"

                    db: Optional[Session] = None
                    try:
                        db = create_db_session(config.DB_FULL_URL)
                        file_manager = FileManager(db)

                        json_string = json.dumps(result, indent=2)
                        file_buffer = io.BytesIO(json_string.encode("utf-8"))
                        file_buffer.seek(0)

                        new_artifact_id = file_manager.upload_artifact(
                            file_object=file_buffer,
                            filename=filename,
                            ttl_seconds=24 * 3600 * 7,  # 7 days
                            user_id=self.user_id,
                            run_id=self.run_id,
                        )
                        logger.info(
                            f"JS result successfully saved to artifact {new_artifact_id}."
                        )
                        return {"success": True, "artifact_id": new_artifact_id}
                    finally:
                        if db:
                            db.close()
                else:
                    # Otherwise, return the raw data
                    content_length = len(json.dumps(result))
                    logger.info(
                        f"JS execution successful. Returning raw data. Content length: {content_length}"
                    )
                    return {
                        "success": True,
                        "result": result,
                        "content_length": content_length,
                    }

            except Exception as e:
                # Catch any error (including JS errors from Playwright) and re-raise
                logger.error(
                    f"[PID: {process_id} | Thread: {thread_id}] An exception occurred: {e}",
                    exc_info=True,
                )
                raise RuntimeError(
                    f"Browser automation or JS execution failed: {str(e)}"
                )
            finally:
                if worker_address:
                    self._shutdown_worker_browsers(worker_address)
                    pool.release(worker_address)

        future = _browser_executor.submit(_setup_and_run_js)
        return future.result()


atexit.register(_browser_executor.shutdown, wait=True)
