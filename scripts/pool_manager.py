import asyncio
import httpx
import redis.asyncio as redis
from playwright.async_api import async_playwright

# --- Configuration ---
# This is the main application. It manages a pool of browser workers
# and processes jobs from the Redis queue concurrently.

REDIS_HOST = "localhost"
REDIS_PORT = 6379
JOB_QUEUE_NAME = "browser_job_queue"

# For your current setup with --net=host, localhost works.
# When you scale to Swarm/K8s, these will be the individual pod IPs.
WORKER_ADDRESSES = [
    "http://localhost:6000"
    # Add more addresses here if you run multiple instances on different ports
    # "http://localhost:6001",
    # "http://localhost:6002",
]

class BrowserPool:
    """Manages a pool of available browser worker addresses."""
    def __init__(self, addresses):
        self._pool = asyncio.Queue()
        for addr in addresses:
            self._pool.put_nowait(addr)
        print(f"✅ Browser pool initialized with {len(addresses)} workers.")

    async def acquire(self):
        """Gets an available worker address, waiting if none are free."""
        print("Waiting for an available browser worker...")
        addr = await self._pool.get()
        print(f"  -> Acquired worker: {addr}")
        return addr

    def release(self, addr):
        """Returns a worker address to the pool, making it free again."""
        self._pool.put_nowait(addr)
        print(f"  -> Released worker: {addr}")

async def run_browser_task(worker_address: str, url: str):
    """
    This function represents a single, complete task performed by a worker.
    It forks, connects, performs the work, and shuts down a browser.
    """
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            # 1. Fork a new browser instance for this task
            print(f"[{worker_address}] Forking browser for URL: {url}")
            fork_resp = await client.post(f"{worker_address}/fork")
            fork_resp.raise_for_status()

            # 2. Get the connection details
            version_resp = await client.get(f"{worker_address}/json/version")
            version_resp.raise_for_status()
            ws_url = version_resp.json()["webSocketDebuggerUrl"]

        # 3. Connect and do the work with Playwright
        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(ws_url)
            page = await browser.new_page()

            print(f"[{worker_address}] Navigating to {url}...")
            await page.goto(url, timeout=60000)
            title = await page.title()
            print(f"✅ [{worker_address}] SUCCESS! Title: '{title}'")

            await browser.close()

    except Exception as e:
        print(f"❌ [{worker_address}] ERROR processing {url}: {e}")

    finally:
        # 4. CRITICAL: Always shut down the browser process to free resources.
        # The rust server's /shutdown endpoint kills ALL browsers it manages on that worker.
        # Since we are forking one-per-task, this is the correct way to clean up.
        async with httpx.AsyncClient() as client:
            print(f"[{worker_address}] Shutting down browser instance(s)...")
            try:
                await client.post(f"{worker_address}/shutdown")
            except httpx.RequestError as e:
                print(f"⚠️  Could not shut down browser on worker {worker_address}: {e}")


async def worker(name: str, pool: BrowserPool, redis_conn: redis.Redis):
    """A worker process that continuously takes jobs and executes them."""
    print(f"[{name}] Starting...")
    while True:
        try:
            # 1. Wait for a job to appear in the Redis queue
            # BLPOP is a blocking pop, it will wait until a job is available.
            print(f"[{name}] Waiting for a job from Redis...")
            # FIX: blpop expects a list of keys, even if there's only one.
            job_data = await redis_conn.blpop([JOB_QUEUE_NAME])
            
            # job_data is a tuple: (b'queue_name', b'url')
            if job_data:
                job_url = job_data[1].decode('utf-8')
            else:
                continue

            # 2. Acquire a browser worker from our pool
            worker_address = await pool.acquire()

            # 3. Run the task
            try:
                await run_browser_task(worker_address, job_url)
            finally:
                # 4. ALWAYS release the worker back to the pool
                pool.release(worker_address)

        except Exception as e:
            print(f"[{name}] An unexpected error occurred in the worker loop: {e}")
            # Avoid rapid-fire loops on critical errors
            await asyncio.sleep(5)


async def main():
    """Sets up the pool and starts the concurrent workers."""
    try:
        redis_conn = redis.from_url(f"redis://{REDIS_HOST}:{REDIS_PORT}")
        await redis_conn.ping()
        print("✅ Connected to Redis for the main pool manager.")
    except redis.exceptions.ConnectionError as e:
        print(f"❌ Main app could not connect to Redis: {e}")
        return

    pool = BrowserPool(WORKER_ADDRESSES)

    # Create worker tasks. The number of tasks should match the number of workers.
    # This ensures one task is always running per available browser.
    worker_tasks = []
    for i, _ in enumerate(WORKER_ADDRESSES):
        task = asyncio.create_task(worker(f"Worker-{i+1}", pool, redis_conn))
        worker_tasks.append(task)

    print(f"\n🚀 Started {len(worker_tasks)} workers. Waiting for jobs... Press Ctrl+C to exit.")
    await asyncio.gather(*worker_tasks)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutting down...")

