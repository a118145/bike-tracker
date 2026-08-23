"""A local stand-in for the Hammerhead API, built from its OpenAPI spec.

Why a real server and not a mocked transport: every failure this client has to
survive is a network condition. A `MockTransport` returning canned objects
exercises the parsing and none of the plumbing — it never streams a binary
body, never rotates a refresh token across two real requests, and cannot show
that a "known" activity was skipped, because that is a statement about which
requests were *made*.

Implements only what the poller uses:

* ``POST /v1/auth/oauth/token``      — authorization_code and refresh_token
* ``POST /v1/auth/oauth/deauthorize``
* ``GET  /v1/api/activities``        — paginated, honours startDate
* ``GET  /v1/api/activities/{id}/file``

Field names and shapes are copied from ``api.hammerhead.io/v1/docs/openapi.yml``
verbatim: ``perPage``/``totalPages``/``createdAt`` and not the snake_case a
Python author would reach for. Getting that wrong in the fake would hide
getting it wrong in the client.
"""

import json
import threading
from dataclasses import dataclass, field
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ACCESS_TOKEN = "access-1"
REFRESH_TOKEN = "refresh-1"
USER_ID = "1000"
VALID_CODE = "the-code"
EXPIRES_IN = 52000


@dataclass
class FakeActivity:
    id: str
    name: str
    created_at: datetime
    payload: bytes


@dataclass
class FakeHammerhead:
    """State and knobs for one fake server instance."""

    activities: list[FakeActivity] = field(default_factory=list)

    # Every request the client made, as (method, path). The assertion surface
    # for "a known activity is not downloaded" — a test on the ride count
    # passes whether or not the download happened, which is exactly how that
    # bug would survive.
    requests: list[tuple[str, str]] = field(default_factory=list)

    # Knobs. Each is consumed or persistent as named.
    per_page: int = 2
    fail_list_times: int = 0
    fail_download_ids: set[str] = field(default_factory=set)
    # 401 on the download only, with the list still answering. The list
    # is requested first, so `unauthorized` alone never reaches the
    # per-activity path.
    unauthorized_download_ids: set[str] = field(default_factory=set)
    unauthorized: bool = False
    # Hammerhead may hand back a new refresh token on every exchange. Rotating
    # here is what proves the client persists it.
    rotate_refresh: bool = True
    # What the token response reports as granted. `None` omits the field
    # entirely, which is what the published spec describes.
    grant_scope: str | None = "activity:read"

    _token_serial: int = 1
    _refresh_serial: int = 1

    def current_access_token(self) -> str:
        return f"{ACCESS_TOKEN}.{self._token_serial}"

    def current_refresh_token(self) -> str:
        return f"{REFRESH_TOKEN}.{self._refresh_serial}"

    def issue_tokens(self) -> dict:
        self._token_serial += 1
        if self.rotate_refresh:
            self._refresh_serial += 1
        body = {
            "token_type": "Bearer",
            "access_token": self.current_access_token(),
            "refresh_token": self.current_refresh_token(),
            "expires_in": EXPIRES_IN,
            "user_id": USER_ID,
        }
        if self.grant_scope is not None:
            body["scope"] = self.grant_scope
        return body

    def visible(self, start: date | None) -> list[FakeActivity]:
        rows = sorted(self.activities, key=lambda a: a.created_at)
        if start is not None:
            rows = [a for a in rows if a.created_at.date() >= start]
        return rows


