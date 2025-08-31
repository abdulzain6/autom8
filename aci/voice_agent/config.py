import os
from aci.common.utils import check_and_get_env_variable, construct_db_url
from dotenv import load_dotenv
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


load_dotenv()
logger.info("Environment variables loaded successfully.")
logging.debug("Debug logging is enabled." + str(os.environ))



MISTRALAI_API_KEY = check_and_get_env_variable("AGENT_MISTRALAI_API_KEY")
CEREBRAS_API_KEY = check_and_get_env_variable("AGENT_CEREBRAS_API_KEY")
DEEPINFRA_API_KEY = check_and_get_env_variable("AGENT_DEEPINFRA_API_KEY")
DEEPINFRA_BASE_URL = check_and_get_env_variable("AGENT_DEEPINFRA_BASE_URL")
OPENAI_API_KEY = check_and_get_env_variable("AGENT_OPENAI_API_KEY")
LIVEKIT_API_SECRET = check_and_get_env_variable("AGENT_LIVEKIT_API_SECRET")
LIVEKIT_URL = check_and_get_env_variable("AGENT_LIVEKIT_URL")
LIVEKIT_API_KEY = check_and_get_env_variable("AGENT_LIVEKIT_API_KEY")

DB_SCHEME = check_and_get_env_variable("AGENT_DB_SCHEME")
DB_USER = check_and_get_env_variable("AGENT_DB_USER")
DB_PASSWORD = check_and_get_env_variable("AGENT_DB_PASSWORD")
DB_HOST = check_and_get_env_variable("AGENT_DB_HOST")
DB_PORT = check_and_get_env_variable("AGENT_DB_PORT")
DB_NAME = check_and_get_env_variable("AGENT_DB_NAME")
DB_FULL_URL = construct_db_url(DB_SCHEME, DB_USER, DB_PASSWORD, DB_HOST, DB_PORT, DB_NAME)