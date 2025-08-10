from aci.common.utils import check_and_get_env_variable, construct_db_url
import dotenv


dotenv.load_dotenv()


ENVIRONMENT = check_and_get_env_variable("SERVER_ENVIRONMENT")

# LLM
OPENAI_BASE_URL = check_and_get_env_variable("SERVER_OPENAI_BASE_URL")
OPENAI_API_KEY = check_and_get_env_variable("SERVER_OPENAI_API_KEY")
OPENAI_EMBEDDING_MODEL = check_and_get_env_variable("SERVER_OPENAI_EMBEDDING_MODEL")
OPENAI_EMBEDDING_DIMENSION = int(check_and_get_env_variable("SERVER_OPENAI_EMBEDDING_DIMENSION"))

# JWT
SUPABASE_JWT_SECRET = check_and_get_env_variable("SERVER_SUPABASE_JWT_SECRET")
REDIRECT_URI_BASE = check_and_get_env_variable("SERVER_REDIRECT_URI_BASE")
SIGNING_KEY = check_and_get_env_variable("SERVER_SIGNING_KEY")
JWT_ALGORITHM = check_and_get_env_variable("SERVER_JWT_ALGORITHM")

DB_SCHEME = check_and_get_env_variable("SERVER_DB_SCHEME")
DB_USER = check_and_get_env_variable("SERVER_DB_USER")
DB_PASSWORD = check_and_get_env_variable("SERVER_DB_PASSWORD")
DB_HOST = check_and_get_env_variable("SERVER_DB_HOST")
DB_PORT = check_and_get_env_variable("SERVER_DB_PORT")
DB_NAME = check_and_get_env_variable("SERVER_DB_NAME")
DB_FULL_URL = construct_db_url(DB_SCHEME, DB_USER, DB_PASSWORD, DB_HOST, DB_PORT, DB_NAME)

# RATE LIMITS
RATE_LIMIT_IP_PER_SECOND = int(check_and_get_env_variable("SERVER_RATE_LIMIT_IP_PER_SECOND"))
RATE_LIMIT_IP_PER_DAY = int(check_and_get_env_variable("SERVER_RATE_LIMIT_IP_PER_DAY"))

# APP
APP_TITLE = "Autom8"
APP_VERSION = "0.0.1"
APP_DOCS_URL = "/docs"
APP_REDOC_URL = "/redoc"
APP_OPENAPI_URL = "/openapi.json"

# ROUTERS
ROUTER_PREFIX_HEALTH = "/v1/health"
ROUTER_PREFIX_AUTH = "/v1/auth"
ROUTER_PREFIX_APPS = "/v1/apps"
ROUTER_PREFIX_FUNCTIONS = "/v1/functions"
ROUTER_PREFIX_APP_CONFIGURATIONS = "/v1/app-configurations"
ROUTER_PREFIX_LINKED_ACCOUNTS = "/v1/linked-accounts"
ROUTER_PREFIX_VOICE_AGENT = "/v1/voice-agent"
# 8KB
MAX_LOG_FIELD_SIZE = 8 * 1024

# VOICE AGENT
LIVEKIT_HOST_URL = check_and_get_env_variable("SERVER_LIVEKIT_HOST_URL")
LIVEKIT_API_KEY = check_and_get_env_variable("SERVER_LIVEKIT_API_KEY")  
LIVEKIT_API_SECRET = check_and_get_env_variable("SERVER_LIVEKIT_API_SECRET")


# SearXNG
SEARXNG_INSTANCE_URL = check_and_get_env_variable("SERVER_SEARXNG_INSTANCE_URL")

# SeaweedFS
SEAWEEDFS_URL = check_and_get_env_variable("SERVER_SEAWEEDFS_URL")

REDIS_URL = check_and_get_env_variable("SERVER_REDIS_URL")

CODE_EXECUTOR_URL = check_and_get_env_variable("SERVER_CODE_EXECUTOR_URL")