#!/bin/sh
# PostgreSQL local para desenvolvimento e testes (socket /tmp, porta 5433, usuário postgres sem senha).
# Idempotente: inicializa o cluster na primeira vez, sobe se estiver parado e cria os bancos atem e atem_test.
set -e
PGBIN=${PGBIN:-$(ls -d /usr/lib/postgresql/*/bin 2>/dev/null | sort -V | tail -1)}
DATA=${PGDATA_DEV:-/var/tmp/pg/data}
PORT=5433
if [ -z "$PGBIN" ]; then echo "PostgreSQL não encontrado (instale postgresql ou defina PGBIN)"; exit 1; fi
run_as_pg() { if [ "$(id -u)" = "0" ]; then su postgres -c "$1"; else sh -c "$1"; fi; }
if ! pg_isready -h /tmp -p $PORT >/dev/null 2>&1; then
  if [ ! -d "$DATA" ]; then
    mkdir -p "$(dirname "$DATA")"; [ "$(id -u)" = "0" ] && chown postgres "$(dirname "$DATA")"
    run_as_pg "$PGBIN/initdb -D $DATA -U postgres -A trust >/dev/null"
  fi
  rm -f "$DATA/postmaster.pid"
  run_as_pg "$PGBIN/pg_ctl -D $DATA -o '-p $PORT -k /tmp -c listen_addresses=localhost' -l $(dirname "$DATA")/log start >/dev/null"
  sleep 3
fi
for db in atem atem_test; do
  psql -h /tmp -p $PORT -U postgres -tc "select 1 from pg_database where datname='$db'" | grep -q 1 \
    || psql -h /tmp -p $PORT -U postgres -c "create database $db" >/dev/null
done
pg_isready -h /tmp -p $PORT
echo "DATABASE_URL=postgresql+psycopg://postgres@/atem?host=/tmp&port=$PORT"
