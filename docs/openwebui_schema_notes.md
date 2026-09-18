# Open WebUI schema notes (reference for later OWUI-specific mock work)

Verified against `backend/open_webui/models/*.py` in the `open-webui/open-webui` source
(fetched 2026-09-18). This repo does **not** mock Open WebUI's own database — `chat_sim.py`
mocks the OpenAI-compatible tool-calling wire shape that OWUI and most other chat UIs share.
These notes exist so a *dedicated* Open WebUI mock (deferred — see conversation), if built later,
starts from verified facts instead of forum-search guesses.

## Coverage of `backend/open_webui/models/` (26 files, fetched 2026-09-18)

Tables were prioritized by relevance to tool-calling/MCP correlation work, not by file-list order.
Everything below marked "not started" is genuinely unexamined — do not assume parity with real
OWUI behavior for it.

| # | File | Status |
| - | --- | --- |
| 1 | `access_grants.py` | not started |
| 2 | `auths.py` | not started |
| 3 | `automations.py` | not started |
| 4 | `calendar.py` | not started |
| 5 | `channels.py` | not started |
| 6 | `chat_messages.py` | not started |
| 7 | `chats.py` | **done** — see `chat` table below |
| 8 | `config.py` | not started (distinct from the top-level `backend/open_webui/config.py` env/settings loader — this one is a `models/` table) |
| 9 | `feedbacks.py` | not started |
| 10 | `files.py` | **done** — see `file` table below |
| 11 | `folders.py` | not started |
| 12 | `functions.py` | **done** — see `function` table below |
| 13 | `groups.py` | not started |
| 14 | `knowledge.py` | not started |
| 15 | `memories.py` | not started |
| 16 | `messages.py` | not started |
| 17 | `models.py` | not started |
| 18 | `notes.py` | not started |
| 19 | `oauth_sessions.py` | not started |
| 20 | `prompt_history.py` | not started |
| 21 | `prompts.py` | not started |
| 22 | `shared_chats.py` | **done** — see `shared_chat` table below (corrects the earlier "mentioned only" entry) |
| 23 | `skills.py` | not started |
| 24 | `tags.py` | not started |
| 25 | `tools.py` | **done** — see `tool` table below |
| 26 | `users.py` | **done** — see `user` / `api_key` tables below |

Also done, chosen for MCP/tool-calling relevance rather than by file-list order:

| File | Status |
| --- | --- |
| `auths.py` | **done** — see `auth` table below |
| `models.py` | **done** — see `model` table below |
| `chat_messages.py` | **done** — see `chat_message` table below |

9 of 26 fully verified. Nothing outside `backend/open_webui/models/` (e.g. the top-level
`config.py`, `main.py`, `routers/`, `utils/`) has been examined at all, and the remaining 17
`models/` files (`access_grants.py`, `automations.py`, `calendar.py`, `channels.py`,
`config.py` (models/ variant), `feedbacks.py`, `folders.py`, `groups.py`, `knowledge.py`,
`memories.py`, `messages.py`, `notes.py`, `oauth_sessions.py`, `prompt_history.py`,
`prompts.py`, `skills.py`, `tags.py`) remain not started.

## `chat` table

```python
class Chat(Base):
    __tablename__ = "chat"

    id = Column(String, primary_key=True, unique=True)
    user_id = Column(String, index=True)
    title = Column(Text)
    chat = Column(JSON)                       # the actual message tree/history
    created_at = Column(BigInteger, index=True)
    updated_at = Column(BigInteger, index=True)
    share_id = Column(Text, unique=True, nullable=True)
    archived = Column(Boolean, default=False)
    pinned = Column(Boolean, default=False, nullable=True)
    meta = Column(JSON, server_default="{}")
    variables = Column(JSON, nullable=True)
    folder_id = Column(Text, nullable=True)
    tasks = Column(JSON, nullable=True)
    summary = Column(Text, nullable=True)
    current_message_id = Column(Text, nullable=True)
    last_read_at = Column(BigInteger, nullable=True)
    timer_at = Column(BigInteger, nullable=True)
```

**`chat.share_id` is not the whole story — see the `shared_chat` table below for the correction.**
An earlier pass through this document claimed the share link's id was "generated from/for the
existing chat.id, not an unrelated identifier." That was wrong: it is a fresh, independent
`uuid.uuid4()`, unrelated to `chat.id` in value. Left here as a visible correction rather than
silently editing it away.

## `chat_file` (join table)

```python
class ChatFile(Base):
    __tablename__ = "chat_file"

    id = Column(Text, unique=True, primary_key=True)
    user_id = Column(Text, nullable=False)
    chat_id = Column(Text, ForeignKey("chat.id", ondelete="CASCADE"), nullable=False)
    message_id = Column(Text, nullable=True)
    file_id = Column(Text, ForeignKey("file.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)
```

Confirms `chat_id` and `file_id` are related only through this join table, each a foreign key to
its own table's `id` — not nested inside one another.

## `file` table

