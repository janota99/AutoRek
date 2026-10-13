"""How the app reaches MongoDB Atlas: configuration, a client, and plain-language failure reasons.

Nothing in the app calls this yet: it is the "ready to connect" layer. Settings come from environment
variables (which win, so a test can point somewhere else) or Streamlit secrets, the same way ``db/connection.py``
does for PostgreSQL:

    # .streamlit/secrets.toml (never committed)          environment equivalent
    [mongodb]                                            AUTOREK_MONGODB_URI
    uri      = "mongodb+srv://user:pw@cluster0.xxxx.mongodb.net/"
    database = "janota_fin"                              AUTOREK_MONGODB_DATABASE   (optional)

With nothing configured ``load_config`` returns None and the app keeps working without a database. The
``pymongo`` driver is imported only when a client is opened, so the rest of the app runs without it installed.
The connection string holds the password, so it is never printed, logged, or shown in an error message.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Mapping, Optional

URI_ENV = "AUTOREK_MONGODB_URI"
DATABASE_ENV = "AUTOREK_MONGODB_DATABASE"
DEFAULT_DATABASE = "janota_fin"
SERVER_TIMEOUT_MS = 5000          # fail fast: the page should never hang waiting for a database

_URI_IN_TEXT = re.compile(r"mongodb(?:\+srv)?://[^\s'\"]+", re.IGNORECASE)


class MongoConfigError(Exception):
    """The settings are present but unusable."""


@dataclass(frozen=True)
class MongoConfig:
    uri: str
    database: str = DEFAULT_DATABASE

    def __repr__(self) -> str:  # never print the connection string
        return f"MongoConfig(database={self.database!r}, uri=<hidden>)"


def load_config(environ: Optional[Mapping[str, str]] = None, secrets: Any = None) -> Optional[MongoConfig]:
    """The configured database, or None when none is set up (the app then works without one)."""
    env = os.environ if environ is None else environ
    section: Mapping[str, Any] = {}
    try:
        if secrets is not None and "mongodb" in secrets:
            section = secrets["mongodb"]
    except Exception:  # noqa: BLE001 - a missing or unreadable secrets file means "not configured"
        section = {}
    uri = env.get(URI_ENV) or section.get("uri")
    database = env.get(DATABASE_ENV) or section.get("database") or DEFAULT_DATABASE
    if not uri:
        return None
    uri = str(uri).strip()
    if not re.match(r"^mongodb(\+srv)?://", uri):
        raise MongoConfigError("The connection string must start with mongodb:// or mongodb+srv://.")
    if "<password>" in uri or "<db_password>" in uri:
        raise MongoConfigError("The connection string still contains the <password> placeholder; put the real "
                               "password in its place (URL-encode special characters such as @ : / ?).")
    if "@" not in uri:
        raise MongoConfigError("The connection string has no user name and password (user:password@host).")
    return MongoConfig(uri=uri, database=str(database))


def connect(config: MongoConfig, *, timeout_ms: int = SERVER_TIMEOUT_MS):
    """A MongoClient for the configured cluster. Lazy: no network traffic until the first operation."""
    from pymongo import MongoClient  # imported here so the app runs without the driver installed

    return MongoClient(config.uri, serverSelectionTimeoutMS=timeout_ms, appname="janota-fin")


def scrub(text: str) -> str:
    """Remove anything that looks like a connection string from text meant for a person."""
    return _URI_IN_TEXT.sub("<connection string hidden>", text)


def describe_error(exc: BaseException) -> str:
    """A one-sentence reason a person can act on, never containing the connection string."""
    kind = type(exc).__name__
    text = str(exc)
    low = text.lower()
    code = getattr(exc, "code", None)
    if kind == "OperationFailure" and (code == 13 or "not authorized" in low or "unauthorized" in low):
        return "The database user is not allowed to do that. It needs the readWrite role on the janota_fin database."
    if kind == "OperationFailure" and (code == 18 or "bad auth" in low or "authentication failed" in low):
        return ("Atlas rejected the user name or password. Check them in Database Access, and URL-encode special "
                "characters in the password (@ becomes %40, : becomes %3A, / becomes %2F).")
    if kind in {"ConfigurationError", "InvalidURI"} or "dns" in low:
        return ("The cluster address in the connection string could not be resolved. Copy the string again from "
                "Atlas (Connect, then Drivers) and check your internet connection.")
    if kind in {"ServerSelectionTimeoutError", "NetworkTimeout", "AutoReconnect"}:
        return ("Could not reach the cluster within the time limit. The usual causes are: this computer's IP "
                "address is not in Atlas's IP Access List (or the temporary entry expired), the cluster is paused "
                "(resume it in Atlas), or there is no internet connection.")
    return f"{kind}: {scrub(text)[:300]}"
