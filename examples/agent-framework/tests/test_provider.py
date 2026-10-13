"""Offline contracts through the real MAF agent and session lifecycle."""

import asyncio
import json
from copy import deepcopy

import pytest
from agent_framework import (
    Agent,
    AgentLoopMiddleware,
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


async def loop_capture(stream, *, fresh_context=False, return_final_only=False, tools=False):
    def lookup_label():
        """Return the current label."""
        return "TOOL_VALUE"

    responses = [
        Message("assistant", ["First draft"], message_id="draft"),
        Message("assistant", ["Final answer"], message_id="final"),
    ]
    if tools:
        responses.insert(
            0,
            Message(
                "assistant",
                [Content("function_call", call_id="loop-call", name="lookup_label", arguments={})],
                message_id="tool-call",
            ),
        )
    client, provider, model, agent, session = setup(responses)
    middleware = [
        AgentLoopMiddleware(
            lambda *, iteration, **kwargs: iteration < 2,
            next_message=lambda **kwargs: "Please refine the draft",
            fresh_context=fresh_context,
            return_final_only=return_final_only,
        )
    ]
    # Per-call middleware exercises the public runtime override as well.
    kwargs = {"middleware": middleware, "tools": [lookup_label] if tools else []}
    if stream:
        async with agent.run("Question", session=session, stream=True, **kwargs) as response:
            async for _ in response:
                pass
    else:
        await agent.run("Question", session=session, **kwargs)
    return client, provider, model, session


@pytest.mark.parametrize("tools", [False, True])
@pytest.mark.parametrize("return_final_only", [False, True])
async def test_streamed_agent_loop_preserves_the_same_transcript(tools, return_final_only):
    ordinary, _, _, _ = await loop_capture(False, tools=tools, return_final_only=return_final_only)
    streamed, _, _, _ = await loop_capture(True, tools=tools, return_final_only=return_final_only)

    def transcript(client):
        return [{"role": row["role"], "parts": row["parts"]} for row in client.rows]

    assert transcript(streamed) == transcript(ordinary)
    assert "First draft" in json.dumps(streamed.rows)
    assert "Please refine the draft" in json.dumps(streamed.rows)
    assert "Final answer" in json.dumps(streamed.rows)
    assert "Preferred format" not in json.dumps(streamed.rows)
    assert len(set(streamed.sessions)) == 1
    assert len({row["source_message_ids"][0] for row in streamed.rows}) == len(streamed.rows)
    if tools:
        parts = [p for row in streamed.rows for p in row["parts"] if p["type"] == "tool"]
        assert [p["tool_status"] for p in parts] == ["pending", "completed"]
        assert {p["tool_id"] for p in parts} == {"loop-call"}
        assert parts[1]["tool_output"] == "TOOL_VALUE"


@pytest.mark.parametrize("stream", [False, True])
async def test_loop_cannot_silently_reset_capture_progress(stream):
    with pytest.raises(RuntimeError, match="state.*reset.*fresh_context=True"):
        await loop_capture(stream, fresh_context=True)


async def test_interrupted_streamed_loop_keeps_only_completed_iterations():
    client, _, _, agent, session = setup(
        [
            Message("assistant", ["First draft"], message_id="draft"),
            Message("assistant", ["Final answer"], message_id="final"),
        ]
    )
    loop = AgentLoopMiddleware(lambda *, iteration, **kwargs: iteration < 2)
    async with agent.run("Question", session=session, middleware=[loop], stream=True) as response:
        async for update in response:
            if "Final answer" in update.text:
                break
    assert [p["text"] for row in client.rows for p in row["parts"]] == ["Question", "First draft"]
    assert not session.state["openviking"]["pending_messages"]


async def test_state_rollback_blocks_flush_and_commit_but_equal_state_restoration_is_safe():
    client, provider, _, agent, session = setup()
    await agent.run("Question", session=session)
    saved = AgentSession.from_dict(json.loads(json.dumps(session.to_dict()))).state
    session.state = {}
    with pytest.raises(RuntimeError, match="state was reset"):
        await provider.flush(session)
    with pytest.raises(RuntimeError, match="state was reset"):
        await provider.commit(session)
    assert not client.commits
    session.state = saved
    await agent.run("Next", session=session)
    assert len(client.rows) == 4
