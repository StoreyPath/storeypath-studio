#!/bin/sh
# StoreyPath Studio's image starts here: its own database first, then Studio.
#
#   storeypath-start serve --host 0.0.0.0 --port 8080 --data /data    (the image's CMD)
#   storeypath-start <any storeypath command and its arguments>
#
# Unless STOREYPATH_DATABASE_URL names another PostgreSQL, the image's own runs for
# the commands that use Studio's data (serve, users, backup, restore, db):
#
# - the first time, `initdb` makes it in /data/pg (this user's, UTF-8 with the
#   builtin C.UTF-8 locale, which no system update changes), reached over a Unix
#   socket in /run/postgresql only: no TCP port, and only this user may connect
#   (as `storeypath`, or as `postgres` to administer it);
# - it is started and waited for; the role and database `storeypath` and the PostGIS
#   extension are made when missing (Studio connects as `storeypath`, which owns its
#   database and is not a superuser);
# - then `storeypath <arguments>` runs, with STOREYPATH_DATABASE_URL pointing at the
#   socket; when it ends (docker stop sends SIGTERM, passed on to it), the database
#   is stopped cleanly after it, so nothing is lost.
#
# One container at a time runs a volume's database: a second one started on the same
# volume says so and stops (run commands in the first: docker exec <name> storeypath …).
# Other commands (views, convert, … on files) run without a database. The database's
# own log: /data/pg/log/ (a file a day, the last seven kept).
set -eu

socket=/run/postgresql
own="postgresql:///storeypath?host=$socket"
pgdata="${PGDATA:-/data/pg}"
major="$(postgres -V | sed -E 's/^[^0-9]*([0-9]+).*/\1/')"  # "postgres (PostgreSQL) 17.11 (Debian …)"

case "${1:-}" in
    serve | users | backup | restore | db) uses_data=1 ;;
    *) uses_data= ;;
esac
if [ -z "$uses_data" ] || [ "${STOREYPATH_DATABASE_URL:-$own}" != "$own" ]; then
    exec storeypath "$@"
fi
export STOREYPATH_DATABASE_URL="$own"

say() { echo "database: $*"; }
fail() { echo "database: $*" >&2; exit 1; }
as_admin() { PGOPTIONS="-c client_min_messages=warning" psql -X -q -v ON_ERROR_STOP=1 -h "$socket" -U postgres "$@"; }

# Asked to stop while starting: start, then stop at once.
stopping=
trap 'stopping=1' TERM INT

mkdir -p "$pgdata" 2>/dev/null || fail "cannot make $pgdata: is /data writable by $(id -un)?"
chmod 0700 "$pgdata"
# Held for as long as the database runs (its processes keep it too): a database
# file left behind by a container that was killed is then known to be stale.
exec 9<"$pgdata"
flock -n 9 || fail "the database in $pgdata is in use by another container on the same volume; run the command there (docker exec <its name> storeypath $*)"

if [ ! -s "$pgdata/PG_VERSION" ]; then
    say "making Studio's database in $pgdata"
    out="$(initdb -D "$pgdata" -U postgres --encoding=UTF8 --locale-provider=builtin --locale=C.UTF-8 \
                  --auth-local=peer --auth-host=reject 2>&1)" || { echo "$out" >&2; fail "initdb failed"; }
    # Over the socket, this user only: as `storeypath` (Studio) or `postgres` (to administer).
    cat > "$pgdata/pg_hba.conf" <<'EOF'
# TYPE  DATABASE  USER  METHOD
local   all       all   peer map=studio
EOF
    cat > "$pgdata/pg_ident.conf" <<'EOF'
# MAP   SYSTEM-USER  DATABASE-USER
studio  storeypath   storeypath
studio  storeypath   postgres
EOF
    cat >> "$pgdata/postgresql.conf" <<EOF

# StoreyPath Studio (docker/start.sh): a Unix socket only, and a log a day, a week kept
listen_addresses = ''
unix_socket_directories = '$socket'
logging_collector = on
log_directory = 'log'
log_filename = 'postgresql-%a.log'
log_rotation_age = 1d
log_rotation_size = 0
log_truncate_on_rotation = on
EOF
fi
made="$(cat "$pgdata/PG_VERSION")"
[ "$made" = "$major" ] || fail "$pgdata was made by PostgreSQL $made and this image has $major: move it over with pg_upgrade (or a backup from the older image restored into this one)"

rm -f "$pgdata/postmaster.pid"  # stale: this container holds the lock
# listen_addresses and the socket again on the command line, whatever the file says
if ! pg_ctl -D "$pgdata" -l "$pgdata/start.log" -w -t 120 \
        -o "-c listen_addresses='' -c unix_socket_directories='$socket'" start >/dev/null; then
    tail -n 20 "$pgdata/start.log" >&2 || true
    fail "PostgreSQL did not start"
fi
waited=0
until pg_isready -q -h "$socket" -U postgres -d postgres; do
    waited=$((waited + 1))
    [ "$waited" -lt 60 ] || fail "PostgreSQL does not answer on $socket"
    sleep 1
done

if [ "$(as_admin -d postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname = 'storeypath'")" != 1 ]; then
    as_admin -d postgres -c "CREATE ROLE storeypath LOGIN"
fi
if [ "$(as_admin -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname = 'storeypath'")" != 1 ]; then
    as_admin -d postgres -c "CREATE DATABASE storeypath OWNER storeypath"
fi
as_admin -d storeypath -c "CREATE EXTENSION IF NOT EXISTS postgis"
# a newer PostGIS in a newer image: the database's copy brought up to it
as_admin -d storeypath -c "ALTER EXTENSION postgis UPDATE" 2>/dev/null || say "PostGIS could not be updated (it still works)"
say "$(as_admin -d storeypath -tAc "SELECT 'PostgreSQL ' || current_setting('server_version') || ', PostGIS ' || extversion FROM pg_extension WHERE extname = 'postgis'") in $pgdata, on $socket"

stop_database() {
    say "stopping"
    pg_ctl -D "$pgdata" -m fast -w -t 60 stop >/dev/null || pg_ctl -D "$pgdata" -m immediate stop >/dev/null || true
}

if [ -n "$stopping" ]; then
    stop_database
    exit 143
fi

storeypath "$@" &
child=$!
trap 'kill -TERM "$child" 2>/dev/null || true' TERM INT
[ -z "$stopping" ] || kill -TERM "$child" 2>/dev/null || true
status=0
wait "$child" || status=$?
while kill -0 "$child" 2>/dev/null; do  # wait was cut short by a signal: Studio is stopping
    status=0
    wait "$child" || status=$?
done
stop_database
exit "$status"
