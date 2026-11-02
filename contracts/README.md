# HTTP contract

This directory is reserved for the versioned HTTP/OpenAPI contract shared by
the server extension, CLI, and agent skill. The server implementation owns
the generated contract; CLI and skill consumers must not import server code.

The first contract extraction is tracked in #47.
