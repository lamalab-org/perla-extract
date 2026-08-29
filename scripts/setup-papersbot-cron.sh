#!/bin/sh
set -eu

SERVICE_USER=perla-papersbot
SERVICE_HOME=/srv/perla-papersbot
INSTALL_ROOT=/opt/perla-papersbot
ENV_FILE=/etc/perla-papersbot.env
WRAPPER=/usr/local/bin/run-perla-papersbot
CRON_FILE=/etc/cron.d/perla-papersbot
LOGROTATE_FILE=/etc/logrotate.d/perla-papersbot
SCHEDULE='17 4 * * *'
PYTHON=python3
RELEASE=
CHECKOUT=

usage() {
    cat <<'EOF'
Usage:
  sudo scripts/setup-papersbot-cron.sh --release VERSION [OPTIONS]
  sudo scripts/setup-papersbot-cron.sh --checkout PATH [OPTIONS]

Install or update PapersBot as a protected Linux cron service.

Options:
  --release VERSION  Install a reviewed release from the package index.
  --checkout PATH    Install the exact contents of a reviewed Git checkout.
  --schedule EXPR    Five-field numeric cron expression (default: 17 4 * * *).
  --python PATH      Python used to create the virtual environment.
  -h, --help         Show this help.

The script never accepts API keys. On its first run it creates
/etc/perla-papersbot.env and stops before enabling cron. Edit that protected file,
then rerun the same command to validate it and install the schedule.
EOF
}

die() {
    printf 'setup-papersbot-cron: %s\n' "$*" >&2
    exit 1
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --release)
            [ "$#" -ge 2 ] || die '--release requires a version'
            RELEASE=$2
            shift 2
            ;;
        --checkout)
            [ "$#" -ge 2 ] || die '--checkout requires a path'
            CHECKOUT=$2
            shift 2
            ;;
        --schedule)
            [ "$#" -ge 2 ] || die '--schedule requires an expression'
            SCHEDULE=$2
            shift 2
            ;;
        --python)
            [ "$#" -ge 2 ] || die '--python requires a path'
            PYTHON=$2
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            die "unknown option: $1"
            ;;
    esac
done

[ "$(id -u)" -eq 0 ] || die 'run this script with sudo'
[ -n "$RELEASE" ] || [ -n "$CHECKOUT" ] || die 'choose --release or --checkout'
[ -z "$RELEASE" ] || [ -z "$CHECKOUT" ] || die 'choose only one installation source'
printf '%s\n' "$SCHEDULE" | grep -Eq '^([0-9*/,-]+[[:space:]]+){4}[0-9*/,-]+$' \
    || die 'schedule must be a five-field numeric cron expression'
command -v "$PYTHON" >/dev/null 2>&1 || die "Python not found: $PYTHON"
command -v flock >/dev/null 2>&1 || die 'flock is required (normally provided by util-linux)'

if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
    command -v useradd >/dev/null 2>&1 || die 'useradd is required'
    if [ -x /usr/sbin/nologin ]; then
        NOLOGIN=/usr/sbin/nologin
    elif [ -x /sbin/nologin ]; then
        NOLOGIN=/sbin/nologin
    else
        die 'could not find a nologin shell'
    fi
    useradd --system --user-group \
        --home-dir "$SERVICE_HOME" --shell "$NOLOGIN" "$SERVICE_USER"
fi
getent group "$SERVICE_USER" >/dev/null 2>&1 \
    || die "the existing $SERVICE_USER account has no same-named group"

install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0750 \
    "$SERVICE_HOME" "$SERVICE_HOME/pdfs" "$SERVICE_HOME/state" "$SERVICE_HOME/log"
install -d -o root -g root -m 0755 "$INSTALL_ROOT"

VENV="$INSTALL_ROOT/.venv"
"$PYTHON" -m venv "$VENV" \
    || die 'virtual environment creation failed; install the Python venv package'

if [ -n "$RELEASE" ]; then
    printf '%s\n' "$RELEASE" | grep -Eq '^[A-Za-z0-9.!+_-]+$' \
        || die 'release contains unsupported characters'
    "$VENV/bin/python" -m pip install "perla-extract[papersbot]==$RELEASE"
    INSTALLED_FROM="release $RELEASE"
else
    CHECKOUT=$(cd "$CHECKOUT" 2>/dev/null && pwd -P) \
        || die 'checkout directory does not exist'
    [ -f "$CHECKOUT/pyproject.toml" ] || die 'checkout has no pyproject.toml'
    command -v git >/dev/null 2>&1 || die 'git is required with --checkout'
    REVISION=$(git -C "$CHECKOUT" rev-parse --verify HEAD 2>/dev/null) \
        || die 'checkout is not a Git repository'
    [ -z "$(git -C "$CHECKOUT" status --porcelain)" ] \
        || die 'checkout is not clean; commit, move, or discard local files before deployment'
    "$VENV/bin/python" -m pip install --upgrade "${CHECKOUT}[papersbot]"
    INSTALLED_FROM="checkout $REVISION"
fi
printf '%s\n' "$INSTALLED_FROM" >"$INSTALL_ROOT/installed-from.txt"
chmod 0644 "$INSTALL_ROOT/installed-from.txt"

TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT HUP INT TERM

cat >"$TMP_DIR/run-perla-papersbot" <<'EOF'
#!/bin/sh
set -eu
umask 077

set -a
. /etc/perla-papersbot.env
set +a