class _Handler(BaseHTTPRequestHandler):
    server: "_Server"

    # Silence the default stderr access log; the request list is the record.
    def log_message(self, *args: object) -> None:
        return

    @property
    def fake(self) -> FakeHammerhead:
        return self.server.fake

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        self.fake.requests.append(("POST", path))
        length = int(self.headers.get("Content-Length", "0"))
        form = parse_qs(self.rfile.read(length).decode())

        if path.endswith("/oauth/token"):
            self._token(form)
        elif path.endswith("/oauth/deauthorize"):
            self._json(200, {})
        else:
            self._json(404, {"message": "Not Found"})

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        self.fake.requests.append(("GET", parsed.path))

        if self.fake.unauthorized or not self._bearer_ok():
            self._json(401, {"message": "Unauthorized"})
            return

        if parsed.path.endswith("/activities"):
            self._activities(parse_qs(parsed.query))
        elif parsed.path.endswith("/file"):
            self._file(parsed.path.rsplit("/", 2)[-2])
        else:
            self._json(404, {"message": "Not Found"})

    # --- endpoints --------------------------------------------------------

    def _token(self, form: dict[str, list[str]]) -> None:
        grant = form.get("grant_type", [""])[0]
        if grant == "authorization_code":
            if form.get("code", [""])[0] != VALID_CODE:
                self._json(400, {"error": "invalid_grant"})
                return
        elif grant == "refresh_token":
            if form.get("refresh_token", [""])[0] != self.fake.current_refresh_token():
                self._json(400, {"error": "invalid_grant"})
                return
        else:
            self._json(400, {"error": "unsupported_grant_type"})
            return
        self._json(200, self.fake.issue_tokens())

    def _activities(self, query: dict[str, list[str]]) -> None:
        if self.fake.fail_list_times > 0:
            self.fake.fail_list_times -= 1
            self._json(503, {"message": "Service Unavailable"})
            return

        start = query.get("startDate", [None])[0]
        rows = self.fake.visible(date.fromisoformat(start) if start else None)

        per_page = self.fake.per_page
        page = int(query.get("page", ["1"])[0])
        total_pages = max(1, -(-len(rows) // per_page))
        chunk = rows[(page - 1) * per_page : page * per_page]

        self._json(
            200,
            {
                "totalItems": len(rows),
                "totalPages": total_pages,
                "perPage": per_page,
                "currentPage": page,
                "data": [
                    {
                        "id": a.id,
                        "name": a.name,
                        # The spec's own format, Z suffix included.
                        "createdAt": a.created_at.isoformat(timespec="milliseconds") + "Z",
                        "duration": 3600,
                        "distance": 12345.0,
                    }
                    for a in chunk
                ],
            },
        )

    def _file(self, activity_id: str) -> None:
        if activity_id in self.fake.unauthorized_download_ids:
            self._json(401, {"message": "Unauthorized"})
            return
        if activity_id in self.fake.fail_download_ids:
            self._json(500, {"message": "Internal Server Error"})
            return
        match = next((a for a in self.fake.activities if a.id == activity_id), None)
        if match is None:
            self._json(404, {"message": "Not Found"})
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/vnd.ant.fit")
        self.send_header("Content-Length", str(len(match.payload)))
        self.end_headers()
        self.wfile.write(match.payload)

    # --- helpers ----------------------------------------------------------

    def _bearer_ok(self) -> bool:
        header = self.headers.get("Authorization", "")
        return header == f"Bearer {self.fake.current_access_token()}"

    def _json(self, status: int, body: dict) -> None:
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, fake: FakeHammerhead) -> None:
        # Port 0: the OS picks a free one, so a leftover server from a crashed
        # run cannot make the next run fail with "address already in use".
        super().__init__(("127.0.0.1", 0), _Handler)
        self.fake = fake


class RunningFake:
    """A started fake server, with the two base URLs the client needs."""

    def __init__(self, fake: FakeHammerhead) -> None:
        self.fake = fake
        self._server = _Server(fake)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def origin(self) -> str:
        # server_address is typed as a generic address tuple, so the host may
        # be bytes as far as the checker is concerned. It is not, here.
        host, port = self._server.server_address[:2]
        return f"http://{host!s:}:{port}" if isinstance(host, str) else f"http://127.0.0.1:{port}"

    @property
    def auth_base(self) -> str:
        return f"{self.origin}/v1/auth"

    @property
    def api_base(self) -> str:
        return f"{self.origin}/v1/api"

    def paths(self, needle: str) -> list[str]:
        """Every requested path containing ``needle``. The assertion surface."""
        return [path for _, path in self.fake.requests if needle in path]

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def __enter__(self) -> "RunningFake":
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()