```python
class File(Base):
    __tablename__ = "file"

    id = Column(String, primary_key=True)     # generation not in this file; likely uuid4 at insert time
    user_id = Column(String, index=True)
    hash = Column(Text, nullable=True)
    filename = Column(Text)
    path = Column(Text, nullable=True)
    data = Column(JSON, nullable=True)
    meta = Column(JSON, nullable=True)
    created_at = Column(BigInteger, index=True)
    updated_at = Column(BigInteger)
```

## `function` / `tool` tables

```python
class Function(Base):
    __tablename__ = "function"

    id = Column(String, primary_key=True)     # user-supplied slug, not a generated id — see below
    user_id = Column(String, index=True, nullable=True)
    name = Column(Text)
    type = Column(Text)                       # "pipe" | "filter" | "action" | ...
    content = Column(Text, nullable=True)     # the Python source
    meta = Column(JSONField, nullable=True)
    valves = Column(JSONField, nullable=True)
    is_active = Column(Boolean, default=False)
    is_global = Column(Boolean)
    updated_at = Column(BigInteger)
    created_at = Column(BigInteger)

class Tool(Base):
    __tablename__ = "tool"

    id = Column(String, primary_key=True)     # same story: user-chosen slug
    user_id = Column(String, index=True, nullable=True)
    name = Column(Text, nullable=True)
    content = Column(Text, nullable=True)
    specs = Column(JSONField, nullable=True)
    meta = Column(JSONField, nullable=True)
    valves = Column(JSONField, nullable=True)
    updated_at = Column(BigInteger)
    created_at = Column(BigInteger, index=True)
```

**The important distinction for tool-calling correctness work**: `function.id`/`tool.id` are
**not** opaque generated identifiers like `chat.id`, `file.id`, or an OpenAI `tool_calls[].id`
(`call_xxx`). They are user-chosen slugs assigned when someone installs/creates a custom
Function or Tool in the OWUI UI — closer to a Python module name than a request-scoped id. That
makes them a fundamentally different kind of "id" than what `chat_sim.py`'s `call_id`/`chat_id`
represent, and is why this repo's own MCP-served tools (`find_municipalities`, etc.) are matched
by `name`, not by a `function_id`/`tool_id` — the MCP protocol has no such concept, and it isn't
OWUI's `function_id` either (that only applies to OWUI's own Python plugin system, which is
orthogonal to MCP servers like this one).

## `shared_chat` table

```python
class SharedChat(Base):
    __tablename__ = "shared_chat"

    id = Column(Text, primary_key=True)       # str(uuid.uuid4()) — see SharedChatsTable.create()
    chat_id = Column(Text, ForeignKey("chat.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Text, nullable=False)
    title = Column(Text, nullable=True)
    chat = Column(JSON, nullable=True)        # a snapshot copy of the chat content, not a live view
    created_at = Column(BigInteger, nullable=True)
    updated_at = Column(BigInteger, nullable=True)
```

`SharedChatsTable.create()` generates the share id as `share_id = str(uuid.uuid4())` — a fresh,
independent token, **not** derived from `chat.id`. That value becomes `SharedChat.id` (the public
`/s/{id}` token) and, per `chats.py`, is also written into `chat.share_id` on the source row so a
chat can be looked up as "currently shared" without a join. The two columns are expected to hold
the same value while a chat is shared; `shared_chat` is a point-in-time content snapshot (its own
`chat`/`title` copy), while `chat.share_id` is just the live pointer to it.

## `user` / `api_key` tables

```python
class User(Base):
    __tablename__ = "user"

    id = Column(String, primary_key=True)     # generation not in this file; not auto-increment
    email = Column(String, unique=True)
    username = Column(String(50), nullable=True)
    role = Column(String, default="pending")
    name = Column(String)
    # ... profile_image_url, bio, timezone, presence_state, status_*, oauth (JSON:
    # {provider: {sub: value}}), scim, settings, variables, info — all optional/JSON
    last_active_at = Column(BigInteger)
    updated_at = Column(BigInteger)
    created_at = Column(BigInteger)

class ApiKey(Base):
    __tablename__ = "api_key"

    id = Column(Text, primary_key=True)       # formatted "key_{user_id}" — derived, not random
    user_id = Column(Text, nullable=False)    # no explicit FK constraint in the model
    key = Column(Text, unique=True)           # the actual bearer token; `id` is not the secret
    expires_at = Column(BigInteger, nullable=True)
    last_used_at = Column(BigInteger, nullable=True)
    created_at = Column(BigInteger)
    updated_at = Column(BigInteger)
```

`api_key.id` is yet another distinct "kind" of id: not random like `chat.id`/`shared_chat.id`, not
a user-chosen slug like `function.id`/`tool.id` — it's mechanically derived from another row's id
(`f"key_{user_id}"`). The actual secret lives in the separate `key` column.

## `auth` table

```python
class Auth(Base):
    __tablename__ = "auth"

    id = Column(String, primary_key=True)     # mirrors User.id exactly — same value, not a new one
    email = Column(String, nullable=True)     # kept in sync with User.email
    password = Column(Text, nullable=True)    # argon2/bcrypt hash
    active = Column(Boolean, nullable=True)
```

