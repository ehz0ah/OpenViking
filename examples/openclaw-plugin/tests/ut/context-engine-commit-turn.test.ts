import { describe, expect, it, vi } from "vitest";
import type { OpenVikingClient } from "../../client.js";
import { memoryOpenVikingConfigSchema } from "../../config.js";
import { createMemoryOpenVikingContextEngine } from "../../context-engine.js";
import { openClawSessionToOvStorageId } from "../../routing/identity-routing.js";

function makeEngine(client: Record<string, unknown>, overrides: Record<string, unknown> = {}) {
  return createMemoryOpenVikingContextEngine({
    id: "openviking", name: "OpenViking", version: "test",
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

  it("preserves sender identity from a host runtime context", async () => {
    const c = client();
    await makeEngine(c, { peer_role: "sender" }).commitTurn({
      ...turn, runtimeContext: { senderId: "alice" },
    });
    expect(c.addSessionTurn.mock.calls[0][2]).toEqual([
      expect.objectContaining({ role: "user", peer_id: "alice" }),
      expect.objectContaining({ role: "assistant" }),
    ]);
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
    [{}, { isHeartbeat: true }],
    [{ bypassSessionPatterns: ["agent:main:explicit:*"] }, {}],
  ])("preserves capture exclusions %j %j", async (cfg, extra) => {
    const c = client();
    await makeEngine(c, cfg).commitTurn({ ...turn, ...extra });
    expect(c.addSessionTurn).not.toHaveBeenCalled();
    expect(c.getSession).not.toHaveBeenCalled();
  });
});
