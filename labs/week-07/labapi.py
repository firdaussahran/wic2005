#!/usr/bin/env python3
"""Week 7 lab: a small practice REST API for a network — VLANs, ACL entries and devices.

Run it in its own container (see the lab), with the last 4 digits of your student ID:
    python3 labapi.py --id NNNN

It listens on port 8000 and behaves like a (very) small network controller's API:

  POST /api/v1/auth/token          log in: {"username": ..., "password": ...} → a bearer token
  /api/v1/vlans, /api/v1/vlans/ID  VLANs: GET, POST (collection); GET, PUT, PATCH, DELETE (one VLAN)
  /api/v1/acl/entries[/ID]         ACL entries: GET, POST (collection); GET, DELETE (one entry)
  /api/v1/devices                  your devices, a page at a time
  /openapi.json                    the API's documentation, as OpenAPI 3.1

Two accounts: netops / netops-pass (read-write) and viewer / viewer-pass (read-only). Lab
passwords only: they protect nothing, and you'd never put a real one in a file like this.

For the lab's experiments, /api/v1/lab/... (no token needed) can make the server misbehave on
purpose — lose responses, return 503s — and shows an audit log of every change it applied.

Standard library only, so it runs in any Python 3.10+ container. Everything is kept in memory:
restart the server and it's back to its starting state.
"""
import argparse
import base64
import hashlib
import ipaddress
import json
import random
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

USERS = {"netops": ("netops-pass", "read-write"), "viewer": ("viewer-pass", "read-only")}
SITES = ["kl-hq", "pj-dc", "penang", "johor"]
MODELS = ["C9300-48P", "C9200L-24T", "N9K-C93180YC", "ISR4331"]
MAX_LIMIT, DEFAULT_LIMIT = 50, 20
NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
PROTOCOLS = {"any", "tcp", "udp", "icmp"}


class State:
    """Everything the API knows. One lock: the server handles requests in threads."""

    def __init__(self, nn, token_ttl, rate):
        self.lock = threading.Lock()
        self.nn, self.token_ttl, self.rate = nn, token_ttl, rate
        self.rng = random.Random()  # chaos is meant to be unpredictable; the devices are not
        self.reset()

    def reset(self):
        self.vlans = {1: "default", 10: "users", 20: "servers", 99: "old-guest"}
        self.acl, self.next_acl = {}, 1
        self.idem = {}  # Idempotency-Key -> (fingerprint of the request, status, body, headers)
        self.tokens = {}  # token -> (user, role, expiry)
        self.buckets = {}  # token -> (tokens left, last refill)
        self.audit, self.seq = [], 0
        self.chaos = {"lose_responses": 0.0, "errors_503": 0.0}
        self.devices = make_devices(self.nn)

    def record(self, method, path, status, what):
        self.seq += 1
        self.audit.append({"seq": self.seq, "time": time.strftime("%H:%M:%S"), "method": method, "path": path,
                           "status": status, "change": what})


def make_devices(nn):
    """200 + NN devices, the same every time for the same student ID."""
    rng = random.Random(nn)
    out = []
    for k in range(200 + nn):
        site = SITES[k % len(SITES)]
        n = k // len(SITES) + 1
        out.append({
            "name": f"sw-{site}-{n:03d}",
            "site": site,
            "model": rng.choice(MODELS),
            "mgmt_ip": f"10.{nn}.{200 + SITES.index(site)}.{n}",
            "os_version": f"17.{rng.randint(3, 12)}.{rng.randint(1, 5)}",
        })
    return sorted(out, key=lambda d: d["name"])


def cursor_for(name):
    return base64.urlsafe_b64encode(name.encode()).decode().rstrip("=")


def name_from(cursor):
    return base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()


def valid_cidr(v):
    try:
        ipaddress.ip_network(v, strict=True)
        return True
    except (ValueError, TypeError):
        return False


