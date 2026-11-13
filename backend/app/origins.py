"""Browser request-origin checks that work behind a reverse proxy."""

from urllib.parse import urlsplit

from starlette.requests import Request

Origin = tuple[str, str, int]


def normalize_origin(value: str) -> Origin | None:
    try:
        url = urlsplit(value)
        if (
            url.scheme not in ("http", "https")
            or not url.hostname
            or url.username is not None
            or url.password is not None
            or url.path not in ("", "/")
            or url.query
            or url.fragment
        ):
            return None
        port = url.port
        return (
            url.scheme,
            url.hostname.lower(),
            port if port is not None else (443 if url.scheme == "https" else 80),
        )
    except ValueError:
        return None


def allows_request(request: Request, allowed: tuple[str, ...]) -> bool:
    site = request.headers.get("sec-fetch-site")
    raw_origin = request.headers.get("origin")
    if raw_origin is None:
        # Preserve non-browser API clients, but reject browser cross-site writes.
        return site not in ("cross-site", "same-site")
    origin = normalize_origin(raw_origin)
    if origin is None:
        return False
    if any(origin == normalize_origin(value) for value in allowed):
        return True
    # Browsers set this forbidden header from the public URL, before any proxy
    # rewrites Host or terminates HTTPS. Client JavaScript cannot override it.
    if site == "same-origin":
        return True
    if site in ("cross-site", "same-site"):
        return False
    return origin == normalize_origin(f"{request.url.scheme}://{request.url.netloc}")