healthcheck() {
    [ -n "${PAPERSBOT_HEALTHCHECK_URL:-}" ] || return 0
    printf 'url = "%s"\n' "$1" \
      | curl -q -fsS --max-time 10 --retry 3 --retry-max-time 30 \
          -o /dev/null --config - || true
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
EOF
install -o root -g root -m 0755 "$TMP_DIR/run-perla-papersbot" "$WRAPPER"

cat >"$TMP_DIR/perla-papersbot.logrotate" <<EOF
$SERVICE_HOME/log/papersbot.jsonl {
    su $SERVICE_USER $SERVICE_USER
    daily
    rotate 14
    compress
    missingok
    notifempty
    copytruncate
}
EOF
install -o root -g root -m 0644 "$TMP_DIR/perla-papersbot.logrotate" "$LOGROTATE_FILE"

CREATED_ENV=false
if [ ! -e "$ENV_FILE" ]; then
    cat >"$TMP_DIR/perla-papersbot.env" <<EOF
PAPERSBOT_DOWNLOAD_DIR=$SERVICE_HOME/pdfs
PAPERSBOT_STATE_DIR=$SERVICE_HOME/state
PAPERSBOT_LOG_FILE=$SERVICE_HOME/log/papersbot.jsonl
PAPERSBOT_LOG_LEVEL=INFO
PAPERSBOT_MAX_ATTEMPTS=4
PAPERSBOT_REQUEST_RETRIES=3
PAPERSBOT_FAIL_ON_PARTIAL=true
PAPERSBOT_HEALTHCHECK_URL=

PAPERSBOT_RSS=true
PAPERSBOT_OPENALEX=true
OPENALEX_EMAIL=project-contact@example.org
OPENALEX_API_KEY=
UNPAYWALL_EMAIL=project-contact@example.org

ZOTERO_GROUP_ID=6651379
ZOTERO_COLLECTION_KEY=SGN9PJAG
ZOTERO_API_KEY=replace-with-the-read-only-group-key
ZOTERO_CURATED=true
EOF
    install -o root -g "$SERVICE_USER" -m 0640 "$TMP_DIR/perla-papersbot.env" "$ENV_FILE"
    CREATED_ENV=true
else
    chown root:"$SERVICE_USER" "$ENV_FILE"
    chmod 0640 "$ENV_FILE"
fi

if [ "$CREATED_ENV" = true ] || grep -Eq \
    '=(replace-with-|project-contact@example\.org)(.*)$' "$ENV_FILE"; then
    rm -f "$CRON_FILE"
    printf '\nInstalled PapersBot from %s.\n' "$INSTALLED_FROM"
    printf 'Cron is not enabled because %s still needs configuration.\n' "$ENV_FILE"
    printf 'Edit it with: sudoedit %s\n' "$ENV_FILE"
    printf 'Then rerun this same setup command.\n'
    exit 0
fi

has_value() {
    grep -Eq "^$1=[^[:space:]]+([[:space:]]*)$" "$ENV_FILE"
}

config_die() {
    rm -f "$CRON_FILE"
    die "$*; cron has been disabled"
}

for REQUIRED_SETTING in \
    PAPERSBOT_DOWNLOAD_DIR PAPERSBOT_STATE_DIR PAPERSBOT_LOG_FILE; do
    has_value "$REQUIRED_SETTING" \
        || config_die "$ENV_FILE has no value for $REQUIRED_SETTING"
done

if grep -Eqi '^PAPERSBOT_OPENALEX=(true|1|yes|on)$' "$ENV_FILE"; then
    has_value OPENALEX_EMAIL \
        || config_die "$ENV_FILE has no OPENALEX_EMAIL for enabled OpenAlex discovery"
fi
if grep -Eqi '^ZOTERO_CURATED=(true|1|yes|on)$' "$ENV_FILE"; then
    for REQUIRED_SETTING in ZOTERO_GROUP_ID ZOTERO_COLLECTION_KEY ZOTERO_API_KEY; do
        has_value "$REQUIRED_SETTING" \
            || config_die "$ENV_FILE has no value for enabled Zotero setting $REQUIRED_SETTING"
    done
fi
if has_value ZOTERO_GROUP_ID; then
    grep -Eq '^ZOTERO_GROUP_ID=[0-9]+$' "$ENV_FILE" \
        || config_die "$ENV_FILE has a non-numeric ZOTERO_GROUP_ID"
fi
if ! grep -Eqi \
    '^(PAPERSBOT_RSS|PAPERSBOT_OPENALEX)=(true|1|yes|on)$' "$ENV_FILE" \
    && ! has_value ZOTERO_GROUP_ID; then
    config_die "$ENV_FILE must enable RSS, OpenAlex, or a Zotero group"
fi
if has_value PAPERSBOT_HEALTHCHECK_URL; then
    grep -Eq \
        '^PAPERSBOT_HEALTHCHECK_URL=https?://[A-Za-z0-9._~:/?=%+-]+$' "$ENV_FILE" \
        || config_die "$ENV_FILE has an invalid PAPERSBOT_HEALTHCHECK_URL"
    command -v curl >/dev/null 2>&1 \
        || config_die 'curl is required when heartbeat monitoring is enabled'
fi

cat >"$TMP_DIR/perla-papersbot.cron" <<EOF
SHELL=/bin/sh
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
MAILTO=""

$SCHEDULE $SERVICE_USER $WRAPPER
EOF
install -o root -g root -m 0644 "$TMP_DIR/perla-papersbot.cron" "$CRON_FILE"

printf '\nInstalled PapersBot from %s.\n' "$INSTALLED_FROM"
printf 'Cron schedule: %s\n' "$SCHEDULE"
printf 'Run once now: sudo -u %s %s\n' "$SERVICE_USER" "$WRAPPER"
printf "Check status: sudo -u %s jq -e '.status == \"complete\"' %s/state/last_run.json\n" \
    "$SERVICE_USER" "$SERVICE_HOME"
