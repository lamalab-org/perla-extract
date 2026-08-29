# Run PapersBot as an internal cron job

The supported unattended deployment is a Linux machine with persistent local
storage. PapersBot writes resumable state, downloaded PDFs, structured logs, and an
immutable ledger for each run. Keeping those files together is important: a
`downloaded` record is reopened automatically when its recorded PDF is missing or no
longer matches its SHA-256 fingerprint.

## Quick setup

The repository includes an idempotent installer for a conventional Linux host. Use a
reviewed release once it is published:

```bash
sudo ./scripts/setup-papersbot-cron.sh --release X.Y.Z
```

To test an unreleased but reviewed checkout, install its exact committed contents:

```bash
sudo ./scripts/setup-papersbot-cron.sh --checkout "$PWD"
```

The checkout must have no tracked or untracked changes. The installed release or
commit SHA is written to `/opt/perla-papersbot/installed-from.txt` for later
inspection.

The first invocation creates the service account, virtual environment, persistent
directories, wrapper, protected configuration template, and log-rotation policy. It
does **not** enable cron while placeholder values remain. Edit the template without
putting secrets in shell history, then run the same setup command again:

```bash
sudoedit /etc/perla-papersbot.env
sudo ./scripts/setup-papersbot-cron.sh --release X.Y.Z
```

The second invocation preserves the environment file, validates that its placeholders
were replaced, updates the installed package, and creates `/etc/cron.d/perla-papersbot`.
Use `--schedule '17 4 * * *'` to change the default daily schedule. Run the script with
`--help` for all options.

The remaining sections explain each installed component and are also useful when the
host does not follow this conventional filesystem layout.

## Create a restricted service account

The examples below use a non-login account named `perla-papersbot`. Adjust the
`nologin` path for the Linux distribution when necessary.

```bash
sudo useradd --system \
  --home-dir /srv/perla-papersbot \
  --shell /usr/sbin/nologin \
  perla-papersbot

sudo install -d -o perla-papersbot -g perla-papersbot -m 0750 \
  /srv/perla-papersbot \
  /srv/perla-papersbot/pdfs \
  /srv/perla-papersbot/state \
  /srv/perla-papersbot/log
```

The resulting persistent layout is:

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

Back up `pdfs/` and `state/` together. State contains absolute local paths and hashes;
restoring only one of the two directories creates an incomplete snapshot.

## Install a reviewed version

Use a dedicated virtual environment and pin the released version that was reviewed.
Update `PERLA_RELEASE` deliberately during upgrades; do not leave an unconstrained
`pip install` in an unattended deployment.

```bash
PERLA_RELEASE='X.Y.Z'  # Replace with the reviewed release.
sudo python3 -m venv /opt/perla-papersbot/.venv
sudo /opt/perla-papersbot/.venv/bin/pip install \
  "perla-extract[papersbot]==${PERLA_RELEASE}"
```

Before a release containing this workflow exists, install a reviewed repository
checkout instead and retain its exact commit SHA in the deployment record.

## Store configuration and secrets

Create an environment file readable by root and the service account, but not other
users:

```bash
sudo install -o root -g perla-papersbot -m 0640 /dev/null \
  /etc/perla-papersbot.env
sudoedit /etc/perla-papersbot.env
```

The current PERLA group and journal-club collection use group `6651379` and collection
`SGN9PJAG`. Copy the dedicated read-only Zotero key directly into this file; do not
place it in the repository, command line, crontab, logs, or a support message.

```bash
PAPERSBOT_DOWNLOAD_DIR=/srv/perla-papersbot/pdfs
PAPERSBOT_STATE_DIR=/srv/perla-papersbot/state
PAPERSBOT_LOG_FILE=/srv/perla-papersbot/log/papersbot.jsonl
PAPERSBOT_LOG_LEVEL=INFO
PAPERSBOT_MAX_ATTEMPTS=4
PAPERSBOT_REQUEST_RETRIES=3
PAPERSBOT_FAIL_ON_PARTIAL=true
PAPERSBOT_HEALTHCHECK_URL=

PAPERSBOT_RSS=true
PAPERSBOT_OPENALEX=true
OPENALEX_EMAIL=project-contact@example.org
OPENALEX_API_KEY=replace-with-a-free-openalex-key
UNPAYWALL_EMAIL=project-contact@example.org

ZOTERO_GROUP_ID=6651379
ZOTERO_COLLECTION_KEY=SGN9PJAG
ZOTERO_API_KEY=replace-with-the-read-only-group-key
ZOTERO_CURATED=true
```

`OPENALEX_API_KEY` is optional for small tests but recommended for scheduled use.
OpenAlex grants a larger free request budget to authenticated clients. The key is sent
as a bearer header and, like the Zotero key, is represented only as an enabled/disabled
flag in run configuration.

`PAPERSBOT_HEALTHCHECK_URL` is optional. Give it the private ping URL from a hosted
or internal Healthchecks-compatible service to receive alerts for failures and missed
runs. Treat this URL as a secret: anyone who knows it can alter the apparent status of
the check. The wrapper sends only start, success, and numeric exit-status requests. It
does not send paper metadata, logs, or extracted content.

