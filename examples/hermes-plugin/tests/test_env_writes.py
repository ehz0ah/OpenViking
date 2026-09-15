"""Preserve .env line endings through the external provider's setup writer."""


def test_lf_file_keeps_lf_on_every_platform(external_provider):
    """Writing one variable must not retype the whole file's line endings.

    Text mode on Windows translates "\n" to CRLF, so an LF .env came back
    with every untouched line rewritten.
    """
    home, _, module, _ = external_provider("env-lf")
    env = home / ".env"
    env.write_bytes(b"A=1\nOPENAI_API_KEY=old\nB=2\n")

    module._write_env_vars(env, {"OPENAI_API_KEY": "new"})

    assert env.read_bytes() == b"A=1\nOPENAI_API_KEY=new\nB=2\n"


def test_crlf_file_keeps_crlf(external_provider):
    """A file saved with CRLF keeps its own line ending."""
    home, _, module, _ = external_provider("env-crlf")
    env = home / ".env"
    env.write_bytes(b"A=1\r\nOPENAI_API_KEY=old\r\nB=2\r\n")

    module._write_env_vars(env, {"OPENAI_API_KEY": "new"})

    assert env.read_bytes() == b"A=1\r\nOPENAI_API_KEY=new\r\nB=2\r\n"
