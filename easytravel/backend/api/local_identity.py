"""Local-only authentication. No request-controlled actor, cookies or proxy trust."""
from hmac import compare_digest
from ipaddress import ip_address
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .errors import LocalApiError


bearer = HTTPBearer(auto_error=False, scheme_name="LocalDevBearer",
                    description="Local development only; opaque server-managed token, not production authentication.")


async def trusted_actor(request: Request,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> str:
    settings = request.app.state.settings
    if not settings.local_business_enabled:
        raise LocalApiError(403, "local_only_api")
    try:
        loopback = request.client is not None and ip_address(request.client.host).is_loopback
    except ValueError:
        loopback = False
    hosts = request.headers.getlist("host")
    if not loopback or len(hosts) != 1 or hosts[0] not in settings.local_allowed_hosts:
        raise LocalApiError(403, "local_only_api")
    origins = request.headers.getlist("origin")
    if len(origins) > 1 or (origins and origins[0] not in settings.local_allowed_origins):
        raise LocalApiError(403, "origin_not_allowed")
    if credentials is None or len(request.headers.getlist("authorization")) != 1:
        raise LocalApiError(401, "authentication_required")
    # Limit to the configured token alphabet before constant-time comparison;
    # compare_digest(str, str) would otherwise throw on non-ASCII credentials.
    token = credentials.credentials
    if len(token) > 128 or not token.isascii():
        raise LocalApiError(401, "authentication_required")
    actor = None
    for identity in settings.local_identities:
        if compare_digest(token, identity.token.get_secret_value()):
            actor = identity.actor
    if actor is None:
        raise LocalApiError(401, "authentication_required")
    pairs = list(request.query_params.multi_items())
    history_query = (request.method == 'GET' and request.scope.get('route') is not None
        and request.scope['route'].path == '/api/v1/trips/{trip_id}/snapshots'
        and all(k in {'limit', 'cursor'} for k, _ in pairs)
        and len({k for k, _ in pairs}) == len(pairs))
    if (pairs and not history_query) or "x-actor" in request.headers:
        raise LocalApiError(422, "invalid_request")
    return actor
