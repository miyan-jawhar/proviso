"""Shared API rate limiter configuration."""

from slowapi import Limiter
from slowapi.util import get_remote_address


# The default key is the client IP address. Authentication still protects the
# endpoint; deployments with a trusted proxy should replace this with a
# user-aware key function after forwarding headers are configured safely.
limiter = Limiter(key_func=get_remote_address)
