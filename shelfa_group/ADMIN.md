# Shelfa Group administration

## Architecture

The repository root app in `xvsx.py` starts the separate Shelfa and Shelfa
Group FastAPI services and serves Shelfa Group web assets from `/web`. The
Group mobile API is mounted at `/shelfa`. `shelfa_group/shelfa.py` initializes
its SQLite database and includes both the app and admin routers. The existing
`user`, `message`, and `contact_registry` tables remain the source of truth;
the admin panel uses parameterized queries in `shelfa_group/db.py` and does not
expose device IDs or Firebase notification tokens. Database initialization
adds the account-block flag and account mode to existing users, initializes
Free and Limited usage limits, and creates `admin_audit` and
`admin_login_attempt` tables in existing databases.

The browser UI is served at `/admin`; its static HTML, CSS, and JavaScript live
in `shelfa_group/web/`. JSON endpoints are under `/admin/api/`. User, message,
contact, schema, and audit lists support bounded pagination; the directory
views support search and account/message filters. Deleting a user also deletes
their messages and contacts.

Administrators can assign Free, Limited, or Premium mode while creating or
editing an account. Free and Limited contact and per-contact message-history
limits can be changed in the Users view; the defaults are 5 contacts and 50
messages for Free, and 50 contacts and 500 messages for Limited. A zero limit
disables the corresponding feature. Premium accounts have no usage limits.
Contact lists expose only the most recent contacts within the account's limit,
and new contact records are rejected when either participant has reached their
limit. Message history requests expose only the most recent configured number
of messages for each conversation; older messages remain stored and become
available again if the limit is raised or the account is upgraded.

## Audio and video calls

The Android/iOS client connects directly to LiveKit, while this backend issues
short-lived room participant tokens and uses Firebase Cloud Messaging (FCM) for
incoming call invites and call-state signals. Configure these server-side
environment variables before enabling calls:

- `LIVEKIT_URL`: the LiveKit `wss://` server URL (local development may use
  `ws://`).
- `LIVEKIT_API_KEY`: the LiveKit API key.
- `LIVEKIT_API_SECRET`: the LiveKit API secret (at least 32 bytes).

The client posts to `/shelfa/call` with `sender_nickname`, `device_id`,
`recipient_nickname`, `call_type` (`audio` or `video`), and `action`
(`start`, `join`, `end`, `decline`, `cancel`, or `busy`); all actions except
`start` also include `call_id`. The backend verifies that `device_id` matches
the sender's registered device and that call participants are contacts. Update
the client request to include its registered device ID. API keys and secrets
must remain server-side.

`start` returns a `call_id`, room name, LiveKit URL, and caller token, then
sends the callee an FCM invite. `join` returns a token for the same room.
Tokens use opaque participant identities and expire after four hours. Call
invites use alert pushes; end, decline, cancel, and busy signals use data-only
background pushes.

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
