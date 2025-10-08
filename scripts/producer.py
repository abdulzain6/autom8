import redis
import redis.exceptions

# --- Configuration ---
# This script connects to Redis and populates the job queue with URLs to process.
REDIS_HOST = "localhost"
REDIS_PORT = 6379
JOB_QUEUE_NAME = "browser_job_queue"

# A list of tasks (URLs) to add to the queue.
URLS_TO_PROCESS = [
    "https://www.rust-lang.org/",
    "https://tokio.rs/",
    "https://www.docker.com/",
    "https://kubernetes.io/",
    "https://github.com/spider-rs/headless-browser",
    "https://www.python.org/",
    "https://www.djangoproject.com/",
    "https://fastapi.tiangolo.com/",
]

def main():
    """Connects to Redis and pushes jobs to the queue."""
    print("Connecting to Redis...")
    try:
        r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
        # Check if the connection is alive
        r.ping()
        print("✅ Connected to Redis successfully.")
    except redis.exceptions.ConnectionError as e:
        print(f"❌ Could not connect to Redis: {e}")
        print("Please ensure Redis is running and accessible.")
        return

    print(f"Adding {len(URLS_TO_PROCESS)} jobs to the queue '{JOB_QUEUE_NAME}'...")
    
    for url in URLS_TO_PROCESS:
        # rpush adds the item to the right (end) of the list.
        r.rpush(JOB_QUEUE_NAME, url)
        print(f"  -> Added job: {url}")

    print("\n✅ All jobs have been added to the queue.")
    print("You can now run the pool_manager.py script to process them.")

if __name__ == "__main__":
    main()
