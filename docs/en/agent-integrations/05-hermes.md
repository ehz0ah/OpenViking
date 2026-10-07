# Hermes

[Hermes Agent](https://hermes-agent.nousresearch.com/) by Nous Research supports
OpenViking as a memory provider. Depending on the Hermes release, the provider
is installed from the plugin catalog or bundled in Hermes.

## Install the provider

On a catalog-based Hermes build, run:

```bash
hermes plugins install openviking --enable
```

Skip this command if your Hermes release still bundles OpenViking. The bundled
copy takes precedence while it is present. When a Hermes update removes the
bundle from a home that already uses `memory.provider: openviking`, Hermes
attempts to install the catalog plugin automatically. If migration cannot
install it, run the command above in the same Hermes profile.

## Setup and chat

```bash
hermes memory setup openviking
hermes
```

Choose **Personal Agent** or **Shared Agent**, then choose a connection:

- **Quick Local**: reuse a supported LLM configured in Hermes. Setup installs a
  private OpenViking server and a local embedding model, checks the LLM and
  starts the server. Extraction can use a remote LLM.
- **OpenViking Service**: enter your service API key.
- **Custom**: enter your server URL and credentials, or reuse an `ovcli.conf`
  profile if the wizard offers one.

Quick Local requires the external provider to be active. It keeps its server
running after Hermes exits. Other self-hosted servers should run in their own
Python environment or container; Hermes connects over HTTP.

After conversations are captured, OpenViking commits and extracts memories
for later recall. Setup does not change existing server data.

## Update

For a catalog installation:

```bash
hermes plugins update openviking
```

Restart Hermes or the gateway afterward. Connection settings and server data
are retained.

## Verify

```bash
hermes memory status
```

`available` means that the provider is configured. It does not check server
connectivity or confirm memory extraction.

## See also

- [Capability Reference](./16-capability-reference.md)
- [OpenViking plugin page](https://hermes-agent.nousresearch.com/docs/plugins/openviking) — full setup guide and configuration options
- [Deployment Guide](../guides/03-deployment.md) — setting up your OpenViking server
- [Authentication](../guides/04-authentication.md) — API key setup for remote access
