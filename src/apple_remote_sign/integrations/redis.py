from redis import Redis
from redis.backoff import NoBackoff
from redis.retry import Retry

from apple_remote_sign.config import Settings


def build_redis_client(settings: Settings) -> Redis:
    try:
        # Native URL parsing preserves DB selection from /N or ?db=N, plus TLS,
        # ACL usernames and percent-encoded credentials. Never replay a timed-out
        # acquisition: its first SET might already have succeeded on the server.
        client = Redis.from_url(
            settings.redis_url.get_secret_value(),
            socket_connect_timeout=3,
            socket_timeout=3,
            retry=Retry(NoBackoff(), 0),
        )
        if client.get_connection_kwargs().get("db", 0) < 0:
            client.close()
            raise ValueError("Negative DB")
        return client
    except ValueError, TypeError:
        raise RuntimeError("Invalid REDIS_URL configuration.") from None
