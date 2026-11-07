"""Decides whether a request really arrived over HTTPS, using TRUSTED information only.

Why this is needed: a load balancer (ALB) talks to the app over plain HTTP and adds the header
"X-Forwarded-Proto: https" to say the BROWSER used HTTPS. But any client can send that header too.
Uvicorn uses it to set request.url.scheme for every address in --forwarded-allow-ips, and by
default that includes 127.0.0.1, which is exactly where SSH-tunnel traffic comes from.

So the app only believes a forwarded header when you say a trusted proxy is in front of it:
  TRUST_PROXY_HEADERS=false (default, SSH tunnel or local PC): a request that carries the header
      cannot be confirmed as HTTPS, whatever uvicorn decided.
  TRUST_PROXY_HEADERS=true (behind your ALB): uvicorn must ALSO be started with
      --proxy-headers --forwarded-allow-ips=<the ALB's subnet range>. Uvicorn then rewrites the scheme
      only for requests from those addresses, and the app accepts uvicorn's decision.
"""

from core.aws_errors import CONFIGURATION_ERROR, CONNECTED, NOT_CONFIGURED  # Status words.
from core.config import get_settings  # Reads TRUST_PROXY_HEADERS and SESSION_HTTPS_ONLY.


def describe_request(request):
    """Collects what the app knows about how this request arrived. Used by the HTTPS checks below.

    Returns: {"scheme": what uvicorn decided ("http"/"https"),
              "forwarded_proto": the X-Forwarded-Proto header ("" if none),
              "trust_proxy_headers": True/False from .env}
    """
    # A chain of proxies can send "https, http". The first value is what the browser used.
    forwarded = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    return {"scheme": request.url.scheme, "forwarded_proto": forwarded,
            "trust_proxy_headers": get_settings()["trust_proxy_headers"]}


def https_status(info):
    """Returns (status, explanation) for "did the browser use HTTPS?". info comes from describe_request()."""
    scheme = info["scheme"]
    forwarded = info.get("forwarded_proto", "")
    trusted = info.get("trust_proxy_headers", False)

    if forwarded and not trusted:
        # The header may be forged, and uvicorn may already have used it, so neither value is proof.
        return CONFIGURATION_ERROR, (
            f"The request carries X-Forwarded-Proto: {forwarded}, but TRUST_PROXY_HEADERS=false, so it is ignored "
            "(any client can send it) and HTTPS cannot be confirmed. Behind your load balancer, set "
            "TRUST_PROXY_HEADERS=true and start uvicorn with --proxy-headers --forwarded-allow-ips=<ALB subnets>. "
            "Through the SSH tunnel, start uvicorn with --no-proxy-headers.")
    if forwarded and scheme == "https":
        return CONNECTED, ("The browser used HTTPS. Your load balancer reported it, and uvicorn accepted the header "
                           "because the load balancer's address is in --forwarded-allow-ips.")
    if forwarded == "https":
        return CONFIGURATION_ERROR, (
            "A proxy says the browser used HTTPS, but uvicorn did not accept it: the proxy's address is not in "
            "--forwarded-allow-ips (or --proxy-headers is off). Add the load balancer's subnet range there.")
    if forwarded:
        return NOT_CONFIGURED, ("The load balancer forwarded a plain-HTTP request. Add an HTTP :80 listener that "
                                "redirects to HTTPS :443.")
    if scheme == "https":
        return CONNECTED, "The browser connected to the app directly over HTTPS."
    return NOT_CONFIGURED, ("HTTPS is not enabled for this connection: the page arrived over plain HTTP. "
                            "That is acceptable only on your own computer or through the SSH tunnel.")


def cookie_status(info):
    """Returns (status, explanation): does SESSION_HTTPS_ONLY match how the app is really reached?"""
    https = https_status(info)[0] == CONNECTED
    secure = get_settings()["session_https_only"]
    if https and secure:
        return CONNECTED, "SESSION_HTTPS_ONLY=true and this request used HTTPS, so the login cookie is Secure."
    if https:
        return CONFIGURATION_ERROR, ("This request used HTTPS, but SESSION_HTTPS_ONLY=false, so the login cookie "
                                     "could also be sent over plain HTTP. Set SESSION_HTTPS_ONLY=true.")
    if secure:
        return CONFIGURATION_ERROR, ("SESSION_HTTPS_ONLY=true, but HTTPS is not confirmed for this request. Browsers "
                                     "may refuse to send the login cookie. Use HTTPS, or set it to false for the "
                                     "SSH tunnel.")
    return NOT_CONFIGURED, ("SESSION_HTTPS_ONLY=false. Correct for plain HTTP through the SSH tunnel; set it to "
                            "true when the app is served over HTTPS.")
