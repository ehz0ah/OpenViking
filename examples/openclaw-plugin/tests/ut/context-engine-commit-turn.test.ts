import { describe, expect, it, vi } from "vitest";
import type { OpenVikingClient } from "../../client.js";
import { memoryOpenVikingConfigSchema } from "../../config.js";
import { createMemoryOpenVikingContextEngine } from "../../context-engine.js";
import { openClawSessionToOvStorageId } from "../../routing/identity-routing.js";

function makeEngine(client: Record<string, unknown>, overrides: Record<string, unknown> = {}, hostVersion: string | undefined = "2026.9.3") {
  return createMemoryOpenVikingContextEngine({
    id: "openviking", name: "OpenViking", version: "test", hostVersion,
    cfg: memoryOpenVikingConfigSchema.parse({ mode: "remote", baseUrl: "http://127.0.0.1:1933", ...overrides }),
    logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() },
    getClient: vi.fn().mockResolvedValue(client as unknown as OpenVikingClient),
    resolveAgentId: vi.fn(() => "agent"),
  });
}

const turn = {
  advancementKey: "accepted-1", sessionId: "s", sessionKey: "agent:main:explicit:s",
  messages: [
    { role: "user", content: "My preferred color is cobalt." },
    { role: "assistant", content: [{ type: "text", text: "Acknowledged." }] },
  ],
};

function client() {
  return {
    addSessionTurn: vi.fn().mockResolvedValue("committed"),
    addSessionMessage: vi.fn(),
    getSession: vi.fn().mockResolvedValue({ pending_tokens: 0 }),
    commitSession: vi.fn().mockResolvedValue({ status: "accepted" }),
  };
}