def check_acl(body):
    if not isinstance(body, dict):
        return "the body must be a JSON object"
    if body.get("action") not in ("permit", "deny"):
        return "action must be 'permit' or 'deny'"
    for f in ("src", "dst"):
        if not valid_cidr(body.get(f)):
            return f"{f} must be a network in CIDR form, such as 10.0.10.0/24"
    proto = body.get("protocol", "any")
    if proto not in PROTOCOLS:
        return f"protocol must be one of {sorted(PROTOCOLS)}"
    port = body.get("port")
    if port is not None and (proto not in ("tcp", "udp") or not isinstance(port, int) or not 1 <= port <= 65535):
        return "port is only for tcp or udp, and must be 1 to 65535"
    if not isinstance(body.get("description", ""), str):
        return "description must be a string"
    return None


def make_handler(state):
    class Handler(BaseHTTPRequestHandler):
        server_version = "labapi/1.0"
        protocol_version = "HTTP/1.1"

        # ---------- plumbing ----------
        def log_message(self, fmt, *args):
            print(f"{time.strftime('%H:%M:%S')} {self.client_address[0]} {fmt % args}", flush=True)

        def send(self, status, body=None, headers=()):
            data = b"" if body is None else (json.dumps(body, indent=2) + "\n").encode()
            self.send_response(status)
            if body is not None:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            for k, v in headers:
                self.send_header(k, v)
            self.end_headers()
            if data and self.command != "HEAD":
                self.wfile.write(data)
            return status

        def error(self, status, msg, headers=()):
            return self.send(status, {"error": msg}, headers)

        def body(self):
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            if not raw:
                return None, None
            try:
                return json.loads(raw), raw
            except ValueError:
                return ..., raw

        def lose_response(self):
            """Chaos: the change is applied, then the connection drops before any reply."""
            self.close_connection = True

        def auth(self, write):
            h = self.headers.get("Authorization", "")
            if not h.startswith("Bearer "):
                self.error(401, "authentication required: log in at POST /api/v1/auth/token, then send "
                                "'Authorization: Bearer <token>'", [("WWW-Authenticate", 'Bearer realm="labapi"')])
                return None
            tok = h[7:].strip()
            info = state.tokens.get(tok)
            if not info:
                self.error(401, "unknown token", [("WWW-Authenticate", 'Bearer realm="labapi", error="invalid_token"')])
                return None
            user, role, exp = info
            if time.time() > exp:
                self.error(401, "token expired: log in again",
                           [("WWW-Authenticate", 'Bearer realm="labapi", error="invalid_token", error_description="expired"')])
                return None
            # Rate limit: a bucket of `rate` requests per token, refilled continuously.
            left, last = state.buckets.get(tok, (float(state.rate), time.time()))
            now = time.time()
            left = min(float(state.rate), left + (now - last) * state.rate)
            if left < 1:
                state.buckets[tok] = (left, now)
                self.error(429, f"too many requests: at most {state.rate} a second", [("Retry-After", "1")])
                return None
            state.buckets[tok] = (left - 1, now)
            if write and role != "read-write":
                self.error(403, f"{user} has a read-only token")
                return None
            return user

        # ---------- dispatch ----------
        def do_GET(self):
            self.route("GET")

        def do_HEAD(self):
            self.route("HEAD")

        def do_POST(self):
            self.route("POST")

        def do_PUT(self):
            self.route("PUT")

        def do_PATCH(self):
            self.route("PATCH")

        def do_DELETE(self):
            self.route("DELETE")

        def route(self, method):
            url = urlparse(self.path)
            path, query = url.path.rstrip("/") or "/", parse_qs(url.query)
            with state.lock:
                if path == "/openapi.json" and method in ("GET", "HEAD"):
                    return self.send(200, openapi(state.nn))
                if path in ("/", "/api", "/api/v1"):
                    return self.send(200, {"name": "WIC2005 lab API", "version": "v1", "docs": "/openapi.json",
                                           "resources": ["/api/v1/vlans", "/api/v1/acl/entries", "/api/v1/devices"],
                                           "login": "POST /api/v1/auth/token"})
                if path.startswith("/api/v1/lab"):
                    return self.lab(method, path)
                if path == "/api/v1/auth/token":
                    return self.login(method)
                if not path.startswith("/api/v1/"):
                    return self.error(404, f"no such resource: {path}")
                if method != "HEAD" and state.chaos["errors_503"] and state.rng.random() < state.chaos["errors_503"]:
                    return self.error(503, "temporarily unavailable: try again shortly", [("Retry-After", "1")])
                if path == "/api/v1/vlans" or path.startswith("/api/v1/vlans/"):
                    return self.vlans(method, path)
                if path == "/api/v1/acl/entries" or path.startswith("/api/v1/acl/entries/"):
                    return self.acl(method, path)
                if path == "/api/v1/devices":
                    return self.devices(method, query)
                return self.error(404, f"no such resource: {path}")

        # ---------- resources ----------
        def login(self, method):
            if method != "POST":
                return self.error(405, "use POST to log in", [("Allow", "POST")])
            body, _ = self.body()
            if not isinstance(body, dict):
                return self.error(400, 'send JSON: {"username": "...", "password": "..."}')
            user = USERS.get(body.get("username"))
            if not user or body.get("password") != user[0]:
                return self.error(401, "wrong username or password", [("WWW-Authenticate", 'Bearer realm="labapi"')])
            tok = "lab_" + secrets.token_hex(16)
            state.tokens[tok] = (body["username"], user[1], time.time() + state.token_ttl)
            return self.send(200, {"token": tok, "token_type": "Bearer", "expires_in": state.token_ttl, "role": user[1]})

        def vlans(self, method, path):
            write = method not in ("GET", "HEAD")
            if self.auth(write) is None:
                return
            items = lambda: [{"id": i, "name": n} for i, n in sorted(state.vlans.items())]
            if path == "/api/v1/vlans":
                if method in ("GET", "HEAD"):
                    return self.send(200, {"items": items(), "next": None})
                if method != "POST":
                    return self.error(405, f"{method} is not allowed on the collection", [("Allow", "GET, POST")])
                body, _ = self.body()
                if body is ... or body is None:
                    return self.error(400, "the request body is not valid JSON")
                if not isinstance(body, dict) or not isinstance(body.get("id"), int) or not 1 <= body["id"] <= 4094:
                    return self.error(422, "id must be a VLAN number from 1 to 4094")
                if not isinstance(body.get("name"), str) or not NAME_RE.match(body["name"]):
                    return self.error(422, "name is required: 1 to 32 letters, digits, '-' or '_'")
                if body["id"] in state.vlans:
                    return self.error(409, f"VLAN {body['id']} already exists: use PUT to replace it")
                state.vlans[body["id"]] = body["name"]
                state.record(method, path, 201, f"created VLAN {body['id']} {body['name']}")
                if self.lost():
                    return
                return self.send(201, {"id": body["id"], "name": body["name"]}, [("Location", f"/api/v1/vlans/{body['id']}")])

            m = re.fullmatch(r"/api/v1/vlans/(-?\d+)", path)
            if not m:
                return self.error(404, f"no such resource: {path}")
            vid = int(m.group(1))
            if method in ("GET", "HEAD"):
                if vid not in state.vlans:
                    return self.error(404, f"VLAN {vid} not found")
                return self.send(200, {"id": vid, "name": state.vlans[vid]})
            if method == "POST":
                return self.error(405, "POST is not allowed on a single VLAN", [("Allow", "GET, PUT, PATCH, DELETE")])
            if method == "DELETE":
                if vid not in state.vlans:
                    return self.error(404, f"VLAN {vid} not found")
                if vid == 1:
                    return self.error(409, "VLAN 1 is the default VLAN and can't be deleted")
                del state.vlans[vid]
                state.record(method, path, 204, f"deleted VLAN {vid}")
                if self.lost():
                    return
                return self.send(204)
            body, _ = self.body()
            if body is ... or body is None:
                return self.error(400, "the request body is not valid JSON")
            if not 1 <= vid <= 4094:
                return self.error(422, "id must be a VLAN number from 1 to 4094")
            if not isinstance(body, dict):
                return self.error(422, "the body must be a JSON object")
            if "id" in body and body["id"] != vid:
                return self.error(422, f"the body's id ({body['id']}) doesn't match the URL ({vid})")
            if method == "PATCH" and vid not in state.vlans:
                return self.error(404, f"VLAN {vid} not found")
            if "name" not in body and method == "PUT":
                return self.error(422, "PUT replaces the whole VLAN: name is required")
            if "name" in body and (not isinstance(body["name"], str) or not NAME_RE.match(body["name"])):
                return self.error(422, "name: 1 to 32 letters, digits, '-' or '_'")
            existed = vid in state.vlans
            old = state.vlans.get(vid)
            state.vlans[vid] = body.get("name", old)
            status = 200 if existed else 201
            if old != state.vlans[vid]:
                state.record(method, path, status, f"{'set' if existed else 'created'} VLAN {vid} {state.vlans[vid]}")
            if self.lost():
                return
            headers = [] if existed else [("Location", f"/api/v1/vlans/{vid}")]
            return self.send(status, {"id": vid, "name": state.vlans[vid]}, headers)

        def acl(self, method, path):
            write = method not in ("GET", "HEAD")
            if self.auth(write) is None:
                return
            if path == "/api/v1/acl/entries":
                if method in ("GET", "HEAD"):
                    return self.send(200, {"items": [state.acl[k] for k in sorted(state.acl)], "next": None})
                if method != "POST":
                    return self.error(405, f"{method} is not allowed on the collection", [("Allow", "GET, POST")])
                body, raw = self.body()
                if body is ... or body is None:
                    return self.error(400, "the request body is not valid JSON")
                key = self.headers.get("Idempotency-Key")
                fp = hashlib.sha256(raw).hexdigest()
                if key:
                    if key in state.idem:
                        seen_fp, status, resp, headers = state.idem[key]
                        if seen_fp != fp:
                            return self.error(422, "this Idempotency-Key was already used with a different request")
                        return self.send(status, resp, headers + [("Idempotent-Replayed", "true")])
                problem = check_acl(body)
                if problem:
                    return self.error(422, problem)
                entry = {"id": state.next_acl, "action": body["action"], "src": body["src"], "dst": body["dst"],
                         "protocol": body.get("protocol", "any"), "port": body.get("port"),
                         "description": body.get("description", "")}
                state.acl[state.next_acl] = entry
                state.next_acl += 1
                headers = [("Location", f"/api/v1/acl/entries/{entry['id']}")]
                if key:
                    state.idem[key] = (fp, 201, entry, headers)
                state.record(method, path, 201, f"added ACL entry {entry['id']}: {entry['action']} {entry['src']} → "
                                                f"{entry['dst']} {entry['protocol']}{'' if entry['port'] is None else '/' + str(entry['port'])}")
                if self.lost():
                    return
                return self.send(201, entry, headers)
            m = re.fullmatch(r"/api/v1/acl/entries/(\d+)", path)
            if not m:
                return self.error(404, f"no such resource: {path}")
            eid = int(m.group(1))
            if method in ("GET", "HEAD"):
                return self.send(200, state.acl[eid]) if eid in state.acl else self.error(404, f"ACL entry {eid} not found")
            if method != "DELETE":
                return self.error(405, f"{method} is not allowed on an ACL entry", [("Allow", "GET, DELETE")])
            if eid not in state.acl:
                return self.error(404, f"ACL entry {eid} not found")
            del state.acl[eid]
            state.record(method, path, 204, f"deleted ACL entry {eid}")
            if self.lost():
                return
            return self.send(204)

        def devices(self, method, query):
            if method not in ("GET", "HEAD"):
                return self.error(405, "devices are read-only here", [("Allow", "GET")])
            if self.auth(False) is None:
                return
            try:
                limit = int(query.get("limit", [DEFAULT_LIMIT])[0])
            except ValueError:
                return self.error(422, "limit must be a number")
            if not 1 <= limit <= MAX_LIMIT:
                return self.error(422, f"limit must be from 1 to {MAX_LIMIT}")
            site = query.get("site", [None])[0]
            if site is not None and site not in SITES:
                return self.error(422, f"site must be one of {SITES}")
            rows = [d for d in state.devices if site is None or d["site"] == site]
            start = 0
            if "cursor" in query:
                try:
                    after = name_from(query["cursor"][0])
                except (ValueError, UnicodeDecodeError):
                    return self.error(400, "that cursor isn't one this API gave you")
                start = next((i + 1 for i, d in enumerate(rows) if d["name"] == after), None)
                if start is None:
                    return self.error(400, "that cursor isn't one this API gave you")
            page = rows[start:start + limit]
            nxt = None
            if start + limit < len(rows):
                q = {"limit": limit, "cursor": cursor_for(page[-1]["name"])}
                if site:
                    q["site"] = site
                nxt = f"http://{self.headers.get('Host', 'api:8000')}/api/v1/devices?{urlencode(q)}"
            return self.send(200, {"items": page, "next": nxt}, [("X-Total-Count", str(len(rows)))])

        # ---------- lab controls ----------
        def lab(self, method, path):
            if path == "/api/v1/lab/audit":
                if method == "GET":
                    return self.send(200, {"writes": len(state.audit), "items": state.audit})
                if method == "DELETE":
                    state.audit = []
                    return self.send(204)
                return self.error(405, "GET to read, DELETE to clear", [("Allow", "GET, DELETE")])
            if path == "/api/v1/lab/chaos":
                if method == "GET":
                    return self.send(200, state.chaos)
                if method in ("PUT", "PATCH"):
                    body, _ = self.body()
                    if not isinstance(body, dict):
                        return self.error(400, 'send JSON such as {"lose_responses": 0.3, "errors_503": 0.2}')
                    for k in ("lose_responses", "errors_503"):
                        if k in body:
                            v = body[k]
                            if not isinstance(v, (int, float)) or not 0 <= v <= 0.9:
                                return self.error(422, f"{k} must be a probability from 0 to 0.9")
                            state.chaos[k] = float(v)
                        elif method == "PUT":
                            state.chaos[k] = 0.0
                    return self.send(200, state.chaos)
                return self.error(405, "GET, PUT or PATCH", [("Allow", "GET, PUT, PATCH")])
            if path == "/api/v1/lab/reset" and method == "POST":
                state.reset()
                return self.send(200, {"reset": True})
            return self.error(404, f"no such lab control: {path}")

        def lost(self):
            if state.chaos["lose_responses"] and state.rng.random() < state.chaos["lose_responses"]:
                self.lose_response()
                return True
            return False

    return Handler


