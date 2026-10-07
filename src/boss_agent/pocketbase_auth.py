"""
boss_agent.pocketbase_auth
==========================
The one place the PocketBase bearer header is built.

The broker adapter and the SavedSearch store talk to the same PocketBase instance with
the same credential, and the auth scheme is the one thing an upgrade changes underneath
both of them. It lives in a leaf module with no project imports because
``broker.pocketbase_adapter`` already imports ``saved_search_store``: the helper cannot
sit in either without one of them importing the other for the sake of two lines.
"""

from __future__ import annotations


def pocketbase_headers(auth_token: str | None) -> dict[str, str]:
    """The JSON request headers for a PocketBase call, bearer token included.

    A missing token yields no ``Authorization`` header at all rather than
    ``Bearer None``: PocketBase answers the literal string with a 401 that reads like a
    rejected credential, so the failure would point at the token instead of at the
    store that never had one.
    """
    headers = {"Content-Type": "application/json"}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    return headers