No foreign key ties `auth.id` to `user.id` — they are the *same string*, written to both tables at
account creation. A one-to-one relationship expressed through identical primary keys rather than a
FK, distinct from every other cross-table pattern in this document (`chat_file`/`shared_chat` use
real foreign keys; `api_key.id` is derived-but-different from `user_id`).

## `model` table

```python
class Model(Base):
    __tablename__ = "model"

    id = Column(Text, primary_key=True)       # THE literal API model identifier, e.g. "gpt-4"
    user_id = Column(Text, nullable=True)
    base_model_id = Column(Text, nullable=True)  # None for a base model; set when wrapping/proxying one
    name = Column(Text)
    params = Column(JSONField)                # inference parameters
    meta = Column(JSONField)                  # description, tags, capabilities (incl. tool-calling flags), knowledge
    is_active = Column(Boolean, default=True)
    updated_at = Column(BigInteger)
    created_at = Column(BigInteger)
```

`model.id` belongs with `function.id`/`tool.id` in the "human-chosen name, not an opaque token"
category — it's literally the string used to address that model in chat-completion requests, and
a workspace entry can override a built-in model by reusing its id. Tool-calling capability is a
loose `meta.capabilities` JSON key, not a dedicated column — OWUI doesn't schema-enforce it.

## `chat_message` table

```python
class ChatMessage(Base):
    __tablename__ = "chat_message"

    id = Column(Text, primary_key=True)       # reportedly f"{chat_id}-{message_id}"-shaped; not independently verified from source
    chat_id = Column(Text, ForeignKey("chat.id", ondelete="CASCADE"), index=True)
    user_id = Column(Text, index=True, nullable=True)
    role = Column(Text)                       # "user" | "assistant" | "system"
    parent_id = Column(Text, nullable=True)   # threading
    content = Column(JSON, nullable=True)
    output = Column(JSON, nullable=True)
    model_id = Column(Text, index=True, nullable=True)   # -> model.id
    files = Column(JSON, nullable=True)
    sources = Column(JSON, nullable=True)
    embeds = Column(JSON, nullable=True)
    meta = Column(JSON, nullable=True)
    done = Column(Boolean, default=True)
    status_history = Column(JSON, nullable=True)
    error = Column(JSON, nullable=True)
    usage = Column(JSON, nullable=True)       # input_tokens/output_tokens (or legacy prompt_/completion_tokens)
    created_at = Column(BigInteger, index=True)
    updated_at = Column(BigInteger)
```

**No dedicated `tool_call_id`/`tool_calls` column exists here.** Whatever OWUI records about a
tool call it decided to make lives inside the `output`/`meta`/`content` JSON blobs on this row, not
as first-class schema — this repo's own `chat_sim.py` `assistant_tool_call_message`/
`tool_result_message` dicts are *not* what gets persisted verbatim; they model the OpenAI wire
shape sent to/from the model, which is a different layer from how OWUI stores the conversation
afterward. A real OWUI-specific mock would need to look at the actual message-processing code
(`backend/open_webui/utils/middleware.py`, not the model files) to know the exact `output`/`meta`
shape, which is out of scope here.

## ID taxonomy across these tables

Six genuinely different "kinds" of id showed up across nine verified tables — worth keeping
straight before mocking any of them:

| Kind | Examples | Generation |
| --- | --- | --- |
| Opaque random token | `chat.id`, `file.id`, `shared_chat.id`/`chat.share_id`, `chat_file.id`, OpenAI `tool_calls[].id` (`call_xxx`) | `uuid.uuid4()` or equivalent, unrelated to any other row |
| Human-chosen slug | `function.id`, `tool.id`, `model.id` | Typed in by whoever creates the Function/Tool/workspace-model entry; doubles as a display/reference name |
| Mirrored primary key | `auth.id` | Copied verbatim from `user.id` at account creation; not a FK, just the same string in two tables |
| Derived-from-another-row | `api_key.id` | Mechanically built as `f"key_{user_id}"` |
| Composite (unverified) | `chat_message.id` | Reportedly `f"{chat_id}-{message_id}"`; not confirmed against actual source, flagged in its table above |
| FK-only relation, no shared id | `chat_file` linking `chat_id`↔`file_id` | Each side keeps its own table's real id; the join row has its own separate `id` too |

## Sources

- `backend/open_webui/models/chats.py`
- `backend/open_webui/models/files.py`
- `backend/open_webui/models/functions.py`
- `backend/open_webui/models/tools.py`
- `backend/open_webui/models/shared_chats.py`
- `backend/open_webui/models/users.py`
- `backend/open_webui/models/auths.py`
- `backend/open_webui/models/models.py`
- `backend/open_webui/models/chat_messages.py`

(all in `github.com/open-webui/open-webui`, `main` branch, fetched 2026-09-18 — re-verify against
the current source before relying on this for a real implementation, as OWUI's schema evolves.)
