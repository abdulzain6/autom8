import atexit
import asyncio
import concurrent.futures
import io
import os
import threading
import time
import redis
import json
import jsonschema
from sqlalchemy.orm import Session
from jsonschema import validate, ValidationError
from redis_semaphore import Semaphore
from typing import Any, Optional
from aci.common.db.sql_models import LinkedAccount
from aci.common.logging_setup import get_logger
from aci.common.schemas.security_scheme import NoAuthScheme, NoAuthSchemeCredentials
from aci.common.utils import create_db_session
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
from playwright.async_api import async_playwright

from aci.server.file_management import FileManager


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
    stale_client_timeout=600,  # 10 minutes
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
        self.user_id = linked_account.user_id
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
                    base_url=config.SKYVERN_BASE_URL, api_key=config.SKYVERN_API_KEY
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
                future = asyncio.run_coroutine_threadsafe(
                    _run_skyvern(), _event_loop_manager.loop
                )
                result = future.result(timeout=300)

            # Small delay to prevent connection conflicts when semaphore is released
            time.sleep(1)
            return result
        else:
            # Use browser-use with Steel
            # This nested function encapsulates the entire lifecycle of a single browser task.
            # It's what will be executed by a thread from the thread pool.
            def _setup_and_run_agent():
                process_id = os.getpid()
                thread_id = threading.get_ident()
                logger.info(
                    f"[PID: {process_id} | Thread: {thread_id}] Starting setup for a new browser task."
                )

                # All clients and sessions are created inside this function, not outside.
                client = Steel(base_url=config.STEEL_BASE_URL)
                session = None
                try:
                    with _browser_semaphore:
                        # 1. Create a new remote browser session for this task only.
                        assert (
                            config.HTTP_PROXY is not None
                        ), "HTTP_PROXY must be set in config"
                        disable_fingerprint = any(
                            site in task for site in DISABLE_FINGERPRINT_SITES
                        )

                        stealth_settings = {
                            "humanize_interactions": True,
                            "skip_fingerprint_injection": disable_fingerprint,
                        }
                        logger.info(
                            f"[PID: {process_id} | Thread: {thread_id}] Stealth settings configured: {stealth_settings}"
                        )

                        session = client.sessions.create(block_ads=True, use_proxy=True, proxy_url=config.HTTP_PROXY, stealth_config=stealth_settings)  # type: ignore
                        cdp_url = f"ws://{config.STEEL_BASE_URL.replace('http://', '').replace('https://', '')}?sessionId={session.id}"

                        logger.info(
                            f"[PID: {process_id} | Thread: {thread_id}] Created unique Steel Session ID: {session.id}"
                        )

                        # LLM and Agent configuration
                        api_key = config.OPENROUTER_API_KEY
                        llm = ChatOpenAI(
                            model="x-ai/grok-4-fast",
                            temperature=0.3,
                            api_key=api_key,
                            base_url=config.OPENROUTER_BASE_URL,
                        )
                        page_extraction_llm = ChatOpenAI(
                            model="openai/gpt-oss-120b",
                            temperature=0.3,
                            api_key=config.DEEPINFRA_API_KEY,
                            base_url=config.DEEPINFRA_BASE_URL,
                            reasoning_effort="minimal",
                        )

                        # 2. Create a new BrowserSession instance for this task only.
                        browser_session_for_this_agent = BrowserSession(cdp_url=cdp_url)
                        logger.info(
                            f"[PID: {process_id} | Thread: {thread_id}] Created unique BrowserSession object ID: {id(browser_session_for_this_agent)}"
                        )

                        # 3. Create a new Agent instance tied to the unique session.
                        # NOTE: Set use_vision=False as the Groq model doesn't support it.
                        agent = Agent(
                            task=task,
                            llm=llm,
                            browser_session=browser_session_for_this_agent,
                            flash_mode=True,
                            extend_system_message="Be super quick and use as few actions as possible. If you encounter a captcha, report 'captcha encountered' and end.",
                            use_thinking=False,
                            use_vision=False,  # Corrected based on logs
                            display_files_in_done_text=True,
                            images_per_step=1,
                            page_extraction_llm=page_extraction_llm,
                        )
                        agent.settings.use_vision = True

                        logger.info(
                            f"[PID: {process_id} | Thread: {thread_id}] Created unique Agent object ID: {id(agent)}"
                        )

                        # The async part of the task
                        async def _run_agent_async():
                            result = await agent.run(max_steps=7)
                            return result.final_result() if result else None

                        # 4. Safely submit the async task to the shared event loop.
                        future = asyncio.run_coroutine_threadsafe(
                            _run_agent_async(), _event_loop_manager.loop
                        )
                        result = future.result(timeout=300)  # 5 minute timeout

                        logger.info(
                            f"[PID: {process_id} | Thread: {thread_id}] Task finished successfully."
                        )
                        return {"result": result, "success": True}

                except Exception as e:
                    logger.error(
                        f"[PID: {process_id} | Thread: {thread_id}] Browser automation failed: {e}",
                        exc_info=True,
                    )
                    raise RuntimeError(f"Browser automation failed: {str(e)}")
                finally:
                    # 5. Always clean up and release the remote browser session.
                    if session:
                        try:
                            client.sessions.release(session.id)
                            logger.info(
                                f"[PID: {process_id} | Thread: {thread_id}] Released Steel Session ID: {session.id}"
                            )
                            # Small delay to prevent connection conflicts when new sessions are created
                            time.sleep(1)
                        except Exception as e:
                            logger.error(
                                f"[PID: {process_id} | Thread: {thread_id}] Failed to release session {session.id}: {e}"
                            )

            # Submit the entire encapsulated function to the thread pool.
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

            client = Steel(base_url=config.STEEL_BASE_URL)
            session = None

            try:
                with _browser_semaphore:
                    # Create Steel browser session
                    assert (
                        config.HTTP_PROXY is not None
                    ), "HTTP_PROXY must be set in config"

                    disable_fingerprint = any(
                        site in url for site in DISABLE_FINGERPRINT_SITES
                    )
                    stealth_settings = {
                        "humanize_interactions": True,
                        "skip_fingerprint_injection": disable_fingerprint,
                    }

                    session = client.sessions.create(
                        block_ads=True,
                        use_proxy=True,
                        proxy_url=config.HTTP_PROXY,
                        stealth_config=stealth_settings,  # type: ignore
                    )

                    # Build CDP URL for Steel browser
                    cdp_url = f"ws://{config.STEEL_BASE_URL.replace('http://', '').replace('https://', '')}?sessionId={session.id}"
                    logger.info(
                        f"[PID: {process_id} | Thread: {thread_id}] Created Steel Session ID: {session.id}"
                    )

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
                    future = asyncio.run_coroutine_threadsafe(
                        _run_crawler_async(), _event_loop_manager.loop
                    )
                    crawler_result = future.result(timeout=300)  # 5 minute timeout

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
                # Clean up Steel session
                if session:
                    try:
                        client.sessions.release(session.id)
                        logger.info(
                            f"[PID: {process_id} | Thread: {thread_id}] Released Steel Session ID: {session.id}"
                        )
                        # Small delay to prevent connection conflicts when new sessions are created
                        time.sleep(1)
                    except Exception as e:
                        logger.error(
                            f"[PID: {process_id} | Thread: {thread_id}] Failed to release session {session.id}: {e}"
                        )

        # Submit to thread pool
        future = _browser_executor.submit(_setup_and_run_crawler)
        return future.result()

    def run_js_and_return_result(
        self, url: str, js_code: str, output_filename: Optional[str] = None
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

            client = Steel(base_url=config.STEEL_BASE_URL)
            session = None

            try:
                with _browser_semaphore:
                    session = client.sessions.create(block_ads=True, use_proxy=True, proxy_url=config.HTTP_PROXY)  # type: ignore
                    cdp_url = f"ws://{config.STEEL_BASE_URL.replace('http://', '').replace('https://', '')}?sessionId={session.id}"
                    logger.info(
                        f"[PID: {process_id} | Thread: {thread_id}] Created Steel Session ID: {session.id}"
                    )

                    async def _run_js_in_browser():
                        async with async_playwright() as p:
                            browser = await p.chromium.connect_over_cdp(cdp_url)
                            context = browser.contexts[0]
                            page = context.pages[0]
                            await page.goto(url, wait_until="networkidle")

                            # Execute the JS code directly. Playwright will raise an
                            # exception if the JS code fails, which will be caught below.
                            result = await page.evaluate(js_code)
                            return result

                    future = asyncio.run_coroutine_threadsafe(
                        _run_js_in_browser(), _event_loop_manager.loop
                    )
                    result = future.result(timeout=120)

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
                if session:
                    try:
                        client.sessions.release(session.id)
                        logger.info(
                            f"[PID: {process_id} | Thread: {thread_id}] Released Steel Session ID: {session.id}"
                        )
                        time.sleep(1)
                    except Exception as e:
                        logger.error(
                            f"[PID: {process_id} | Thread: {thread_id}] Failed to release session {session.id}: {e}"
                        )

        future = _browser_executor.submit(_setup_and_run_js)
        return future.result()


atexit.register(_browser_executor.shutdown, wait=True)
