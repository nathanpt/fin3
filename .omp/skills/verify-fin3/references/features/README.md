# Features

One line per area, linking to its file. Drive everything through
`scripts/verify.py` unless a trap says otherwise.

- [CLI contract](cli-contract.md): the installed `fin3` process — subcommands, JSON envelopes, exit codes, config from env
- [Manifest planning](manifest-planning.md): declarative datasets → coverage comparison → planned actions, dry-run safety
- [Retrieval](retrieval.md): `get_data()` idempotent covered-range reads and the canonical frame contract

Run the offline set as one command from the repo root:

```
uv run python scripts/verify.py all          # cli + plan + idempotent, machine-readable JSON
uv run python scripts/verify.py store --human  # read-only check against the configured MinIO
```

Exit codes: 0 all passed, 1 a check failed, 2 setup error (message on stderr).
