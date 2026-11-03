# Shelfa Group administration

## Architecture

The repository root app in `xvsx.py` starts the separate Shelfa and Shelfa
Group FastAPI services and serves Shelfa Group web assets from `/web`. The
Group mobile API is mounted at `/shelfa`. `shelfa_group/shelfa.py` initializes
its SQLite database and includes both the app and admin routers. The existing
`user`, `message`, and `contact_registry` tables remain the source of truth;
the admin panel uses parameterized queries in `shelfa_group/db.py` and does not
expose device IDs or Firebase notification tokens. Database initialization
adds the account-block flag and creates `admin_audit` and
`admin_login_attempt` tables in existing databases.

The browser UI is served at `/admin`; its static HTML, CSS, and JavaScript live
in `shelfa_group/web/`. JSON endpoints are under `/admin/api/`. User, message,
contact, schema, and audit lists support bounded pagination; the directory
views support search and account/message filters. Deleting a user also deletes
their messages and contacts.

## Configure administrator access

Set these environment variables on the server before starting the application:

- `SHELFA_ADMIN_USERNAME`: the single administrator username.
- `SHELFA_ADMIN_PASSWORD`: a password of at least 12 characters.
- `SHELFA_ADMIN_SECRET_KEY`: a random secret of at least 32 UTF-8 bytes, used
  to sign administrator sessions. Generate one with `openssl rand -hex 32`.

There is deliberately no built-in/default administrator account. The admin
interface remains unavailable until all three values meet the requirements.
Use HTTPS in deployment. The session cookie is HTTP-only, `SameSite=Strict`,
and `Secure` by default. For local HTTP development only, set
`SHELFA_ADMIN_COOKIE_SECURE=false`.

The admin API uses an eight-hour signed session, a per-client sign-in throttle,
and a CSRF token required for state-changing requests. Credential values and
session tokens are not written to the audit log. Successful sign-ins, failed
sign-in attempts, sign-outs, and data changes are recorded in `admin_audit`.

## Account and API behavior

Creating an account provisions its nickname and type without a device token;
the first app registration can claim that empty device slot. Blocking an
account prevents registration, token updates, messaging, and contact actions
for requests that reference the blocked nickname.

The legacy mobile API does not authenticate message and contact requests with
a device credential. Consequently, nickname-based blocking is not a substitute
for end-user identity verification and does not protect against a client
impersonating a different nickname. File upload/download routes likewise have
no account identity. These existing API contracts need a coordinated
mobile-client authentication change before those operations can enforce
per-account ownership.
