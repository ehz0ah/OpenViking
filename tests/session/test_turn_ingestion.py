# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
import asyncio
import json

import pytest

from openviking.message import TextPart
from openviking_cli.exceptions import InvalidArgumentError


def messages(text="My preferred color is cobalt."):
    return [
        {"role": "user", "parts": [TextPart(text)]},
        {"role": "assistant", "parts": [TextPart("Acknowledged.")]},
    ]


async def test_accepted_turn_survives_restart_and_concurrent_delivery(client):
    a = client(session_id="turn-retry")
    await a.ensure_exists()
    b = client(session_id=a.session_id)
    await b.load()
    results = await asyncio.gather(
        a.add_turn_async(messages(), "k"), b.add_turn_async(messages(), "k")
    )
    assert sorted(results) == ["committed", "duplicate"]
    fresh = client(session_id=a.session_id)
    await fresh.load()
    assert len(fresh.messages) == 2
    assert await fresh.add_turn_async(messages(), "k") == "duplicate"
    with pytest.raises(InvalidArgumentError, match="different messages"):
        await fresh.add_turn_async(messages("different"), "k")
    assert len(await fresh._read_live_messages_strict()) == 2
    assert await fresh.add_turn_async(messages(), "next-key") == "committed"
    assert len(await fresh._read_live_messages_strict()) == 4


@pytest.mark.parametrize("failure_point", ["before_append", "after_append", "receipt"])
async def test_pending_turn_recovers_interrupted_delivery(client, monkeypatch, failure_point):
    a = client(session_id=f"pending-turn-{failure_point}")
    await a.ensure_exists()
    fs = a._viking_fs
    if failure_point == "receipt":
        original = fs.write_file

        async def fail_receipt(uri=None, content=None, **kwargs):
            if "/.turn-" in uri and json.loads(content)["state"] == "committed":
                raise OSError("lost receipt write")
            return await original(uri, content, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(fs, "write_file", fail_receipt)
            with pytest.raises(OSError, match="lost receipt"):
                await a.add_turn_async(messages(), "k")
    elif failure_point == "after_append":
        original_append = fs.append_file

        async def lost_append_response(*args, **kwargs):
            await original_append(*args, **kwargs)
            raise OSError("lost append response")

        with monkeypatch.context() as patch:
            patch.setattr(fs, "append_file", lost_append_response)
            with pytest.raises(OSError, match="lost append response"):
                await a.add_turn_async(messages(), "k")
    else:
        with monkeypatch.context() as patch:

            async def fail_append(*args, **kwargs):
                raise OSError("append unavailable")

            patch.setattr(a, "_append_messages_locked", fail_append)
            with pytest.raises(OSError, match="append unavailable"):
                await a.add_turn_async(messages(), "k")
    fresh = client(session_id=a.session_id)
    await fresh.load()
    assert await fresh.add_turn_async(messages(), "k") == "committed"
    assert len(await fresh._read_live_messages_strict()) == 2
    assert fresh._meta.total_message_count == 2
    assert fresh._meta.pending_tokens > 0
    assert await fresh.add_turn_async(messages(), "k") == "duplicate"


async def test_receipt_survives_compaction(client):
    a = client(session_id="turn-archive")
    await a.ensure_exists()
    await a.add_turn_async(messages(), "k")
    await a.commit_async(
        memory_policy={
            "working_memory": {"enabled": False},
            "self": {"enabled": False},
            "peer": {"enabled": False},
        }
    )
    fresh = client(session_id=a.session_id)
    await fresh.load()
    assert await fresh.add_turn_async(messages(), "k") == "duplicate"
    assert await fresh._read_live_messages_strict() == []
    assert len(await fresh._list_archive_refs()) == 1


async def test_pending_receipt_recovers_after_compaction_and_other_appends(client, monkeypatch):
    a = client(session_id="pending-turn-archive")
    await a.ensure_exists()
    fs = a._viking_fs
    original = fs.write_file

    async def fail_receipt(uri=None, content=None, **kwargs):
        if "/.turn-" in uri and json.loads(content)["state"] == "committed":
            raise OSError("lost receipt write")
        return await original(uri, content, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(fs, "write_file", fail_receipt)
        with pytest.raises(OSError, match="lost receipt"):
            await a.add_turn_async(messages(), "k")
    await a.commit_async(
        memory_policy={
            "working_memory": {"enabled": False},
            "self": {"enabled": False},
            "peer": {"enabled": False},
        }
    )
    await a.add_turn_async(messages("another turn"), "other")
    fresh = client(session_id=a.session_id)
    await fresh.load()
    assert await fresh.add_turn_async(messages(), "k") == "committed"
    live = await fresh._read_live_messages_strict()
    assert len(live) == 2
    assert live[0].parts[0].text == "another turn"
    assert fresh._meta.total_message_count == 4
    assert await fresh.add_turn_async(messages(), "k") == "duplicate"


async def test_pending_receipt_does_not_ignore_unreadable_history(client, monkeypatch):
    a = client(session_id="pending-turn-history-error")
    await a.ensure_exists()
    with monkeypatch.context() as patch:

        async def fail_append(*args, **kwargs):
            raise OSError("append unavailable")

        patch.setattr(a, "_append_messages_locked", fail_append)
        with pytest.raises(OSError, match="append unavailable"):
            await a.add_turn_async(messages(), "k")
    with monkeypatch.context() as patch:

        async def fail_ls(*args, **kwargs):
            raise OSError("history unavailable")

        patch.setattr(a._viking_fs, "ls", fail_ls)
        with pytest.raises(OSError, match="history unavailable"):
            await a.add_turn_async(messages(), "k")
    assert await a._read_live_messages_strict() == []
    assert await a.add_turn_async(messages(), "k") == "committed"
