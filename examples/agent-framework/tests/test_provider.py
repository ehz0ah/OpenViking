"""Offline contracts through the real MAF agent and session lifecycle."""

import asyncio
import json
from copy import deepcopy

import pytest
from agent_framework import (
    Agent,
    AgentSession,
    BaseChatClient,
    ChatMiddlewareLayer,
    ChatResponse,
    ChatResponseUpdate,
    Content,
    FunctionInvocationLayer,
    Message,
    ResponseStream,
)
from openviking_sdk import AsyncHTTPClient

from openviking_agent_framework import OpenVikingContextProvider


class MemoryClient(AsyncHTTPClient):
    """Model the SDK boundary, including ambiguous append acknowledgements."""

    def __init__(self):
        super().__init__(url="http://openviking.test", api_key="test-user")
        self.rows = []
        self.sessions = []
        self.searches = []
        self.commits = []
        self.batches = 0
        self.fail_batch = None
        self.lose_ack = False
        self.recall_error = None

    async def get_session(self, session_id, *, auto_create=False):
        assert auto_create
        self.sessions.append(session_id)
        return {"session_id": session_id}

    async def search_context(self, **kwargs):
        if self.recall_error:
            raise self.recall_error
        self.searches.append(kwargs)
        return {"rendered": "Preferred format: JSON", "entries": [{"uri": "viking://memory"}]}

    async def batch_add_messages(self, session_id, messages):
        self.batches += 1
        if self.batches == self.fail_batch:
            raise ConnectionError("request not sent")
        self.rows.extend(deepcopy(messages))
        if self.lose_ack:
            raise ConnectionError("lost acknowledgement")
        return {"added": len(messages)}

    async def commit_session(self, session_id):
        self.commits.append(session_id)
        return {"task_id": "extraction-job"}


class ChatClient(FunctionInvocationLayer, ChatMiddlewareLayer, BaseChatClient):
    """Deterministic model results; MAF still owns tools and streaming."""

    def __init__(self, responses=None):
        super().__init__()
        self.inputs = []
        self.responses = iter(responses) if responses is not None else None
        self.error = None

    def _inner_get_response(self, *, messages, stream, options, **kwargs):
        self.inputs.append(deepcopy(list(messages)))
        message = (
            deepcopy(next(self.responses)) if self.responses else Message("assistant", ["Answer"])
        )
        if stream:

            async def updates():
                for content in message.contents:
                    yield ChatResponseUpdate(
                        role="assistant", contents=[content], message_id=message.message_id
                    )

            return ResponseStream(updates(), finalizer=ChatResponse.from_updates)

        async def response():
            if self.error:
                raise self.error
            return ChatResponse(messages=[message])

        return response()


def setup(responses=None):
    client = MemoryClient()
    provider = OpenVikingContextProvider(client)
    model = ChatClient(responses)
    agent = Agent(model, context_providers=[provider])
    return client, provider, model, agent, agent.create_session()


async def test_recall_capture_and_explicit_commit():
    client, provider, model, agent, session = setup()
    await agent.run(
        [Message("system", ["Private instructions"]), Message("user", ["My question"])],
        session=session,
    )
    assert "Preferred format: JSON" in json.dumps([m.to_dict() for m in model.inputs[0]])
    assert [row["role"] for row in client.rows] == ["user", "assistant"]
    assert "Preferred format" not in json.dumps(client.rows)
    assert "Private instructions" not in json.dumps(client.rows)
    assert client.searches[0]["options"] == {"max_tokens": 4000, "peer_scope": "all"}
    assert not client.commits
    assert await provider.commit(session) == {"task_id": "extraction-job"}
    assert client.commits == [session.state[provider.source_id]["session_id"]]


async def test_tools_keep_calls_and_results_paired():
    def lookup_label():
        """Return the current label."""
        return "LABEL"

    client, _, _, agent, session = setup(
        [
            Message(
                "assistant",
                [Content("function_call", call_id="call-1", name="lookup_label", arguments={})],
            ),
            Message("assistant", ["LABEL"]),
        ]
    )
    await agent.run("Read the label", tools=[lookup_label], session=session)
    parts = [part for row in client.rows for part in row["parts"] if part["type"] == "tool"]
    assert len(parts) == 2
    assert [part["tool_status"] for part in parts] == ["pending", "completed"]
    assert {part["tool_id"] for part in parts} == {"call-1"}
    assert {part["tool_name"] for part in parts} == {"lookup_label"}
    assert parts[1]["tool_output"] == "LABEL"


