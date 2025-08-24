from huey import RedisHuey
from aci.server.config import REDIS_URL

huey = RedisHuey('app', url=REDIS_URL)