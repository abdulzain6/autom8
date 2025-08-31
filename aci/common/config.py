from aci.common.utils import check_and_get_env_variable
import os


DOTENV_PATH = os.path.join(os.path.dirname(__file__), "..", ".env")
if os.path.exists(DOTENV_PATH):
    from dotenv import load_dotenv

    load_dotenv(DOTENV_PATH)
else:
    raise FileNotFoundError(f".env file not found at {DOTENV_PATH}")

ENCRYPTION_SECRET_KEY = check_and_get_env_variable("COMMON_ENCRYPTION_SECRET_KEY")