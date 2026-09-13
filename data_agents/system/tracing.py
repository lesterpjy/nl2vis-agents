"""Tracing, configured once per process, where that process starts doing work.

Configuring at import instead would trace the test suite, whose Turns carry the same rows a real one does — so the two
callers are the CLI's callback and the service's startup, and never a module body.
"""

import os

import logfire

OFF = ("0", "false", "no", "off")
ON = ("1", "true", "yes", "on")


def configure(service_name: str) -> None:
    logfire.configure(send_to_logfire=_send(), console=False, service_name=service_name)
    logfire.instrument_pydantic_ai()
    logfire.instrument_sqlite3()


def _send() -> bool | str:
    """Whether spans leave the machine. The default is Logfire's own `if-token-present`, which is what lets an installation
    with no token run silently — it sends nothing and says nothing about it.

    `LOGFIRE_SEND_TO_LOGFIRE=false` turns sending off outright whatever credentials are lying around, which is what the test
    suite sets: one CLI command configures the whole pytest process, and every Turn after it used to ship its rows.
    """
    value = os.environ.get("LOGFIRE_SEND_TO_LOGFIRE", "").strip().lower()
    if value in OFF:
        return False
    return True if value in ON else "if-token-present"
