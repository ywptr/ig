# IG internal Python integration helper

Server-side helpers for trusted applications such as Sandcastle. Python 3.11+.

Install: `python -m pip install /path/to/ig/integrations/python`.

See [the integration guide](../../docs/sandcastle-integration.md) for registration,
delegated context, capabilities, image/video submission, retries, polling, Artifact
results, errors and the reserved IG#0010 boundary. No public package is published.

Only `ig_client` and its HTTP/signing dependencies are required. No IG database,
application configuration, provider keys or queue credentials are needed.
