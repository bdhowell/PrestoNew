# Chatbot Details

Like page-specific CSS and JavaScript, each webpage loaded into the iframe can
load `/static/javascript/pages/{page}.does`, using the same route-supplied page
slug. The file is included only if it exists, after the core client and the
page's ordinary JavaScript.

A `.does` file contains executable JavaScript, not JSON, and is served with the
`text/javascript` content type. It registers commands using direct function
references or inline functions, rather than string-valued function names:

```javascript
window.registerWikkitCommands({
    commands: {
        highlight: {
            does: (data) => highlightSection(data),
            data: { section: "introduction" }
        }
    }
});
```

`does` can also be an existing function reference, such as `does: highlightSection`.
The handler receives one argument: an object, an array of objects, or null.
An array is passed intact, not spread into multiple arguments. A command's
`payload` overrides the registered `data`; omitted payload uses that default,
and explicit null is passed through. Registrations belong to the iframe page
and are discarded when that page unloads.

Page-specific commands are registered in the `.does` files and override intrinsic commands with the same key
Pages can also dynamically register or unregister commands at runtime using the same `window.registerWikkitCommands` API.

## Server replies

User input is processed by the conversation's `IntentStack`. The server sends
the following protocol; both `say` and `command` are optional:

```json
{
    "say": "Here is your introduction.",
    "command": {
        "does": "highlight",
        "payload": { "section": "introduction" }
    }
}
```

The server sends stable command keys and JSON data, never JavaScript source.
The client accepts legacy `user`/`client` replies and `action` command keys
during migration. The server also normalizes legacy intent-handler results.

An intent handler may return `{"responses": ["First response", "Second response"]}`;
one string is randomly selected as `say` on each dispatch. A command may
accompany those responses.

Page-specific (extrinsic) commands override intrinsic commands with the same
key. Handlers can be async, and commands execute in arrival order. Commands
received before the iframe connects wait for it and its scripts to load.
Unknown commands and execution errors are logged and shown as a chatbot notice.

## Intrinsic commands

- `clear-chat`: clear the visible chat log.
  Account AJAX replies can include `command`; the page awaits
  `window.wikkit.execute(command)` before displaying `say` or redirecting.
- `redirect`: navigate the iframe to the same-origin URL in the string payload.
- `request-response`: start waiting for user input on the client.
  `request` is an alias for the example's original command key.

While a response is requested, the client displays a reminder after 120,000 ms
of inactivity, and again every 120,000 ms. Typing resets the timer. Sending a
nonempty response or closing the socket stops reminders. Rejected overlong
responses leave the server waiting and restart client reminders. There is no
response timeout.

## Server-side chatbot logic

The root `wikkit.py` module owns reply normalization and a `Chatbot` instance
per websocket. The router makes that instance available as
`websocket.state.chatbot`.

- `await chatbot.send(reply)` sends a reply at any time on the socket's event
  loop, without requiring user input.
- `await chatbot.request_response(say=None)` sends the `request-response`
  command, optionally says a prompt, and suspends the calling workflow until
  the next nonempty, length-valid user message arrives. That message is
  returned to the workflow instead of being dispatched as a new intent.
- Override `Chatbot.handle_message` for async conversation workflows that need
  to ask questions and then continue. Ordinary messages continue through the
  existing intent stack.

The receive loop remains active while a workflow waits. Only one response
request may be outstanding per conversation, and disconnecting cancels
pending response waits and the message-processing task.

## Page workflows

Pages rendered in the iframe extend `templates/page.html`, which sets
`<body data-page="slug" data-signed-in="true|false">`. When a page attaches,
the client sends the control frame `{"page": "slug"}`; `wikkit.page_event`
recognizes only that exact shape. `Chatbot.open_page(slug)` cancels and awaits
the previous page's workflow, then starts the coroutine registered for the slug
in `routers.wikkit.WORKFLOWS` (currently `wikkit-magic` and `wikkit-pricing`).
Pages without a workflow leave messages to the intent stack. Workflow
exceptions are logged and the user sees `FAILURE_MESSAGE`.

