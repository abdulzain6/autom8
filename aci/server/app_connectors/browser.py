import atexit
import asyncio
import concurrent.futures
import io
import os
import socket
import threading
import time
import json
import jsonschema
import requests
import re
from urllib.parse import urlparse
from patchright.async_api import async_playwright
from playwright_stealth import stealth_async
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
from skyvern import Skyvern
from crawl4ai import AsyncWebCrawler
from crawl4ai.async_configs import BrowserConfig, CrawlerRunConfig, CacheMode, LLMConfig
from crawl4ai import UndetectedAdapter
from crawl4ai.async_crawler_strategy import AsyncPlaywrightCrawlerStrategy
from crawl4ai.extraction_strategy import LLMExtractionStrategy
from aci.server.browser_pool import ResilientBrowserPool
from aci.server.file_management import FileManager
from redis import Redis as SyncRedis


logger = get_logger(__name__)

_pool: Optional[ResilientBrowserPool] = None
_pool_lock = threading.Lock()


def get_pool() -> ResilientBrowserPool:
    """Initializes and returns the global browser pool instance."""
    global _pool
    with _pool_lock:
        if _pool is None:
            # Assumes config has BROWSER_SERVICE_NAME and BROWSER_POOL_REFRESH_INTERVAL
            redis_client = SyncRedis.from_url(config.REDIS_URL)
            _pool = ResilientBrowserPool(
                redis_client=redis_client,
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

# Known Docker Swarm services that should be allowed
ALLOWED_DOCKER_SERVICES = {
    'caddy', 'server', 'huey_worker', 'livekit', 'voice_agent',
    'gotenberg', 'code-executor', 'searxng', 'cycletls-server',
    'steel-browser-api', 'headless-browser', 'local-proxy',
    'skyvern', 'skyvern-ui', 'postgres', 'redis'
}


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

    def _validate_url_security(self, url: str) -> None:
        """
        Validates URLs for security to prevent attacks on local files and internal services.

        Args:
            url (str): The URL to validate

        Raises:
            ValueError: If the URL is deemed unsafe
        """
        if not url or not isinstance(url, str):
            raise ValueError("URL must be a non-empty string")

        try:
            parsed = urlparse(url)
        except Exception as e:
            raise ValueError(f"Invalid URL format: {str(e)}")

        # Block file:// scheme (local file access)
        if parsed.scheme.lower() == "file":
            raise ValueError("Access to local files (file://) is not allowed")

        # Block other dangerous schemes
        dangerous_schemes = {"ftp", "ftps", "data", "javascript", "vbscript", "blob"}
        if parsed.scheme.lower() in dangerous_schemes:
            raise ValueError(f"Dangerous URL scheme not allowed: {parsed.scheme}")

        # Block localhost and internal IP addresses
        if parsed.hostname:
            hostname_lower = parsed.hostname.lower()

            # Block localhost variations
            localhost_patterns = [
                "localhost",
                "127.0.0.1",
                "127.0.0.0/8",
                "::1",
                "0:0:0:0:0:0:0:1",
            ]

            for pattern in localhost_patterns:
                if hostname_lower == pattern or hostname_lower.startswith(pattern):
                    raise ValueError(
                        "Access to localhost/internal services is not allowed"
                    )

            # Block private IP ranges
            private_ip_patterns = [
                r"^10\.",  # 10.0.0.0/8
                r"^172\.(1[6-9]|2[0-9]|3[0-1])\.",  # 172.16.0.0/12
                r"^192\.168\.",  # 192.168.0.0/16
                r"^169\.254\.",  # Link-local
                r"^fc00:",  # IPv6 private
                r"^fe80:",  # IPv6 link-local
                r"^::1$",  # IPv6 localhost
            ]

            for pattern in private_ip_patterns:
                if re.match(pattern, hostname_lower):
                    raise ValueError(
                        "Access to private/internal IP addresses is not allowed"
                    )

            # Block common internal service hostnames
            internal_hostnames = [
                "internal",
                "api.internal",
                "service.internal",
                "db",
                "database",
                "redis",
                "postgres",
                "mysql",
                "elasticsearch",
                "kibana",
                "grafana",
                "prometheus",
                "jenkins",
                "gitlab",
                "github.internal",
                "docker.internal",
                "kubernetes.internal",
            ]

            if hostname_lower in internal_hostnames:
                raise ValueError("Access to internal services is not allowed")
                
            # Allow known Docker Swarm services
            if hostname_lower in ALLOWED_DOCKER_SERVICES:
                logger.info(f"Allowing access to known Docker service: {hostname_lower}")
                return  # Skip further validation for known services
                
            # Additional check: DNS resolution for suspicious hostnames
            # Be suspicious of hostnames with no dots (might be localhost-like) or very short names
            is_suspicious_hostname = (
                '.' not in hostname_lower or  # No dots at all (localhost, server, etc.)
                len(hostname_lower.split('.')[0]) <= 2  # Very short first part (db, api, etc.)
            )
            
            if is_suspicious_hostname and len(hostname_lower) > 0:
                if self._check_dns_for_internal_ip(hostname_lower):
                    raise ValueError("Access to internal services is not allowed (detected via DNS resolution)")

        # Additional security checks
        # Block URLs with suspicious patterns
        suspicious_patterns = [
            r"\.\.",  # Directory traversal
            r"%2e%2e",  # URL encoded ..
            r"%2f%2f",  # URL encoded //
            r"\\",  # Backslashes
        ]

        for pattern in suspicious_patterns:
            if re.search(pattern, url, re.IGNORECASE):
                raise ValueError(
                    "URL contains suspicious patterns that are not allowed"
                )

    def _validate_filename_security(self, filename: str) -> None:
        """
        Validates filenames to prevent path traversal and other attacks.

        Args:
            filename (str): The filename to validate

        Raises:
            ValueError: If the filename is deemed unsafe
        """
        if not filename or not isinstance(filename, str):
            raise ValueError("Filename must be a non-empty string")

        # Remove any path separators and check for traversal
        if ".." in filename or "/" in filename or "\\" in filename:
            raise ValueError(
                "Filename contains path traversal characters that are not allowed"
            )

        # Check for other suspicious characters
        suspicious_chars = ["<", ">", ":", "*", "?", '"', "|"]
        for char in suspicious_chars:
            if char in filename:
                raise ValueError(
                    f"Filename contains suspicious character '{char}' that is not allowed"
                )

        # Check file extension is reasonable
        allowed_extensions = {
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".pdf",
            ".json",
            ".txt",
            ".csv",
            ".html",
        }
        if "." in filename:
            ext = filename[filename.rfind(".") :].lower()
            if ext not in allowed_extensions:
                raise ValueError(f"File extension '{ext}' is not allowed")
        elif not filename.lower().endswith(".png"):  # Default for screenshots
            # Allow filenames without extension for some cases
            pass

    def _check_dns_for_internal_ip(self, hostname: str) -> bool:
        """
        Performs DNS resolution to check if a hostname resolves to internal/private IP addresses.
        
        Args:
            hostname (str): The hostname to check
            
        Returns:
            bool: True if the hostname resolves to internal/private IPs, False otherwise
        """
        try:
            # Set a short timeout for DNS resolution to avoid blocking
            socket.setdefaulttimeout(2.0)
            
            # Try to resolve both IPv4 and IPv6
            try:
                ipv4_info = socket.getaddrinfo(hostname, None, socket.AF_INET)
                ipv4_addresses = [info[4][0] for info in ipv4_info]
            except socket.gaierror:
                ipv4_addresses = []
                
            try:
                ipv6_info = socket.getaddrinfo(hostname, None, socket.AF_INET6)
                ipv6_addresses = [info[4][0] for info in ipv6_info]
            except socket.gaierror:
                ipv6_addresses = []
                
            all_addresses = ipv4_addresses + ipv6_addresses
            
            if not all_addresses:
                # If we can't resolve, assume it's safe (fail open for DNS issues)
                logger.warning(f"DNS resolution failed for {hostname}, allowing access")
                return False
                
            # Check if any resolved IP is in private ranges
            private_ip_patterns = [
                r"^10\.",  # 10.0.0.0/8
                r"^172\.(1[6-9]|2[0-9]|3[0-1])\.",  # 172.16.0.0/12
                r"^192\.168\.",  # 192.168.0.0/16
                r"^169\.254\.",  # Link-local
                r"^127\.",  # Loopback
                r"^fc00:",  # IPv6 private
                r"^fe80:",  # IPv6 link-local
                r"^::1$",  # IPv6 localhost
            ]
            
            for ip in all_addresses:
                ip_str = str(ip).lower()
                for pattern in private_ip_patterns:
                    if re.match(pattern, ip_str):
                        logger.warning(f"Hostname {hostname} resolves to internal IP {ip}, blocking access")
                        return True
                        
            logger.info(f"Hostname {hostname} resolves to public IPs: {all_addresses[:3]}...")
            return False
            
        except Exception as e:
            # If DNS resolution fails for any reason, log and allow access
            # This prevents blocking legitimate sites due to DNS issues
            logger.warning(f"DNS check failed for {hostname}: {e}, allowing access")
            return False

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

            ws_url = ws_url.replace("0.0.0.0", config.BROWSER_SERVICE_NAME)

            logger.info(f"CDP URL from worker {worker_address}: {ws_url}")

            time.sleep(4)

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
        # Basic security validation for task string
        if not task or not isinstance(task, str):
            raise ValueError("Task must be a non-empty string")

        # Check for obviously dangerous patterns in task
        dangerous_patterns = [
            r"file://",  # File URLs
            r"localhost[:/]",  # Localhost references
            r"127\.0\.0\.1",  # Local IP
            r"10\.\d+\.\d+\.\d+",  # Private IP range 10.x.x.x
            r"172\.(1[6-9]|2\d|3[01])\.\d+\.\d+",  # Private IP range 172.16-31.x.x
            r"192\.168\.\d+\.\d+",  # Private IP range 192.168.x.x
            r"internal\.|\.internal",  # Internal domains
            r"docker\.internal",  # Docker internal
            r"kubernetes\.internal",  # Kubernetes internal
        ]

        task_lower = task.lower()
        for pattern in dangerous_patterns:
            if re.search(pattern, task_lower):
                raise ValueError(
                    f"Task contains potentially dangerous content that is not allowed: {pattern}"
                )

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
                    worker_address = pool.acquire(timeout=600)
                    if worker_address is None:
                        raise RuntimeError(
                            "Failed to acquire browser worker within timeout"
                        )
                    cdp_url = self._get_cdp_url_from_worker(worker_address)

                    async with async_playwright() as p:
                        # 1️⃣ Connect to existing CDP browser
                        browser = await p.chromium.connect_over_cdp(cdp_url)
                        contexts = browser.contexts
                        context = (
                            contexts[0] if contexts else await browser.new_context()
                        )

                        page = await context.new_page()
                        # 2️⃣ Apply stealth
                        await stealth_async(page)  # type: ignore
                        logger.info(
                            f"Connected to CDP browser with stealth. Proxy={config.HTTP_PROXY}"
                        )

                        if not config.HTTP_PROXY:
                            raise ValueError(
                                "HTTP_PROXY must be set in config for CDP workers"
                            )

                        # 4️⃣ Init LLMs
                        llm = ChatOpenAI(
                            model="moonshotai/Kimi-K2-Instruct-0905",
                            temperature=0.3,
                            api_key=config.DEEPINFRA_API_KEY,
                            base_url=config.DEEPINFRA_BASE_URL,
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
                            use_vision=False,  # Change later if needed
                            display_files_in_done_text=True,
                            images_per_step=1,
                            page_extraction_llm=page_extraction_llm,
                        )

                        result = await asyncio.wait_for(
                            agent.run(max_steps=7), timeout=600
                        )
                        logger.info("Task completed successfully.")
                        await browser.close()

                        return {
                            "result": result.final_result() if result else None,
                            "success": True,
                        }

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
        # Validate URL security before proceeding
        self._validate_url_security(url)

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
                worker_address = pool.acquire(timeout=600)
                if worker_address is None:
                    raise RuntimeError(
                        "Failed to acquire browser worker within timeout"
                    )
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
                        "reasoning_effort": "none",
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
                        result: Any = await crawler.arun(url=url, config=crawl_config)

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
        # Validate URL security before proceeding
        self._validate_url_security(url)

        # Basic JavaScript security validation
        if not js_code or not isinstance(js_code, str):
            raise ValueError("JavaScript code must be a non-empty string")

        # Check for dangerous JavaScript patterns
        dangerous_js_patterns = [
            r"import\s*\(",  # Dynamic imports
            r"require\s*\(",  # Node.js require
            r"process\.",  # Node.js process access
            r"__dirname",  # Node.js directory access
            r"__filename",  # Node.js filename access
            r"child_process",  # Child process execution
            r"fs\.",  # File system access
            r"exec\s*\(",  # Command execution
            r"eval\s*\(",  # Code evaluation
            r"Function\s*\(",  # Function constructor
            r'setTimeout\s*\(\s*["\'][^"\']*["\']\s*,',  # setTimeout with string code
            r'setInterval\s*\(\s*["\'][^"\']*["\']\s*,',  # setInterval with string code
        ]

        js_code_lower = js_code.lower()
        for pattern in dangerous_js_patterns:
            if re.search(pattern, js_code_lower):
                raise ValueError(
                    f"JavaScript code contains potentially dangerous patterns that are not allowed: {pattern}"
                )

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
                worker_address = pool.acquire(timeout=600)
                if worker_address is None:
                    raise RuntimeError(
                        "Failed to acquire browser worker within timeout"
                    )

                cdp_url = self._get_cdp_url_from_worker(worker_address)

                async def _run_js_in_browser():
                    async with async_playwright() as p:
                        browser = await p.chromium.connect_over_cdp(cdp_url)
                        context = browser.contexts[0]
                        page = context.pages[0]

                        await stealth_async(page)  # type: ignore
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
                    # Validate filename security before proceeding
                    self._validate_filename_security(output_filename)
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

    def take_screenshot(
        self,
        url: str,
        output_filename: str,
        full_page: bool = False,
        delay_seconds: float = 0.0,
    ) -> dict:
        """
        Takes a screenshot of a webpage and saves it as an artifact.

        Args:
            url (str): The URL of the webpage to screenshot.
            output_filename (str): The desired filename for the screenshot artifact.
            full_page (bool): Whether to capture the full page or just the viewport.
            delay_seconds (float): Optional delay in seconds to wait after page load.

        Returns:
            dict: Contains the artifact ID of the saved screenshot.
        """
        # Validate URL security before proceeding
        self._validate_url_security(url)

        logger.info(f"Taking screenshot of URL: {url}")

        def _setup_and_take_screenshot():
            process_id = os.getpid()
            thread_id = threading.get_ident()
            logger.info(
                f"[PID: {process_id} | Thread: {thread_id}] Starting screenshot setup."
            )

            pool = get_pool()
            worker_address = None
            try:
                worker_address = pool.acquire(timeout=600)
                if worker_address is None:
                    raise RuntimeError(
                        "Failed to acquire browser worker within timeout"
                    )

                cdp_url = self._get_cdp_url_from_worker(worker_address)

                async def _take_screenshot_async():
                    async with async_playwright() as p:
                        browser = await p.chromium.connect_over_cdp(cdp_url)
                        context = browser.contexts[0]
                        page = context.pages[0]

                        await stealth_async(page)  # type: ignore
                        await page.goto(url, wait_until="load", timeout=60000)

                        # Optional delay for dynamic content loading
                        if delay_seconds > 0:
                            await asyncio.sleep(delay_seconds)

                        # Take screenshot
                        screenshot_bytes = await page.screenshot(full_page=full_page)
                        return screenshot_bytes

                screenshot_data = asyncio.run(_take_screenshot_async())

                # Save screenshot as artifact
                # Validate filename security before proceeding
                self._validate_filename_security(output_filename)
                filename = output_filename
                if not filename.lower().endswith(".png"):
                    filename = filename + ".png"

                db: Optional[Session] = None
                try:
                    db = create_db_session(config.DB_FULL_URL)
                    file_manager = FileManager(db)

                    file_buffer = io.BytesIO(screenshot_data)
                    file_buffer.seek(0)

                    new_artifact_id = file_manager.upload_artifact(
                        file_object=file_buffer,
                        filename=filename,
                        ttl_seconds=24 * 3600 * 7,  # 7 days
                        user_id=self.user_id,
                        run_id=self.run_id,
                    )
                    logger.info(
                        f"Screenshot successfully saved to artifact {new_artifact_id}."
                    )
                    return {"success": True, "artifact_id": new_artifact_id}
                finally:
                    if db:
                        db.close()

            except Exception as e:
                logger.error(
                    f"[PID: {process_id} | Thread: {thread_id}] Screenshot failed: {e}",
                    exc_info=True,
                )
                raise RuntimeError(f"Screenshot capture failed: {str(e)}")
            finally:
                if worker_address:
                    self._shutdown_worker_browsers(worker_address)
                    pool.release(worker_address)

        future = _browser_executor.submit(_setup_and_take_screenshot)
        return future.result()


atexit.register(_browser_executor.shutdown, wait=True)
