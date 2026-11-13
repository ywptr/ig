# IG internal Python integration helper

Server-side helpers for trusted applications such as Sandcastle. Python 3.11+.

Local installation:

```sh
python -m pip install /path/to/ig/integrations/python
```

Deployment installation from an immutable reviewed Git commit (replace
`FULL_40_CHARACTER_COMMIT_SHA` with the commit containing the required client):

```sh
python -m pip install 'ig-integration-client @ git+https://github.com/ywptr/ig.git@FULL_40_CHARACTER_COMMIT_SHA#subdirectory=integrations/python'
```

The same PEP 508 requirement can be placed in your deployment requirements file.
This syntax was parsed using the installed pip's requirement parser, including
the package name and subdirectory fragment. Local wheel build/install is also
tested. This is not a claim that an uncommitted change is available remotely.
Use your normal Git credentials for private repository access; do not put tokens
in the requirement URL. No public package is published.

See [the integration guide](../../docs/sandcastle-integration.md) for registration,
delegated context, capabilities, image/video submission, retries, polling, Artifact
results and errors. See [embedded editor integration](../../docs/embedded-editor.md)
for `create_editor_session()` and the completed IG#0010 launch contract.

Only `ig_client` and its HTTP/signing dependencies are required. No IG database,
application configuration, provider keys or queue credentials are needed.