Static page greetings ("Wikkit says") are rendered by the HTTP route into the
page and spoken by `window.wikkit.say(...)`, so they reflect the request's
current session. The websocket's session is a connect-time snapshot, so state
that must survive (the visitor's name, sign-in) is written over HTTP: the
magic workflow sends the `remember-name` command from
`static/javascript/pages/wikkit-magic.does`, which posts JSON to
`POST /wikkit/name` to set the name cookie.

`data-signed-in` updates the shell menu's Sign in/Sign out link. Menu clicks
navigate the iframe. Successful sign-in, password recovery, and account deletion
AJAX replies also include a boolean `signed_in`; the account client calls
`window.wikkit.setSignedIn(...)` before redirecting, so the menu updates even
if the destination page cannot load. Sign out clears the server-side session
and its page reports `data-signed-in="false"` to restore the Sign in link.
A page opened directly at top level posts its path to
`/wikkit` so it reopens inside the shell, so a top-level GET is loaded twice;
single-use links (such as account confirmation) redirect to a token-free page
after use.


# Chatbot memory:
- Stores user responses and context for ongoing interactions.
- Keeps track of the current state of the conversation.
- Can be used to recall previous user inputs when needed.
- Can be used to maintain context across multiple steps in a conversation.

# Backed KV Store
Write a Python KV store that stores its values in Postgres.
- The chatbot uses a KV store backed by a database to persist long-term and user memory.
- Loaded into the session when the chatbot is initialized.
  - Pickled with the session for persistence.
- Initializes with an optional parent key table.
- If the parent key table was provided, identify name and data type of its primary key.
- If the table doesn't exist, create it.
  - If the table is created and a parent key table was provided, add a foreign key to its parent table with the same name and data type as the parent's primary key.
- `kvstore.set(key, value, [foreign_key_value])` sets a value in the KV store, optionally with a foreign key.
- (value, foreign_key_value|None) = kvstore.get(key)
- When the KV store is initialized, get the primary key name and data type of its foreign key, if applicable.
  - The name of that field becomes the name of the foreign key parameter, looked up using kwargs.
  - The data type of that field becomes the data type of the foreign key column in the KV store table.

## Session Memory
Short-term memory stores information relevant to the current conversation session.
- A Python dictionary used to keep track of recent user inputs and context that may influence the chatbot's responses.

## Poster Memory
Long-term memory stores information that persists across multiple conversation sessions.
- It is used to maintain context and recall user inputs from previous interactions.
- It is a Backed KV Store whose parent table is poster

## User Memory
User memory stores information that is accessible across all sessions for a particular user.
It is used to maintain context and recall user inputs specific to an individual user.
- It is a KV store implemented in Postgres and associated with a particular user.

## Short-Term, Long-Term, and User Memory
Each made available as part of the execution context when handling web requests and chat interactions.
- Short-term memory as short_term_memory
- Long-term memory as long_term_memory
- User memory as user_memory

## Memory implementation and integration

`kvstore.KVStore` provides synchronous PostgreSQL-backed JSON storage. Install
the driver with `bin/python -m pip install -r requirements-memory.txt` and set
`DATABASE_URL`. The database role needs schema access and table/index creation
permissions. Parent tables must already exist in the selected schema and have a
single-column primary key. Composite keys and incompatible existing KV schemas
are rejected rather than silently migrated.

```python
from kvstore import KVStore

store = KVStore("poster_memory", "poster")
store.set("title", "My poster", pid=42)  # pid is discovered, not hard-coded
value, owner = store.get("title", pid=42)

# Bound stores use their owner by default and reject other owners.
poster_memory = KVStore("poster_memory", "poster", foreign_key_value=42)
poster_memory.set("title", "Updated title")
value, owner = poster_memory.get("title")
```

The optional foreign-key value can also be positional. Unbound stores with no
owner argument access the unowned scope; they never search other owners.
Each `(key, owner)` is unique, including the unowned scope, and writes use atomic
upserts. Missing keys raise `KeyError`; a stored JSON null returns `(None, owner)`.
Values must be JSON serializable. Foreign-key values retain their PostgreSQL
types (for example, integer or UUID). Identifiers are quoted and values are
parameterized. Existing tables are inspected for their foreign key even when
`parent_table` is omitted. Other schemas are supported with `schema=...`; the
parent is resolved in that same schema.

`memory.Memory` holds the dictionary `short_term_memory` and optional bound
`long_term_memory` (poster) and `user_memory` stores. It is pickleable, but it
contains no sockets, tasks, locks, database connections, or database credentials.
Store handles retain only the database environment-variable name and reopen
connections for operations. Credentials must remain available in the environment
after restoring a session. Values are read from PostgreSQL on demand, not cached
in session snapshots.

The application's `memory_context` dependency exposes the same three names on
`request.state` and `websocket.state`, and on the websocket's `Chatbot` instance.
Intent handlers and other downstream code can use `memory.current_memory()` to
access those names without changing existing handler signatures. Context is
propagated into the intent-dispatch threadpool and isolated between requests.
Synchronous KV operations must be run in a threadpool from async workflows:

```python
from fastapi.concurrency import run_in_threadpool
from memory import current_memory

memory = current_memory()
short_term_memory = memory.short_term_memory
if memory.long_term_memory is not None:
    await run_in_threadpool(memory.long_term_memory.set, "title", "My poster")
```

Authentication/authorization code must populate `state.user_id` and/or
`state.poster_id` with authorized positive integer IDs **before** the dependency
runs. IDs are never taken from query strings, forms, or websocket messages.
Absent IDs leave the respective persistent scopes as `None`, rather than sharing
an unowned store. The store is not itself an authorization mechanism.

`sessions.PostgresSessionMiddleware` supplies the trusted server-side mapping as
`scope["session"]`. The dependency loads and saves a `Memory` object under
`session["chatbot_memory"]`. An identity change replaces all three scopes to
avoid retaining another user's/poster's context. A session identifies a user
only after `identity.sign_in` stores a verified `user_id` (see Accounts);
poster access is still a separate integration.

### PostgreSQL session backend

The app enables sessions automatically when `DATABASE_URL` and `SECRET_KEY` are
configured. `SECRET_KEY` must contain at least 32 bytes of random secret data;
generate one with `bin/python -c 'import secrets; print(secrets.token_urlsafe(48))'`
and place it in the deployment's secret configuration, not source control.
Every worker must use the same secret. Partial configuration fails explicitly.

```env
SESSION_BACKEND=auto
SESSION_MAX_AGE=600
SESSION_COOKIE_SECURE=true
```

- `SESSION_BACKEND=postgres` requires database and secret configuration.
- `SESSION_BACKEND=none` explicitly disables persistence for database-free
  development. `auto` also disables persistence, with a warning, when neither
  database nor secret is configured.
- `SESSION_COOKIE_SECURE=true` is the default and requires HTTPS/WSS. Set it to
  `false` only for local HTTP/WS development.
- `SESSION_MAX_AGE` is a positive lifetime in seconds (default 600). Expiry is
  fixed at session creation, not sliding; sessions are not resurrected by old
  websocket connections after expiration. Choose a lifetime long enough for
  expected conversations.

The backend creates `public.chatbot_session` and its expiry index on first use.
The database role needs table/index creation permissions. Expired rows are
removed on session loads. Static assets under `/static/` bypass sessions.
No additional PostgreSQL extensions are needed.

The `presto_session` cookie is HttpOnly, SameSite=Lax, host-only, and scoped to
`/`. It contains only a cryptographically random opaque identifier. Session
objects are pickled **only on the server**, with an HMAC-SHA256 integrity check
verified before unpickling. The signature also binds the payload to its session
identifier. Database access must still be restricted to trusted application
roles: signing is not encryption, and pickle is not a format for untrusted data.
Changing `SECRET_KEY` invalidates stored payload signatures; clear existing
sessions as part of an intentional key rotation rather than silently falling
back after an integrity error.

HTTP sessions are saved before response headers. The app uses function-scoped
memory dependencies so their cleanup completes before this save. Changes from
streaming response bodies, background tasks, or request-scoped dependency
cleanup are not supported; finalize session mutations before sending headers.
Websocket sessions are saved after workflow/dependency teardown on disconnect,
including cancelled workflows. Unsaved websocket changes do not survive process
termination or crashes. HTTP responses carrying sessions use `Cache-Control:
no-store` and vary on `Cookie`.

Open an HTTP page first to obtain the cookie, then use the same cookie for WS/WSS.
Websockets without an existing, unexpired session are rejected with code 1008.
Browser websocket origins must match the handshake's scheme and host; configure
trusted proxy headers correctly when terminating TLS upstream.

Concurrent writes use optimistic version checks. Unchanged snapshots do not
overwrite newer data. Conflicting or expired writes fail rather than lose data:
HTTP returns 409 and websocket teardown raises/logs the persistence error.
Clients should reload/reconnect before retrying; there is no automatic merge.
Avoid editing the same session in multiple active conversations simultaneously.
Sign-in and sign-out are implemented by `identity.py` and `routers/account.py`
(see Accounts below); the backend still does not rotate session IDs at login.

When persistence is disabled, short-term memory lasts for one HTTP request or
websocket connection. Starlette's JSON cookie-session middleware cannot replace
this backend because it cannot store Python memory objects.

Valid chat messages, including answers to `request_response`, update
`short_term_memory["last_user_input"]`. Workflows can add other conversation state
to the dictionary; no unbounded transcript is retained automatically.

Run the memory and chatbot tests with:

```sh
# Use a dedicated test database, never production.
TEST_DATABASE_URL=postgresql:///presto_memory_test \
    bin/python -m unittest test_sessions test_memory test_wikkit test_intents test_account
```

Without `TEST_DATABASE_URL`, PostgreSQL integration tests are explicitly skipped;
request/context and session-configuration unit tests still run. Session tests
create the backend table in the dedicated database; KV tests use temporary schemas.

# Chatbot Output Filtering

`chatbot.substitute(text)` replaces tokens such as `{{user.name}}`,
`{{poster.title}}`, and `{{session.id}}` using the current connection's
`state.user`, `state.poster`, and `state.session`. For chatbot workflows, this
is `websocket.state`, the websocket equivalent of `request.state`. These values
must be supplied by server-side application code; the filter does not load
identities or infer them from memory or the session middleware.

Fields can be dictionary keys or object attributes, including nested public
fields such as `{{poster.author.name}}` and hyphenated keys such as
`{{user.user-name}}`. Substituted values are HTML-escaped because `say` is
rendered as HTML. Whitespace around the identifier is
allowed. Values are converted to strings, including zero and false. Missing
or null values and callable attributes leave the original token unchanged and
log a warning. Other roots, private attributes, and expressions are not
supported and remain unchanged. Replacement values are not expanded again.

Every `chatbot.send(reply)` passes its top-level `say` string through
`substitute`, including intent replies, random responses, response prompts,
and unsolicited messages. Other keys (including command payloads) are not
filtered, and the original reply dictionary is not modified. A non-string
`say` raises `TypeError`.

# Accounts

`identity.py` resolves each request's identity before memory loads:
`state.user_id` (from `session["user_id"]`, verified against the `"user"` table)
and `state.user = {"user-name", "uid", "username", "email"}`. The visitor's
name is kept in the `presto_user` cookie (HttpOnly, SameSite=Lax, one year), so
returning visitors go from `/wikkit/magic` to `/account/signin`.

`users.py` stores accounts in the `"user"` table from `schema/schema.sql`
(created if missing). Passwords are hashed with salted scrypt. Confirmation and
recovery links are HMACs of `SECRET_KEY` over the purpose, user, the record's
`recovery` timestamp, and the password hash; using a link clears or replaces
`recovery`, so each link works once. Unconfirmed users cannot sign in;
attempting to sends a new confirmation link. Recovery responses never reveal
whether an email is registered.

Account form endpoints accept JSON only (`static/javascript/account/router.js`)
and reply `{ok, say, redirect?, errors?}`. Successful sign-in redirects to
`state.poster["resumption-page"]`, or `/posters/list`.

```env
MAIL_BACKEND=console          # console (logs; default) or smtp
MAIL_FROM=wikkit@example.com  # required for smtp
SMTP_HOST=mail.smtp2go.com
SMTP_PORT=587
SMTP_SECURITY=starttls        # none, starttls, or ssl
SMTP_USERNAME=...
SMTP_PASSWORD=...
PUBLIC_BASE_URL=https://example.com  # base for emailed links; defaults to the request URL
CONFIRMATION_LINK_MAX_AGE=86400      # seconds
RECOVERY_LINK_MAX_AGE=3600           # seconds
SUPPORT_EMAIL=support@example.com    # shown on /wikkit/help
RENDER_PRICE=0.00                    # shown on /wikkit/pricing; 0 shows "free"
```

Account tests truncate the `"user"` table, so `TEST_DATABASE_URL` must point to
a dedicated database.
