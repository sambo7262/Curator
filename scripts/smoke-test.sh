#!/usr/bin/env bash
# Phase 1 go/no-go smoke test — run ON the Synology NAS from the repo dir (with .env present).
# Maps 1:1 to the four ROADMAP Phase-1 success criteria. Exits non-zero on any hard NO-GO.
set -uo pipefail

# Load .env. Values with spaces or `;` (PIA_PF_REGION, SLSKD_API_KEY) MUST be quoted in .env
# (the template quotes them) so this shell-source parses them correctly.
# shellcheck disable=SC1091
[ -f .env ] && set -a && . ./.env && set +a

FAIL=0
pass() { echo "  PASS: $*"; }
fail() { echo "  FAIL: $*"; FAIL=1; }
hdr()  { echo; echo "=== $* ==="; }

hdr "Criterion 1: VPN egress + kill-switch + non-US PF"

# 1a. slskd egress goes through PIA (not the home IP)
SLSKD_IP=$(docker exec slskd sh -c 'wget -qO- https://ipinfo.io/ip' 2>/dev/null | tr -d '[:space:]')
HOME_IP=$(curl -s https://ipinfo.io/ip | tr -d '[:space:]')
if [ -n "$SLSKD_IP" ] && [ "$SLSKD_IP" != "$HOME_IP" ]; then pass "slskd egress $SLSKD_IP != home $HOME_IP"; else fail "slskd egress ($SLSKD_IP) == home ($HOME_IP) or empty"; fi

# 1b. Forwarded port non-zero + region non-US (authed control server)
PF_JSON=$(docker exec gluetun wget -qO- --header="X-Api-Key: ${GLUETUN_API_KEY}" http://localhost:8000/v1/portforward 2>/dev/null)
PF_PORT=$(echo "$PF_JSON" | grep -oE '"port":[ ]*[0-9]+' | grep -oE '[0-9]+')
CC=$(docker exec gluetun wget -qO- --header="X-Api-Key: ${GLUETUN_API_KEY}" http://localhost:8000/v1/publicip/ip 2>/dev/null | grep -oE '"country":"[^"]*"' | cut -d'"' -f4)
if echo "$PF_JSON" | grep -qi 'unauthorized'; then fail "control server 401 — check GLUETUN_API_KEY matches on both services (Pitfall 1)"; fi
if [ -n "${PF_PORT:-}" ] && [ "${PF_PORT:-0}" -gt 0 ] 2>/dev/null; then pass "forwarded port = $PF_PORT"; else fail "forwarded port is 0/empty (US region? PF off?)"; fi
if [ -n "$CC" ] && [ "$CC" != "US" ]; then pass "VPN country = $CC (non-US)"; else fail "VPN country = '${CC:-?}' (must be non-US for PF)"; fi

# 1c. Kill-switch fail-closed
docker stop gluetun >/dev/null 2>&1
if docker exec slskd sh -c 'wget -T 5 -qO- https://ipinfo.io/ip' >/dev/null 2>&1; then fail "IP LEAK — slskd had egress with gluetun stopped"; else pass "fail-closed (slskd has no egress when gluetun down)"; fi
docker start gluetun >/dev/null 2>&1
sleep 20

hdr "Criterion 2: PF auto-sync survives restart"
# 2a. slskd self-applied the forwarded port
docker logs slskd 2>&1 | grep -i -E 'listen|forwarded|vpn' | tail -5 || true
# 2b. restart re-sync
docker restart gluetun slskd >/dev/null 2>&1 && sleep 60
PF2=$(docker exec gluetun wget -qO- --header="X-Api-Key: ${GLUETUN_API_KEY}" http://localhost:8000/v1/portforward 2>/dev/null | grep -oE '"port":[ ]*[0-9]+' | grep -oE '[0-9]+')
if [ -n "${PF2:-}" ] && [ "${PF2:-0}" -gt 0 ] 2>/dev/null; then pass "post-restart forwarded port = $PF2"; else fail "no forwarded port after restart (Pitfall 3)"; fi
docker logs slskd 2>&1 | grep -i listen | tail -2 || true

hdr "Criterion 3: reachability + CI + no baked secrets"
# 3a. slskd API reachable from synobridge via gluetun's published port
CODE=$(docker run --rm --network synobridge curlimages/curl -s -o /dev/null -w "%{http_code}" http://gluetun:5030/ 2>/dev/null)
case "$CODE" in 200|302|401) pass "slskd API alive via gluetun:5030 (HTTP $CODE)";; *) fail "slskd API not reachable via gluetun:5030 (HTTP ${CODE:-?})";; esac
# 3b. *arr reachable by container name over synobridge (INFRA-03).
#     NOTE: the curator image is python:3.12-slim and has NO curl — prove the synobridge path
#     with a curl sidecar on the same network (equivalent: curator is a plain synobridge member).
LCODE=$(docker run --rm --network synobridge curlimages/curl -s -o /dev/null -w "%{http_code}" -H "X-Api-Key: ${LIDARR_API_KEY}" http://lidarr:8686/api/v1/system/status 2>/dev/null)
[ "$LCODE" = "200" ] && pass "lidarr:8686 reachable on synobridge = 200" || fail "lidarr returned ${LCODE:-?} (FIREWALL_OUTBOUND_SUBNETS / name resolution)"
# 3c. Curator process itself is alive + reachable on synobridge (also catches a crash-loop)
RCODE=$(docker run --rm --network synobridge curlimages/curl -s -o /dev/null -w "%{http_code}" http://curator:8674/healthz 2>/dev/null)
[ "$RCODE" = "200" ] && pass "curator:8674 /healthz = 200 (curator stable on synobridge)" || fail "curator:8674 /healthz ${RCODE:-?} (curator not stable? check: docker logs curator)"
# 3d. CI reminder
echo "  NOTE: confirm GitHub Actions is green and ${DOCKERHUB_USER}/curator pushed to Docker Hub (3d)."
# 3e. No baked secrets in the image
if docker history --no-trunc "${DOCKERHUB_USER}/curator:latest" 2>/dev/null | grep -iE 'PIA_|API_KEY|PASSWORD' >/dev/null; then fail "baked secrets found in image layers"; else pass "no baked secrets in image"; fi

hdr "Criterion 4: single compose + /data + ownership + hardlink"
# 4a. stack up — WAIT for gluetun healthy + curator answering before asserting (avoid restart race)
docker compose up -d >/dev/null 2>&1
for _ in $(seq 1 30); do
  H=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' gluetun 2>/dev/null)
  C=$(docker run --rm --network synobridge curlimages/curl -s -o /dev/null -w "%{http_code}" http://curator:8674/healthz 2>/dev/null)
  { [ "$H" = "healthy" ] || [ "$H" = "none" ]; } && [ "$C" = "200" ] && break
  sleep 5
done
docker compose ps
# 4b. Curator can read /data (synobridge sidecar — no curl in the slim curator image)
READY=$(docker run --rm --network synobridge curlimages/curl -s http://curator:8674/readyz 2>/dev/null)
if echo "$READY" | grep -q '"data_mount_present":true' && echo "$READY" | grep -q '"data_readable":true'; then pass "Curator /readyz: /data present + readable"; else fail "Curator /readyz did not confirm /data: $READY"; fi
# 4c. ownership matches media user (wait for slskd to be running first)
for _ in $(seq 1 24); do [ "$(docker inspect -f '{{.State.Running}}' slskd 2>/dev/null)" = "true" ] && break; sleep 5; done
docker exec slskd sh -c 'ls -ld /data && id' 2>/dev/null || true
# 4d. hardlink across /data
HL=$(docker exec slskd sh -c '
  mkdir -p /data/downloads/soulseek /data/media/music 2>/dev/null
  touch /data/downloads/soulseek/_hltest 2>/dev/null &&
  ln /data/downloads/soulseek/_hltest /data/media/music/_hltest 2>/dev/null &&
  echo HARDLINK_OK || echo HARDLINK_FAILED
  rm -f /data/downloads/soulseek/_hltest /data/media/music/_hltest 2>/dev/null')
[ "$HL" = "HARDLINK_OK" ] && pass "hardlink works across /data (single FS)" || fail "hardlink across /data FAILED (cross-device — INFRA-06)"

echo
if [ "$FAIL" -eq 0 ]; then echo "===> GO: all Phase-1 criteria passed."; exit 0; else echo "===> NO-GO: one or more hard checks failed (see FAIL lines above)."; exit 1; fi