describe("accepted-turn capture", () => {
  it("captures the whole accepted turn without an afterTurn callback", async () => {
    const c = client();
    const engine = makeEngine(c);
    expect(engine.info.transcriptSemantics?.turnAdvancementIdempotency).toBe("atomic-idempotent-v1");
    await expect(engine.commitTurn(turn)).resolves.toEqual({ status: "committed" });
    expect(c.addSessionTurn).toHaveBeenCalledWith(
      openClawSessionToOvStorageId(turn.sessionId, turn.sessionKey), turn.advancementKey,
      [expect.objectContaining({ role: "user", parts: [{ type: "text", text: "My preferred color is cobalt." }] }),
       expect.objectContaining({ role: "assistant", parts: [{ type: "text", text: "Acknowledged." }] })],
    );
    expect(c.addSessionMessage).not.toHaveBeenCalled();
  });

  it("preserves explicitly supplied trusted sender context (not supplied by 2026.9.3)", async () => {
    const c = client();
    await makeEngine(c, { peer_role: "sender" }).commitTurn({
      ...turn, runtimeContext: { senderId: "alice" },
    });
    expect(c.addSessionTurn.mock.calls[0][2]).toEqual([
      expect.objectContaining({ role: "user", peer_id: "alice" }),
      expect.objectContaining({ role: "assistant" }),
    ]);
  });

  // 2026.8.1 tool-result-context-guard calls afterTurn at loop checkpoints;
  // context-engine-turn-outbox later supplies the entire accepted turn.
  it.each(["2026.8.1", "2026.8.1-1", "2026.9.3"])(
    "captures a tool turn once when %s calls both hooks", async (hostVersion) => {
      const c = client();
      const engine = makeEngine(c, {}, hostVersion);
      const messages = [
        { role: "user", content: "Check my saved color." },
        { role: "assistant", content: [{ type: "toolCall", id: "tool-1", name: "lookup", arguments: {} }] },
        { role: "toolResult", toolCallId: "tool-1", content: [{ type: "text", text: "cobalt" }] },
        { role: "assistant", content: [{ type: "text", text: "Your color is cobalt." }] },
      ];
      await engine.afterTurn!({ ...turn, sessionFile: "", messages: messages.slice(0, 3), prePromptMessageCount: 0 });
      expect(c.addSessionMessage).not.toHaveBeenCalled();
      expect(c.getSession).not.toHaveBeenCalled();
      await expect(engine.commitTurn({ ...turn, messages })).resolves.toEqual({ status: "committed" });
      expect(c.addSessionTurn).toHaveBeenCalledTimes(1);
      expect(c.addSessionTurn.mock.calls[0][2]).toHaveLength(3);
      // Even an extra finalization hook cannot write the accepted turn again.
      await engine.afterTurn!({ ...turn, sessionFile: "", messages, prePromptMessageCount: 3 });
      expect(c.addSessionMessage).not.toHaveBeenCalled();
    },
  );

  it("retains ordinary capture on hosts before accepted-turn delivery", async () => {
    const c = client();
    const engine = makeEngine(c, {}, "2026.7.31");
    await engine.afterTurn!({ ...turn, sessionFile: "", prePromptMessageCount: 0 });
    expect(c.addSessionMessage).toHaveBeenCalledTimes(2);
    expect(c.addSessionTurn).not.toHaveBeenCalled();
    await expect(engine.commitTurn(turn)).rejects.toThrow("legacy OpenClaw host");
  });

  it.each(["", "unknown"])("does not guess the capture path for host version %j", async (version) => {
    const c = client();
    const engine = makeEngine(c, {}, version);
    await expect(engine.afterTurn!({ ...turn, sessionFile: "", prePromptMessageCount: 0 }))
      .rejects.toThrow("runtime.version");
    expect(c.addSessionMessage).not.toHaveBeenCalled();
    // An actual commitTurn delivery is authoritative and can safely use receipts.
    await expect(engine.commitTurn(turn)).resolves.toEqual({ status: "committed" });
  });

  // Exact identity shape from published 2026.9.3 context-engine-turn-outbox:
  // sessionTarget has agent/session identifiers, but no runtimeContext/senderId.
  it("retains a sender-scoped turn instead of silently writing self-owned messages", async () => {
    const c = client();
    const delivered = { ...turn, sessionTarget: { agentId: "main", sessionKey: turn.sessionKey } };
    for (let attempt = 0; attempt < 2; attempt++) {
      const engine = makeEngine(c, { peer_role: "sender" });
      await expect(engine.commitTurn(delivered)).rejects.toThrow("trusted sender identity");
    }
    expect(c.addSessionTurn).not.toHaveBeenCalled();
    expect(c.addSessionMessage).not.toHaveBeenCalled();
    expect(c.getSession).not.toHaveBeenCalled();
  });

  it.each(["", "   ", "@#$"])("rejects unusable sender identity %j before writing", async (senderId) => {
    const c = client();
    await expect(makeEngine(c, { peer_role: "sender" }).commitTurn({ ...turn, runtimeContext: { senderId } }))
      .rejects.toThrow("trusted sender identity");
    expect(c.addSessionTurn).not.toHaveBeenCalled();
  });

  it.each(["none", "assistant"])("accepts real host identity with peer_role=%s", async (peer_role) => {
    const c = client();
    await expect(makeEngine(c, { peer_role }).commitTurn({ ...turn, sessionTarget: { agentId: "main" } }))
      .resolves.toEqual({ status: "committed" });
    const messages = c.addSessionTurn.mock.calls[0][2];
    expect(messages[0].peer_id).toBeUndefined();
    expect(messages[1].peer_id).toBe(peer_role === "assistant" ? "agent" : undefined);
  });

  it("uses the server receipt after an engine restart", async () => {
    const c = client();
    c.addSessionTurn.mockResolvedValueOnce("committed").mockResolvedValue("duplicate");
    await expect(makeEngine(c).commitTurn(turn)).resolves.toEqual({ status: "committed" });
    await expect(makeEngine(c).commitTurn(turn)).resolves.toEqual({ status: "duplicate" });
  });

  it("propagates delivery failures so the host retains the turn for retry", async () => {
    const c = client();
    c.addSessionTurn.mockRejectedValueOnce(new Error("lost response"));
    const engine = makeEngine(c);
    await expect(engine.commitTurn(turn)).rejects.toThrow("lost response");
    expect(c.commitSession).not.toHaveBeenCalled();
    await expect(engine.commitTurn(turn)).resolves.toEqual({ status: "committed" });
    expect(c.addSessionTurn.mock.calls[0]).toEqual(c.addSessionTurn.mock.calls[1]);
  });

  it("retries auto-commit failure without falling back to message POSTs", async () => {
    const c = client();
    c.getSession.mockResolvedValue({ pending_tokens: 10 });
    c.commitSession.mockRejectedValueOnce(new Error("commit failed"));
    const engine = makeEngine(c, { commitTokenThresholdRatio: 0 });
    await expect(engine.commitTurn(turn)).rejects.toThrow("commit failed");
    c.addSessionTurn.mockResolvedValue("duplicate");
    await expect(engine.commitTurn(turn)).resolves.toEqual({ status: "duplicate" });
    expect(c.addSessionMessage).not.toHaveBeenCalled();
  });

  it.each([
    [{ autoCapture: false }, {}],
    [{ autoCapture: false, peer_role: "sender" }, {}],
    [{ peer_role: "sender" }, { isHeartbeat: true }],
    [{}, { isHeartbeat: true }],
    [{ bypassSessionPatterns: ["agent:main:explicit:*"] }, {}],
  ])("preserves capture exclusions %j %j", async (cfg, extra) => {
    const c = client();
    await makeEngine(c, cfg).commitTurn({ ...turn, ...extra });
    expect(c.addSessionTurn).not.toHaveBeenCalled();
    expect(c.getSession).not.toHaveBeenCalled();
  });
});