For example, after creating a check at Healthchecks.io or a compatible internal
deployment, set:

```bash
PAPERSBOT_HEALTHCHECK_URL=https://hc-ping.com/private-check-uuid
```

Use `PAPERSBOT_RSS=false` and `PAPERSBOT_OPENALEX=false` for a Zotero-only run. Omit
the Zotero variables for an RSS/OpenAlex-only run. `PAPERSBOT_FAIL_ON_PARTIAL=true`
makes recorded discovery or acquisition errors observable to cron as exit code 2,
after the JSON run ledger has been written. Scientific outcomes such as `no_pdf`
remain inspectable in the ledger without being treated as infrastructure failures.

## Install the locked wrapper

The wrapper loads secrets before the process starts, applies a private umask, uses
`flock` to prevent concurrent writers, and optionally reports start, success, and
failure to a heartbeat monitor. Monitoring requests have short timeouts, and a failed
monitoring request does not change the literature run's result.

```sh
#!/bin/sh
set -eu
umask 077

set -a
. /etc/perla-papersbot.env
set +a

healthcheck() {
    [ -n "${PAPERSBOT_HEALTHCHECK_URL:-}" ] || return 0
    curl -fsS --max-time 10 --retry 3 -o /dev/null "$1" || true
}

HEALTHCHECK_URL=${PAPERSBOT_HEALTHCHECK_URL:-}
HEALTHCHECK_URL=${HEALTHCHECK_URL%/}
healthcheck "$HEALTHCHECK_URL/start"

set +e
/usr/bin/flock -n "$PAPERSBOT_STATE_DIR/cron.lock" \
  /opt/perla-papersbot/.venv/bin/perla-papersbot \
  >/dev/null 2>"$PAPERSBOT_STATE_DIR/last_cron.stderr"
STATUS=$?
set -e

if [ "$STATUS" -eq 0 ]; then
    : >"$PAPERSBOT_STATE_DIR/last_cron.stderr"
    healthcheck "$HEALTHCHECK_URL"
else
    healthcheck "$HEALTHCHECK_URL/$STATUS"
fi
exit "$STATUS"
```

Save it as `/usr/local/bin/run-perla-papersbot`:

```bash
sudo chown root:root /usr/local/bin/run-perla-papersbot
sudo chmod 0755 /usr/local/bin/run-perla-papersbot
```

Run it once as the service account before scheduling it:

```bash
sudo -u perla-papersbot /usr/local/bin/run-perla-papersbot
sudo -u perla-papersbot jq -e '.status == "complete"' \
  /srv/perla-papersbot/state/last_run.json
```

The second command must print `true`. Inspect `discovery_failures`,
`acquisition_failures`, and `outcomes` in `last_run.json` before accepting the first
run.

## Schedule and monitor it

Create `/etc/cron.d/perla-papersbot`:

```cron
SHELL=/bin/sh
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
MAILTO=""

17 4 * * * perla-papersbot /usr/local/bin/run-perla-papersbot
```

The minimum health check is both a recent `last_run.json` and
`status == "complete"`. Every run is also retained under `state/runs/<run-id>.json`,
so longitudinal statistics do not depend on console output. An interrupted process
leaves a `running` ledger; a fatal exception leaves `failed`; recoverable source or
paper failures produce `complete_with_errors` and a nonzero scheduled exit. The
wrapper records stderr from the most recent failed command in
`state/last_cron.stderr`; it empties that file after a successful run.

With a heartbeat URL configured, the monitoring service should expect the same cron
schedule plus a reasonable grace period. It can notify by email, chat, or another
integration when it receives a failure status or when the success ping never arrives.
The latter also detects a stopped cron daemon, an offline host, or a broken wrapper.
Cron mail is explicitly disabled so routine progress logs do not generate messages.

Configure log rotation with `copytruncate`, because the process opens the Loguru file
sink directly:

```text
/srv/perla-papersbot/log/papersbot.jsonl {
    su perla-papersbot perla-papersbot
    daily
    rotate 14
    compress
    missingok
    notifempty
    copytruncate
}
```

Run ledgers are audit data, not logs. Apply the project's retention policy to them
separately.

## Understand the acquisition boundary

RSS, OpenAlex, and Zotero describe how the bot learned that a paper exists. PDF
acquisition is an ordered list of `PdfSource` implementations. The built-in list first
uses every stored Zotero PDF and then checks public open-access locations when Zotero
has none. Every retained document records its local path, SHA-256, source URL, source,
access basis, original label, and filename.

PapersBot deliberately does not guess which stored attachment is the article or SI.
Zotero titles and filenames are preserved so a downstream review or extraction planner
can assign that role with evidence. Open-access DOI retrieval is explicitly marked as
the article because that resolver returns the article PDF.

An institutionally authorized retriever can implement `PdfSource` and pass it to
`run_papersbot(pdf_sources=[...])`. It may use a library API or another approved local
mechanism, but it must enforce the applicable licence, authentication, rate limits,
and retention policy. The core validates every returned PDF and records provenance;
it does not infer authorization merely from being on a university network.
