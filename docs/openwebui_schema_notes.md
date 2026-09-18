# Open WebUI schema notes (reference for later OWUI-specific mock work)

Verified against `backend/open_webui/models/*.py` in the `open-webui/open-webui` source
(fetched 2026-09-18). This repo does **not** mock Open WebUI's own database — `chat_sim.py`
mocks the OpenAI-compatible tool-calling wire shape that OWUI and most other chat UIs share.
These notes exist so a *dedicated* Open WebUI mock (deferred — see conversation), if built later,
starts from verified facts instead of forum-search guesses.

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

**Correction to an earlier assumption in this repo's discussion**: `shared_chat_id` is not a
separate id space living on a different entity — it is `chat.share_id`, a nullable unique column
on the *same* `chat` row. A separate `SharedChats` model manages the public snapshot data (via
`insert_shared_chat_by_chat_id()`), but the id a share link exposes is generated from/for the
existing `chat.id`, not an unrelated identifier.

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

## Sources

- `backend/open_webui/models/chats.py`
- `backend/open_webui/models/files.py`
- `backend/open_webui/models/functions.py`
- `backend/open_webui/models/tools.py`

(all in `github.com/open-webui/open-webui`, `main` branch, fetched 2026-09-18 — re-verify against
the current source before relying on this for a real implementation, as OWUI's schema evolves.)
