import os
import json
from pathlib import Path
from typing import cast

from pydantic import SecretStr
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
APP_VERSION = "0.0.5"
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
ROUTER_PREFIX_PROFILE = "/v1/profile"
ROUTER_PREFIX_AUTOMATIONS = "/v1/automations"
ROUTER_PREFIX_AUTOMATION_TEMPLATES = "/v1/automation-templates"
ROUTER_PREFIX_AUTOMATION_RUNS = "/v1/automation-runs"
ROUTER_PREFIX_ACTIVITY = "/v1/activity"
ROUTER_PREFIX_USAGE = "/v1/usage"
ROUTER_PREFIX_FCM = "/v1/fcm"
ROUTER_PREFIX_WEBHOOKS = "/v1/webhooks"
ROUTER_PREFIX_SUBSCRIPTIONS = "/v1/subscriptions"

# SUBSCRIPTION PLANS
# Load subscription plans from JSON file at startup
def _load_subscription_plans():
    """Load subscription plans from the JSON configuration file."""
    try:

        plans_file = os.getenv("SUBSCRIPTION_PLANS_FILE_PATH", "aci/subscription_plans.json") 

        if not os.path.exists(plans_file):
            print(f"Warning: subscription_plans.json not found at {plans_file}")
            return []

        with open(plans_file, 'r') as f:
            data = json.load(f)

        return data
    except Exception as e:
        print(f"Error loading subscription plans: {e}")
        return []

SUBSCRIPTION_PLANS = cast(dict[str, dict], _load_subscription_plans())

# 8KB
MAX_LOG_FIELD_SIZE = 8 * 1024

# VOICE AGENT
LIVEKIT_HOST_URL = check_and_get_env_variable("SERVER_LIVEKIT_HOST_URL")
LIVEKIT_API_KEY = check_and_get_env_variable("SERVER_LIVEKIT_API_KEY")  
LIVEKIT_API_SECRET = check_and_get_env_variable("SERVER_LIVEKIT_API_SECRET")


# SearXNG
SEARXNG_INSTANCE_URL = check_and_get_env_variable("SERVER_SEARXNG_INSTANCE_URL")

# Supabase
SUPABASE_SERVICE_KEY = check_and_get_env_variable("SERVER_SUPABASE_SERVICE_KEY")
SUPABASE_URL = check_and_get_env_variable("SERVER_SUPABASE_URL")


REDIS_URL = check_and_get_env_variable("SERVER_REDIS_URL")

CODE_EXECUTOR_URL = check_and_get_env_variable("SERVER_CODE_EXECUTOR_URL")


# CycleTLS Server Configuration
CYCLE_TLS_SERVER_URL = check_and_get_env_variable("SERVER_CYCLE_TLS_SERVER_URL")
HTTP_PROXY = os.getenv("SERVER_HTTP_PROXY")

# LLM
DEEPINFRA_API_KEY = check_and_get_env_variable("SERVER_DEEPINFRA_API_KEY")
DEEPINFRA_BASE_URL = check_and_get_env_variable("SERVER_DEEPINFRA_BASE_URL")
TOGETHER_API_KEY = check_and_get_env_variable("SERVER_TOGETHER_API_KEY")
TOGETHER_BASE_URL = check_and_get_env_variable("SERVER_TOGETHER_BASE_URL")

GOTENBERG_URL = check_and_get_env_variable("SERVER_GOTENBERG_URL")

# Browser Automation
STEEL_BASE_URL = check_and_get_env_variable("SERVER_STEEL_BASE_URL")
BROWSER_MAX_WORKERS = int(os.getenv("SERVER_BROWSER_MAX_WORKERS", "5"))

# SMTP
SMTP_SERVER = check_and_get_env_variable("SERVER_SMTP_SERVER")
SMTP_PORT = int(check_and_get_env_variable("SERVER_SMTP_PORT"))
SMTP_USERNAME = check_and_get_env_variable("SERVER_SMTP_USERNAME")  
SMTP_PASSWORD = SecretStr(check_and_get_env_variable("SERVER_SMTP_PASSWORD"))
FROM_EMAIL_AGENT = check_and_get_env_variable("SERVER_FROM_EMAIL_AGENT")
FIREBASE_SERVICE_ACCOUNT_KEY_PATH = check_and_get_env_variable("SERVER_FIREBASE_SERVICE_ACCOUNT_KEY_PATH")

WHATSAPP_API_TOKEN = check_and_get_env_variable("SERVER_WHATSAPP_API_TOKEN")
WHATSAPP_PHONE_NUMBER_ID = check_and_get_env_variable("SERVER_WHATSAPP_PHONE_NUMBER_ID")

SKYVERN_BASE_URL = check_and_get_env_variable("SERVER_SKYVERN_BASE_URL")
SKYVERN_API_KEY = check_and_get_env_variable("SERVER_SKYVERN_API_KEY")
USE_SKYVERN = os.getenv("SERVER_USE_SKYVERN", "false").lower() == "true"
BROWSER_POOL_REFRESH_INTERVAL = int(os.getenv("SERVER_BROWSER_POOL_REFRESH_INTERVAL", "30"))
BROWSER_SERVICE_NAME = os.getenv("SERVER_BROWSER_SERVICE_NAME", "headless-browser")


# RevenueCat
REVENUECAT_WEBHOOK_AUTH_TOKEN = check_and_get_env_variable("SERVER_REVENUECAT_WEBHOOK_AUTH_TOKEN")
