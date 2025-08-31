from aci.common.utils import check_and_get_env_variable
from dotenv import load_dotenv

load_dotenv()

MISTRALAI_API_KEY = check_and_get_env_variable("AGENT_MISTRALAI_API_KEY")
CEREBRAS_API_KEY = check_and_get_env_variable("AGENT_CEREBRAS_API_KEY")
DEEPINFRA_API_KEY = check_and_get_env_variable("AGENT_DEEPINFRA_API_KEY")
DEEPINFRA_BASE_URL = check_and_get_env_variable("AGENT_DEEPINFRA_BASE_URL")
OPENAI_API_KEY = check_and_get_env_variable("AGENT_OPENAI_API_KEY")
LIVEKIT_API_SECRET = check_and_get_env_variable("AGENT_LIVEKIT_API_SECRET")
LIVEKIT_URL = check_and_get_env_variable("AGENT_LIVEKIT_URL")
LIVEKIT_API_KEY = check_and_get_env_variable("AGENT_LIVEKIT_API_KEY")