def openapi(nn):
    """The API's documentation, as an OpenAPI 3.1 document."""
    bearer = [{"bearerAuth": []}]
    err = lambda d: {"description": d, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}}
    page = lambda ref: {"type": "object", "properties": {"items": {"type": "array", "items": {"$ref": ref}},
                                                         "next": {"type": ["string", "null"],
                                                                  "description": "Full URL of the next page; null on the last page."}}}
    return {
        "openapi": "3.1.0",
        "info": {"title": "WIC2005 lab API", "version": "1.0",
                 "description": f"A practice network controller API for the Week 7 lab (student block 10.{nn}.0.0/16). "
                                f"Rate limit: requests are limited per token; over the limit you get 429 with Retry-After."},
        "servers": [{"url": "http://api:8000"}],
        "components": {
            "securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer",
                                               "description": "Get a token from POST /api/v1/auth/token. Tokens expire."}},
            "schemas": {
                "Error": {"type": "object", "properties": {"error": {"type": "string"}}},
                "Vlan": {"type": "object", "required": ["id", "name"],
                         "properties": {"id": {"type": "integer", "minimum": 1, "maximum": 4094},
                                        "name": {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,32}$"}}},
                "AclEntry": {"type": "object", "required": ["action", "src", "dst"],
                             "properties": {"id": {"type": "integer", "readOnly": True},
                                            "action": {"enum": ["permit", "deny"]},
                                            "src": {"type": "string", "description": "CIDR, e.g. 10.0.10.0/24"},
                                            "dst": {"type": "string", "description": "CIDR"},
                                            "protocol": {"enum": ["any", "tcp", "udp", "icmp"], "default": "any"},
                                            "port": {"type": ["integer", "null"], "minimum": 1, "maximum": 65535,
                                                     "description": "tcp or udp only"},
                                            "description": {"type": "string"}}},
                "Device": {"type": "object", "properties": {"name": {"type": "string"}, "site": {"type": "string"},
                                                            "model": {"type": "string"}, "mgmt_ip": {"type": "string"},
                                                            "os_version": {"type": "string"}}},
            },
        },
        "paths": {
            "/api/v1/auth/token": {"post": {
                "summary": "Log in and get a bearer token",
                "requestBody": {"content": {"application/json": {"schema": {
                    "type": "object", "properties": {"username": {"type": "string"}, "password": {"type": "string"}}}}}},
                "responses": {"200": {"description": "token, token_type, expires_in (seconds), role"},
                              "401": err("Wrong username or password.")}}},
            "/api/v1/vlans": {
                "get": {"summary": "List VLANs", "security": bearer, "responses": {"200": {"description": "All VLANs."}}},
                "post": {"summary": "Create a VLAN", "security": bearer,
                         "requestBody": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Vlan"}}}},
                         "responses": {"201": {"description": "Created; Location says where."},
                                       "409": err("That VLAN already exists."), "422": err("Invalid id or name.")}}},
            "/api/v1/vlans/{id}": {
                "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}],
                "get": {"summary": "Read one VLAN", "security": bearer, "responses": {"200": {"description": "The VLAN."}, "404": err("No such VLAN.")}},
                "put": {"summary": "Create or replace a VLAN (idempotent)", "security": bearer,
                        "responses": {"200": {"description": "Replaced."}, "201": {"description": "Created."}}},
                "patch": {"summary": "Change some fields of an existing VLAN", "security": bearer,
                          "responses": {"200": {"description": "Changed."}, "404": err("No such VLAN.")}},
                "delete": {"summary": "Delete a VLAN (VLAN 1 can't be deleted)", "security": bearer,
                           "responses": {"204": {"description": "Deleted."}, "404": err("No such VLAN."),
                                         "409": err("VLAN 1 is the default VLAN.")}}},
            "/api/v1/acl/entries": {
                "get": {"summary": "List ACL entries", "security": bearer, "responses": {"200": {"description": "All entries."}}},
                "post": {"summary": "Append an ACL entry (not idempotent)", "security": bearer,
                         "parameters": [{"name": "Idempotency-Key", "in": "header", "required": False,
                                         "schema": {"type": "string"},
                                         "description": "A unique key for this change. Retrying with the same key and the same "
                                                        "body returns the first result instead of adding the entry again "
                                                        "(response header Idempotent-Replayed: true). The same key with a "
                                                        "different body is rejected with 422."}],
                         "requestBody": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/AclEntry"}}}},
                         "responses": {"201": {"description": "Added; Location says where."}, "422": err("Invalid entry.")}}},
            "/api/v1/acl/entries/{id}": {
                "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}],
                "get": {"summary": "Read one ACL entry", "security": bearer, "responses": {"200": {"description": "The entry."}}},
                "delete": {"summary": "Delete one ACL entry", "security": bearer,
                           "responses": {"204": {"description": "Deleted."}, "404": err("No such entry.")}}},
            "/api/v1/devices": {"get": {
                "summary": "List devices, a page at a time", "security": bearer,
                "parameters": [
                    {"name": "limit", "in": "query", "schema": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT,
                                                                "default": DEFAULT_LIMIT}},
                    {"name": "cursor", "in": "query", "schema": {"type": "string"},
                     "description": "Opaque: take it from the previous page's next URL."},
                    {"name": "site", "in": "query", "schema": {"enum": SITES}, "description": "Only devices at this site."}],
                "responses": {"200": {"description": "One page. Header X-Total-Count: how many devices match in all.",
                                      "content": {"application/json": {"schema": page("#/components/schemas/Device")}}},
                              "401": err("Missing, invalid or expired token."),
                              "422": err("limit or site out of range."),
                              "429": err("Rate limited: wait the seconds in Retry-After."),
                              "503": err("Temporarily unavailable: retry with backoff.")}}},
        },
    }


def main():
    ap = argparse.ArgumentParser(description="Week 7 practice API")
    ap.add_argument("--id", required=True, help="the last 4 digits of your student ID")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--token-ttl", type=int, default=600, help="seconds a token lasts (default 600)")
    ap.add_argument("--rate", type=int, default=5, help="requests a second allowed per token (default 5)")
    args = ap.parse_args()
    if not re.fullmatch(r"\d{4}", args.id):
        raise SystemExit("--id must be the last 4 digits of your student ID, e.g. --id 4207")
    nn = int(args.id[2:])
    state = State(nn, args.token_ttl, args.rate)
    srv = ThreadingHTTPServer(("0.0.0.0", args.port), make_handler(state))
    print(f"Lab API for student …{args.id}: {len(state.devices)} devices on 10.{nn}.0.0/16, "
          f"tokens last {args.token_ttl}s, {args.rate} requests/s per token. Listening on port {args.port}.", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