@pytest.mark.parametrize("complete", [False, True])
async def test_stream_capture_requires_complete_consumption(complete):
    client, _, _, agent, session = setup([Message("assistant", ["First", "Second"])])
    async with agent.run("Question", session=session, stream=True) as stream:
        async for _ in stream:
            if not complete:
                break
    assert len(client.rows) == (2 if complete else 0)


async def test_restoration_preserves_session_and_acknowledged_message_ids():
    client, provider, _, agent, session = setup()
    question = Message("user", ["Same text"], message_id="original")
    response = await agent.run(question, session=session)
    restored = AgentSession.from_dict(json.loads(json.dumps(session.to_dict())))
    await agent.run(
        [question, *response.messages, Message("user", ["Same text"], message_id="new")],
        session=restored,
    )
    assert len(client.rows) == 4
    ids = [row["source_message_ids"][0] for row in client.rows]
    assert len(ids) == len(set(ids))
    assert {"original", "new"}.issubset(ids)
    assert len(set(client.sessions)) == 1
    assert (
        restored.state[provider.source_id]["session_id"]
        == session.state[provider.source_id]["session_id"]
    )


async def test_partial_write_keeps_only_unacknowledged_messages_for_flush():
    client, provider, _, agent, session = setup()
    client.fail_batch = 2
    messages = [Message("user", [f"Item {i}"], message_id=str(i)) for i in range(101)]
    with pytest.raises(Exception, match="request not sent"):
        await agent.run(messages, session=session)
    assert len(client.rows) == 100
    assert len(session.state[provider.source_id]["pending_messages"]) == 2
    client.fail_batch = None
    await provider.flush(session)
    assert len(client.rows) == 102
    assert not session.state[provider.source_id]["pending_messages"]


async def test_lost_ack_blocks_implicit_retry_and_commit():
    client, provider, model, agent, session = setup()
    client.lose_ack = True
    with pytest.raises(Exception, match="lost acknowledgement"):
        await agent.run("Question", session=session)
    assert len(client.rows) == 2
    assert len(session.state[provider.source_id]["pending_messages"]) == 2
    with pytest.raises(RuntimeError, match="unconfirmed"):
        await agent.run("Another question", session=session)
    with pytest.raises(RuntimeError, match="pending"):
        await provider.commit(session)
    assert len(model.inputs) == 1
    assert len(client.rows) == 2


async def test_recall_failure_stops_inference_and_remains_retryable():
    client, _, model, agent, session = setup()
    client.recall_error = ConnectionError("recall unavailable")
    with pytest.raises(Exception, match="recall unavailable"):
        await agent.run("Question", session=session)
    assert not model.inputs and not client.rows
    client.recall_error = None
    await agent.run("Question", session=session)
    assert len(client.rows) == 2


@pytest.mark.parametrize("error", [RuntimeError("model failed"), asyncio.CancelledError()])
async def test_failed_or_cancelled_inference_is_not_captured(error):
    client, _, model, agent, session = setup()
    model.error = error
    with pytest.raises(type(error)):
        await agent.run("Question", session=session)
    assert not client.rows
    assert not session.state["openviking"].get("pending_messages")
    model.error = None
    await agent.run("Retry", session=session)
    assert len(client.rows) == 2


async def test_hidden_reasoning_media_and_other_provider_context_are_excluded():
    client, _, _, agent, session = setup(
        [
            Message(
                "assistant",
                [Content("text", text="Visible"), Content("text_reasoning", text="Hidden")],
            )
        ]
    )
    await agent.run(
        [
            Message("user", ["Question", Content("image", uri="https://example.test/private.png")]),
            Message(
                "user",
                ["Other memory"],
                additional_properties={"_attribution": {"source_id": "other"}},
            ),
        ],
        session=session,
    )
    captured = json.dumps(client.rows)
    assert "Question" in captured and "Visible" in captured
    assert all(value not in captured for value in ("Hidden", "private.png", "Other memory"))
    assert client.searches[0]["query"] == "Question"


async def test_shared_provider_keeps_session_state_separate():
    client, provider, _, agent, first = setup()
    second = agent.create_session()
    await asyncio.gather(agent.run("First", session=first), agent.run("Second", session=second))
    assert (
        first.state[provider.source_id]["session_id"]
        != second.state[provider.source_id]["session_id"]
    )
    assert len(client.rows) == 4
    assert not first.state[provider.source_id]["pending_messages"]
    assert not second.state[provider.source_id]["pending_messages"]
