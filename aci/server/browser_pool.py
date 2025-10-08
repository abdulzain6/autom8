import socket
import requests
import threading
from queue import Queue, Empty
from aci.common.utils import get_logger


logger = get_logger(__name__)


class ResilientBrowserPool:
    """
    Manages a dynamic and self-healing pool of browser worker addresses.
    This is a synchronous, thread-safe version.

    It automatically discovers workers, periodically refreshes the list in a
    background thread, and performs health checks to ensure reliability.
    """

    def __init__(self, service_name: str, refresh_interval: int = 30):
        self._service_name = service_name
        self._refresh_interval = refresh_interval

        self._pool = Queue()  # Thread-safe queue for 'free' workers
        self._active_workers = set()  # Holds all known 'live' workers

        self._refresh_thread = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()  # To protect access to _active_workers

    def start(self):
        """Initializes the pool and starts the background refresh thread."""
        logger.info("Starting resilient browser pool...")
        self._discover_and_update()

        self._refresh_thread = threading.Thread(
            target=self._periodic_refresh, daemon=True
        )
        self._refresh_thread.start()
        logger.info(
            f"✅ Pool started. Will refresh worker list every {self._refresh_interval} seconds."
        )

    def stop(self):
        """Stops the background refresh thread gracefully."""
        logger.info("Stopping browser pool...")
        self._stop_event.set()
        if self._refresh_thread:
            self._refresh_thread.join()
        logger.info("Browser pool stopped.")

    def _discover_workers(self) -> set[str]:
        """Dynamically finds worker IPs using synchronous DNS service discovery."""
        discovered_addresses = set()
        dns_name = f"{self._service_name}"
        try:
            # Synchronous DNS lookup
            resolved_hosts = socket.getaddrinfo(
                dns_name, 6000, family=socket.AF_INET, type=socket.SOCK_STREAM
            )
            logger.info(f"DNS discovery: Found {len(resolved_hosts)} hosts for '{dns_name}'.")
            for host in resolved_hosts:
                ip = host[4][0]
                discovered_addresses.add(f"http://{ip}:6000")
        except socket.gaierror:
            logger.info(f"DNS discovery: No hosts found for '{dns_name}'.")
        except Exception as e:
            logger.info(f"An unexpected error occurred during service discovery: {e}")
        return discovered_addresses

    def _is_healthy(self, addr: str) -> bool:
        """Performs a quick, synchronous health check on a worker instance."""
        try:
            # Using the requests library for blocking HTTP calls
            with requests.Session() as session:
                resp = session.get(f"{addr}/health", timeout=3.0)
                return resp.status_code == 200
        except requests.exceptions.RequestException:
            return False

    def _periodic_refresh(self):
        """The background thread's target function that periodically updates the worker list."""
        while not self._stop_event.is_set():
            try:
                # Use a loop with a timeout instead of a long sleep to be more responsive to the stop event
                self._stop_event.wait(self._refresh_interval)
                if self._stop_event.is_set():
                    break
                logger.info("🔄 Performing periodic worker refresh...")
                self._discover_and_update()
            except Exception as e:
                logger.info(f"Error in periodic refresh loop: {e}")

    def _discover_and_update(self):
        """Compares discovered workers with the active list and updates the pool."""
        discovered = self._discover_workers()

        with self._lock:
            # Add new workers
            new_workers = discovered - self._active_workers
            for addr in new_workers:
                if self._is_healthy(addr):
                    self._active_workers.add(addr)
                    self._pool.put(addr)
                    logger.info(f"  -> Added new healthy worker: {addr}")

            # Identify and handle dead workers
            dead_workers = self._active_workers - discovered
            if dead_workers:
                logger.info(
                    f"  -> Discovered dead workers (no longer in DNS): {dead_workers}"
                )
                self._active_workers -= dead_workers

            logger.info(
                f"Worker refresh complete. Active workers: {len(self._active_workers)}"
            )

    def acquire(self, block: bool = True, timeout: float | None = None) -> str:
        """
        Gets a worker from the pool. If the worker is found to be dead,
        it's discarded and the next one is fetched.
        """
        while True:
            try:
                logger.info(
                    f"Waiting for a free worker... ({self._pool.qsize()} available)"
                )
                # Use the blocking get() from the thread-safe queue
                addr = self._pool.get(block=block, timeout=timeout)

                with self._lock:
                    if addr in self._active_workers:
                        logger.info(f"  -> Acquired worker: {addr}")
                        return addr
                    else:
                        logger.info(f"  -> Discarding stale worker from queue: {addr}")
                        # Continue loop to get the next available worker
            except Empty:
                raise  # Re-raise the Empty exception if timeout is hit

    def release(self, addr: str):
        """
        Returns a worker to the pool, but only if it's still healthy and active.
        """
        with self._lock:
            if addr in self._active_workers:
                if self._is_healthy(addr):
                    self._pool.put(addr)
                    logger.info(f"  -> Released healthy worker: {addr}")
                else:
                    logger.info(
                        f"  -> Worker {addr} failed health check. Removing from pool."
                    )
                    self._active_workers.discard(addr)
            else:
                logger.info(f"  -> Not releasing dead worker: {addr}")
