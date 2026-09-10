# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Accepted-turn receipts, serialized by the existing session append/commit lock."""

import hashlib
import json
import re
from dataclasses import asdict
from typing import TYPE_CHECKING

from openviking.message import Message
from openviking_cli.exceptions import FailedPreconditionError, InvalidArgumentError

if TYPE_CHECKING:
    from openviking.session.session import Session


async def append_turn(session: "Session", messages_spec: list[dict], advancement_key: str) -> str:
    from openviking.session.session import (
        _SESSION_PHASE1_LOCK_TIMEOUT_SECONDS,
        _is_storage_not_found,
    )

    if not isinstance(advancement_key, str) or not advancement_key or len(advancement_key) > 512:
        raise InvalidArgumentError("advancement_key must contain 1 to 512 characters")
    fs = session._viking_fs
    if not messages_spec or len(messages_spec) > 20_000:
        raise InvalidArgumentError("Accepted turns require 1 to 20000 messages")
    if fs is None:
        raise FailedPreconditionError("Accepted turns require durable session storage")
    canonical = [
        {**spec, "parts": [asdict(part) for part in spec["parts"]]} for spec in messages_spec
    ]
    fingerprint = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    key_hash = hashlib.sha256(advancement_key.encode()).hexdigest()
    receipt_uri = f"{session.uri}/.turn-{key_hash}.json"
    path = fs._uri_to_path(session.uri, ctx=session.ctx)
    lease = await fs._async_agfs.pathlock_acquire_exact(
        path, timeout_secs=_SESSION_PHASE1_LOCK_TIMEOUT_SECONDS
    )
    try:
        recovered_total = None
        try:
            receipt = json.loads(await fs.read_file(receipt_uri, ctx=session.ctx))
        except Exception as exc:
            if not _is_storage_not_found(exc):
                raise
            receipt = None
        if receipt is not None:
            if receipt.get("fingerprint") != fingerprint:
                raise InvalidArgumentError(
                    "advancement_key was already used for different messages"
                )
            if receipt.get("state") == "committed":
                return "duplicate"
            if receipt.get("state") != "pending":
                raise FailedPreconditionError("Invalid accepted-turn receipt")
            messages = [Message.from_dict(item) for item in receipt["messages"]]
            # A lost append/receipt response must not append again. Commits can
            # move those durable message IDs into archives before this retry.
            live = await session._read_live_messages_strict()
            seen = {message.id for message in live}
            try:
                history = await fs.ls(f"{session.uri}/history", ctx=session.ctx)
            except Exception as exc:
                if not _is_storage_not_found(exc):
                    raise
                history = []
            for item in history:
                name = item.get("name") if isinstance(item, dict) else item
                if not isinstance(name, str) or not re.fullmatch(r"archive_\d+", name):
                    continue
                seen.update(
                    message.id
                    for message in await session._read_archive_messages(
                        f"{session.uri}/history/{name}"
                    )
                )
            messages = [message for message in messages if message.id not in seen]
            recovered_total = len(seen) + len(messages)
        else:
            messages = session._build_messages(messages_spec)
            receipt = {
                "state": "pending",
                "fingerprint": fingerprint,
                "messages": [message.to_dict() for message in messages],
            }
            # Save generated IDs before appending: recovery reuses the same
            # messages even when the worker died after storage accepted a write.
            await fs.write_file(receipt_uri, json.dumps(receipt), ctx=session.ctx)
        await session._append_messages_locked(messages)
        # Recompute pending tokens if append succeeded but its metadata write
        # was interrupted. Normal appends retain their existing fast path.
        if recovered_total is not None:
            session._meta.total_message_count = recovered_total
        await session._rebuild_pending_tokens()
        await session._save_meta()
        await fs.write_file(
            receipt_uri,
            json.dumps({"state": "committed", "fingerprint": fingerprint}),
            ctx=session.ctx,
        )
        return "committed"
    finally:
        await fs._async_agfs.pathlock_release(lease)
