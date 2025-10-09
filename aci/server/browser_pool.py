import socket
import requests
import threading
import time
from redis import Redis as SyncRedis
from redis import exceptions
from aci.common.utils import get_logger


logger = get_logger(__name__)


class ResilientBrowserPool:
    """
    Manages a dynamic, self-healing, and distributed pool of browser workers using Redis.
    This is a synchronous, thread-safe class designed for multi-threaded applications.
    """

    def __init__(
        self,
        redis_client: SyncRedis,
        service_name: str,
        refresh_interval: int = 30,
        stale_timeout: int = 300, # 5 minutes
    ):
        """
        Initializes the pool.
        Args:
            redis_client: An already-configured Redis client instance.
            service_name: The DNS name of the browser service in Docker Swarm.
            refresh_interval: How often (in seconds) to check for new/dead pods.
            stale_timeout: How long (in seconds) a worker can be 'busy' before being considered stuck.
        """
        self._redis = redis_client
        self._service_name = service_name
        self._refresh_interval = refresh_interval
        self._stale_timeout = stale_timeout

        # Define the Redis keys we will use to manage state
        self._keys = {
            "free": f"browser_pool:{service_name}:free",      # LIST of available worker addresses
            "active": f"browser_pool:{service_name}:active",  # SET of all known live worker addresses
            "busy": f"browser_pool:{service_name}:busy",      # HASH of {addr: timestamp} for busy workers
        }

        self._refresh_thread = None
        self._stop_event = threading.Event()

    def start(self):
        """Initializes the pool state in Redis and starts the background refresh thread."""
        logger.info("Starting resilient browser pool with Redis backend...")
        # Clear any old state on startup for a clean slate
        self._redis.delete(*self._keys.values())
        
        self._discover_and_update()

        self._refresh_thread = threading.Thread(
            target=self._periodic_refresh, daemon=True, name="PoolRefreshThread"
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
        dns_name = f"tasks.{self._service_name}"
        try:
            resolved_hosts = socket.getaddrinfo(
                dns_name, 6000, family=socket.AF_INET, type=socket.SOCK_STREAM
            )
            for host in resolved_hosts:
                ip = host[4][0]
                discovered_addresses.add(f"http://{ip}:6000")
        except socket.gaierror:
            pass # This is expected if the service has scaled to 0
        except Exception as e:
            logger.error(f"Unexpected DNS discovery error: {e}")
        return discovered_addresses

    def _is_healthy(self, addr: str) -> bool:
        """Performs a quick, synchronous health check on a worker instance."""
        try:
            with requests.Session() as session:
                resp = session.get(f"{addr}/health", timeout=2.0)
                return resp.status_code == 200
        except requests.exceptions.RequestException:
            return False

    def _periodic_refresh(self) -> None:
        """Background thread that periodically discovers, prunes dead workers, and reclaims stuck workers."""
        while not self._stop_event.is_set():
            try:
                self._stop_event.wait(self._refresh_interval)
                if self._stop_event.is_set(): break
                
                logger.info("🔄 Performing periodic worker refresh...")
                self._discover_and_update()
                self._reclaim_stuck_workers()

            except Exception as e:
                logger.error(f"Error in periodic refresh loop: {e}")

    def _discover_and_update(self) -> None:
        """Compares discovered workers with the active list in Redis and updates state."""
        discovered = self._discover_workers()
        active = {addr.decode() for addr in self._redis.smembers(self._keys["active"])}  # type: ignore

        # Add new workers
        new_workers = discovered - active
        for addr in new_workers:
            if self._is_healthy(addr):
                self._redis.sadd(self._keys["active"], addr)
                self._redis.lpush(self._keys["free"], addr)
                logger.info(f"  -> Discovered and added new healthy worker: {addr}")

        # Remove dead workers that are no longer in DNS
        dead_workers = active - discovered
        if dead_workers:
            logger.warning(f"  -> Pruning dead workers (no longer in DNS): {dead_workers}")
            pipe = self._redis.pipeline()
            pipe.srem(self._keys["active"], *dead_workers)
            pipe.hdel(self._keys["busy"], *dead_workers)
            for addr in dead_workers:
                pipe.lrem(self._keys["free"], 0, addr)
            pipe.execute()

    def _reclaim_stuck_workers(self) -> None:
        """Finds workers in the 'busy' hash that have exceeded their timeout and reclaims them."""
        busy_workers = self._redis.hgetall(self._keys["busy"])  # type: ignore
        now = time.time()
        
        for addr_bytes, timestamp_bytes in busy_workers.items():  # type: ignore
            addr = addr_bytes.decode()
            timestamp = float(timestamp_bytes.decode())

            if (now - timestamp) > self._stale_timeout:
                logger.warning(f"  -> Found stale lease for worker {addr}. Attempting to reclaim...")
                if self._is_healthy(addr):
                    self._redis.lpush(self._keys["free"], addr)
                    logger.info(f"  -> Reclaimed healthy stale worker: {addr}")
                else:
                    # If it's not healthy, just remove it. The discovery process will handle the rest.
                    logger.warning(f"  -> Stale worker {addr} is unhealthy. Removing permanently.")
                    self._redis.srem(self._keys["active"], addr)
                
                # In either case, remove the stale lease from the busy hash
                self._redis.hdel(self._keys["busy"], addr)


    def acquire(self, timeout: int = 60) -> str | None:
        """
        Gets a worker address from the free pool (blocking) and places a lease on it in the busy hash.
        Returns:
            The worker address string, or None if the timeout is reached.
        """
        logger.info(f"Waiting for a free worker... ({self._redis.llen(self._keys['free'])} available)")
        try:
            # BRPOP is a blocking pop from the right of the list. Returns (key, value) or None on timeout.
            result = self._redis.brpop([self._keys["free"]], timeout=timeout)
            if result is None:
                raise TypeError # Will be caught below
            
            _, addr_bytes = result  # type: ignore
            addr = addr_bytes.decode()
            
            # --- FIX: Cast the float timestamp to a string before setting in Redis ---
            # Place a lease on the worker by adding it to the busy hash with a timestamp
            self._redis.hset(self._keys["busy"], addr, str(time.time()))
            # --- END FIX ---
            
            logger.info(f"  -> Acquired worker: {addr}")
            return addr
        except (TypeError, exceptions.RedisError):
            # TypeError occurs if brpop times out (returns None), RedisError for connection issues
            logger.warning(f"Could not acquire a worker within the {timeout}s timeout.")
            return None

    def release(self, addr: str):
        """
        Returns a worker to the pool if it's healthy, and always removes its 'busy' lease.
        """
        if self._is_healthy(addr):
            self._redis.lpush(self._keys["free"], addr)
            logger.info(f"  -> Released healthy worker: {addr}")
        else:
            logger.warning(f"  -> Worker {addr} failed health check upon release. Removing permanently.")
            self._redis.srem(self._keys["active"], addr)
        
        # Always remove the lease, whether healthy or not
        self._redis.hdel(self._keys["busy"], addr)

