# Run PapersBot on an internal machine

PapersBot does not depend on a particular scheduler. Its command accepts every
operational option from the environment, writes resumable state to an ordinary
directory, and exits nonzero when a run fails. A cron deployment therefore needs no
GitHub-specific wrapper or cloud PDF archive.

## Install and allocate storage

Install a pinned release in a virtual environment owned by a dedicated service user.
Keep PDFs, state, and logs outside the source checkout:

```text
/srv/perla-papersbot/
├── pdfs/
├── state/
│   ├── state.json
│   ├── last_run.json
│   └── runs/
└── log/
    └── papersbot.jsonl
```

```bash
python -m venv /opt/perla-papersbot/.venv
/opt/perla-papersbot/.venv/bin/pip install 'perla-extract[papersbot]'
```

The service account needs write access only to `/srv/perla-papersbot`. Restrict that
tree according to the access conditions of the PDFs it may contain.

## Configure one source-composable run

Store configuration in `/etc/perla-papersbot.env`, readable only by the service
account and administrators:

```bash
PAPERSBOT_DOWNLOAD_DIR=/srv/perla-papersbot/pdfs
PAPERSBOT_STATE_DIR=/srv/perla-papersbot/state
PAPERSBOT_LOG_FILE=/srv/perla-papersbot/log/papersbot.jsonl
PAPERSBOT_LOG_LEVEL=INFO
PAPERSBOT_MAX_ATTEMPTS=4

# Enable any combination of discovery sources.
PAPERSBOT_RSS=true
PAPERSBOT_OPENALEX=true
OPENALEX_EMAIL=project-contact@example.org
UNPAYWALL_EMAIL=project-contact@example.org

# Optional curated Zotero intake.
ZOTERO_GROUP_ID=123456
ZOTERO_COLLECTION_KEY=ABCD1234
ZOTERO_API_KEY=replace-with-a-read-only-group-key
ZOTERO_CURATED=true

# Writes remain separate opt-ins.
ZOTERO_SAVE=false
ZOTERO_PDF_POLICY=never
```

`PAPERSBOT_RSS=false` and `PAPERSBOT_OPENALEX=false` make this a Zotero-only job.
Omitting the Zotero variables makes it an RSS/OpenAlex job. Command-line options
override environment values, so the same installation can also run explicit
backfills.

The initial Zotero key should be limited to read access for the intended group. Do not
place it in the repository, command line, crontab, structured logs, or run state.

## Prevent overlapping cron runs

Use one small wrapper so secrets are loaded before the process starts and `flock`
prevents two invocations from changing the same state directory:

```sh
#!/bin/sh
set -eu
umask 077

set -a
. /etc/perla-papersbot.env
set +a

exec /usr/bin/flock -n "$PAPERSBOT_STATE_DIR/cron.lock" \
  /opt/perla-papersbot/.venv/bin/perla-papersbot
```

For example, run it daily at 04:17:

```cron
17 4 * * * /usr/local/bin/run-perla-papersbot
```

Cron can report a nonzero exit through the institution's ordinary monitoring or mail
path. `state/last_run.json` is the health-check target; `state/runs/*.json` and the
JSONL log are the durable audit trail. Configure ordinary log rotation for the JSONL
file, but do not rotate or delete run ledgers under the log policy.

## Keep discovery separate from access policy

RSS, OpenAlex, and Zotero only say how the bot learned that a paper exists. PDF
acquisition is an ordered list of `PdfSource` implementations. The built-in list first
uses a stored Zotero attachment when available and then checks open-access locations.
Each successful source records both its name and an explicit access basis in state.

An institutionally authorized retrieval service can implement the same small Python
interface and be passed to `run_papersbot(pdf_sources=[...])`. It may use institutional
network access, a library API, or another approved mechanism; the core bot does not
guess that being on a particular network is sufficient authorization. Source failures
are retained in `acquisition_failures` even when a later source succeeds.

```python
from pathlib import Path

from perla_extract.papersbot import AcquiredPdf, PaperRecord, run_papersbot


class InstitutionalLibrary:
    name = "institutional-library"

    def acquire(
        self, record: PaperRecord, destination: Path
    ) -> AcquiredPdf | None:
        """Retrieve only through the institution's approved access mechanism."""

        # Deployment-specific resolution and authorization belong here.
        return None


result = run_papersbot(
    "/srv/perla-papersbot/pdfs",
    state_dir="/srv/perla-papersbot/state",
    pdf_sources=[InstitutionalLibrary()],
)
```

The adapter writes the candidate bytes to `destination` and provides an auditable
`access_basis`; PapersBot independently checks the PDF signature and stores a SHA-256
fingerprint before accepting it. The adapter should enforce the applicable licence,
rate limits, authentication, and retention policy rather than encoding publisher
exceptions in PapersBot.
