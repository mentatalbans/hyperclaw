"""Test-only CLI launcher that resets a session after chat reads its generation."""
from urllib.parse import urlsplit

import httpx


original_get = httpx.Client.get


def reset_after_session_read(self, url, *args, **kwargs):
    response = original_get(self, url, *args, **kwargs)
    path = urlsplit(str(url)).path
    if path.startswith("/v1/sessions/") and path.count("/") == 3 and response.status_code == 200:
        generation = response.json()["generation"]
        reset = self.post(path + "/reset", json={"generation": generation})
        reset.raise_for_status()
    return response


httpx.Client.get = reset_after_session_read

from hyperclaw.cli import main

main()
