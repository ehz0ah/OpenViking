# OpenViking for Microsoft Agent Framework

An optional Python package for MAF agents. It recalls OpenViking context before a
run and captures completed user/assistant turns, including tool calls and results.
The application explicitly commits a conversation for memory extraction.

## Install and try

Requires Python 3.10+, MAF core 1.20–1.x, OpenViking SDK 0.1.13–0.1.x, and a
running OpenViking 0.4.23+ server. Install from an OpenViking checkout:

```bash
pip install './examples/agent-framework[example]'
```

Set `OPENVIKING_URL`, `OPENVIKING_API_KEY` (a normal user key), `OPENAI_API_KEY`,
`OPENAI_BASE_URL`, and `OPENAI_MODEL`, then run:

```bash
python examples/agent-framework/example.py
```

The example records a preference, waits for extraction, and asks about it in a new
conversation. Its OpenAI-compatible model client is optional; the provider works
with other MAF chat clients that support chat middleware (including the built-in
clients). The package installs neither the OpenViking server
nor a model client unless the `example` extra is selected.

## Attach to an existing agent

With an initialized, application-owned `AsyncHTTPClient`:

```python
from agent_framework import Agent
from openviking_agent_framework import OpenVikingContextProvider

memory = OpenVikingContextProvider(client)
agent = Agent(chat_client, context_providers=[memory])
session = agent.create_session()
response = await agent.run("Remember my preferred reply format: JSON.", session=session)
job = await memory.commit(session)
```

`commit()` returns task acceptance. Poll `client.get_task(job["task_id"])` until
completion before checking extracted memory. Close the client in the application.

## State and failure handling

- Use one client/provider per user and actor; serialize work for the same session.
- Persist `session.to_dict()` and restore with `AgentSession.from_dict()` under the
  same authenticated user. Preserve message IDs when replaying previous messages.
- Recalled context is attributed reference data and is excluded from capture.
  System instructions, hidden reasoning, and nontext media are also excluded.
- Fully consume streaming responses. Interrupted streams are not captured.
- Errors propagate. Unconfirmed capture blocks another run and `commit()`.
  `flush(session)` retries pending messages after a known failure before sending.
  A lost response can mean the server already appended the messages: inspect
  active records and archives by `source_message_ids` before retrying or updating
  the saved provider state. Blind retries can duplicate capture.

The application owns MAF session persistence; there is no exactly-once guarantee.
Options are `source_id="openviking"`, `limit=5`, `token_budget=4000` (64–32000),
and `peer_scope="all"`. Use `peer_scope="actor"` with the SDK client's actor ID.
The server owns access control, retrieval, storage, and extraction; MAF owns agent
execution and chat history. No framework modifications or MCP tools are required.

## Validate

```bash
pip install -e './examples/agent-framework[test,dev]'
cd examples/agent-framework
pytest -q --confcutdir=tests tests
ruff check .
ruff format --check .
mypy src
python -m build
```

Offline tests use the real MAF agent lifecycle with deterministic model and SDK
boundaries. They require no server, credentials, or model calls. Support belongs
in [OpenViking issues](https://github.com/volcengine/OpenViking/issues/new/choose).
