"""
ETag middleware for conditional HTTP responses.

Generates ETags from response body hashes. Returns 304 Not Modified
when client sends matching If-None-Match header, saving bandwidth
on unchanged polling responses.
"""

import hashlib
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class ETagMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        # Only process GET requests with JSON responses
        if request.method != "GET":
            return response

        content_type = response.headers.get("content-type", "")
        if "application/json" not in content_type:
            return response

        # Read the response body
        body = b""
        async for chunk in response.body_iterator:
            body += chunk

        # Generate ETag from body hash
        etag = f'W/"{hashlib.md5(body).hexdigest()}"'

        # Check If-None-Match
        if_none_match = request.headers.get("if-none-match")
        if if_none_match and if_none_match == etag:
            return Response(status_code=304, headers={"ETag": etag})

        # Return response with ETag header
        return Response(
            content=body,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.media_type,
        )
