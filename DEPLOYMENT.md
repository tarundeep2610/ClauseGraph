# Deployment guide: CPU server + GPU server

This service now runs across two machines:

- **CPU server** — runs `docker-compose.yml` at the repo root: the FastAPI app (`glm-ocr-api`), job orchestration, the frontend, and AuthNexus SSO/RBAC. This is the machine end users and integrating projects talk to.
- **GPU server** — runs `gpu-ocr-service/docker-compose.yml`: the OCR model inference (`gpu-ocr-api`) and Ollama, reachable **only** from the CPU server over a private Tailscale network.

Order matters: do the one-time admin steps first, then **bring up the GPU machine before the CPU machine** — the CPU box's `.env` needs the GPU box's Tailscale address (obtained via `tailscale ip -4` on the GPU box, see Part 1 step 6). Both machines' Tailscale logins are host-level and persist independently of these compose stacks — verify the address is current before using it.

## 0. One-time admin steps

These are done once for the whole setup, not per-machine.

1. **Tailscale.** Both the CPU and GPU servers are shared machines that already have Tailscale logged in **at the host level** under the same account the sibling "itender"/RokkaAI project uses on them (confirmed via `tailscale status` showing both as peers). Neither machine runs its own Tailscale client/container for this project anymore — there's nothing to set up here. The tailnet's ACL policy is confirmed default-open, so the CPU box can already reach the GPU box on any port (including `11610`) with no explicit grant needed. If that ever changes (a restrictive custom ACL gets introduced later), add a grant: Source = the CPU box's device, Destination = the GPU box's device, port `11610` tcp — but don't introduce new tags on these shared devices without confirming it won't change what the other project can reach (tagging a device switches it from owner-based default ACLs to tag-based ones).
2. **Shared secret.** Generate the CPU↔GPU shared secret once:
   ```
   python -c "import secrets; print(secrets.token_hex(32))"
   ```
   This exact value goes into **both** machines' `.env` as `GPU_OCR_SHARED_SECRET`.
3. **AuthNexus project registration.** Register OCR as a new project/client in the same AuthNexus/Zitadel org tenant the sibling "itender" project uses. This depends on whoever administers that tenant — you need: `AUTH_AUTHORITY` (the tenant base URL), a new `PROJECT_ID`, the `ORG_ID`, and a new `CLIENT_ID`, with the redirect URI registered as `https://<cpu-server-address>/callback`.

   **If you don't have AuthNexus admin access yet**, everything else below can proceed with `DEV_MODE=true` — the service will use its own local dev-login instead (`POST /api/auth/dev/login` with `dev-admin-001` / `dev-user-001`), and you can swap in real AuthNexus values later without any other changes.

## 1. GPU machine — first

**This deployment's GPU box is Windows + Docker Desktop (WSL2 backend, GPU support enabled)**, with the image pre-built elsewhere and transferred in (e.g. via Google Drive) rather than built on the box itself. Commands below are PowerShell; adjust if the box is actually Linux.

1. Confirm GPU passthrough works through Docker Desktop's WSL2 backend before anything else:
   ```powershell
   docker run --rm --gpus all nvidia/cuda:12.1.1-base-ubuntu22.04 nvidia-smi
   ```
   This needs NVIDIA's CUDA-on-WSL driver installed on the Windows host and GPU support enabled in Docker Desktop's settings — if this fails, fix that before going further.
2. Get `gpu-ocr-service/docker-compose.yml`, `Caddyfile`, `.env`, and the built image tarball (`ocr-gpu-api.tar.gz`) onto the box (however you're transferring — Google Drive, scp, etc.) into one directory.
3. Load the pre-built image — `docker load` handles the gzip-compressed tarball directly, no separate unzip step needed on any OS:
   ```powershell
   docker load -i ocr-gpu-api.tar.gz
   ```
4. Confirm the CUDA version the image was built for (cu121 by default) actually matches this GPU's driver — check with `nvidia-smi`. If it doesn't match, the image needs rebuilding with the right `--extra-index-url` in `requirements.txt` rather than patched after the fact.
5. `docker compose up -d` — this uses the loaded image directly (no rebuild) for `gpu-ocr-api`, and pulls `ollama` and `caddy` fresh from their registries. There is no `tailscale` service in this stack — see the comment block at the top of `docker-compose.yml`.
6. Confirm the Windows host's EXISTING Tailscale login is active — this is the SAME login the other project on this box already uses; do not log in again or create a new node. Open a normal PowerShell window directly on the Windows host (NOT `docker compose exec` — there's no Tailscale container here) and run:
   ```powershell
   tailscale status
   tailscale ip -4
   ```
   (if `tailscale.exe` isn't on PATH, it's typically at `C:\Program Files\Tailscale\tailscale.exe`). Note the IPv4 address reported here — the CPU box's `.env` needs this as `<gpu-tailscale-ip>`.
7. Sanity-check Ollama: `docker compose exec ollama ollama list`.
8. Sanity-check the consolidated proxy from inside this box — everything on this machine goes through the single port `11610`, path-routed by `Caddyfile` (`/ollama/*` → Ollama, everything else → gpu-ocr-api):
   ```powershell
   docker compose exec gpu-proxy wget -q -O- --header="X-Gpu-Ocr-Key: wrong" http://localhost:11610/ollama/api/tags   # expect 401
   docker compose exec gpu-proxy wget -q -O- --header="X-Gpu-Ocr-Key: $env:GPU_OCR_SHARED_SECRET" http://localhost:11610/ollama/api/tags  # expect 200
   docker compose exec gpu-proxy wget -q -O- http://localhost:11610/health   # expect 200, no header needed
   ```
   Also confirm the GPU machine's own firewall (Windows Firewall, if applicable) actually allows inbound TCP on `11610` — a locally-blocked port causes the exact same symptom (connection timeout) as a Tailscale ACL or routing problem, and is easy to mistake for one:
   ```powershell
   New-NetFirewallRule -DisplayName "GPU OCR Proxy 11610" -Direction Inbound -LocalPort 11610 -Protocol TCP -Action Allow
   ```
   **Optional hardening:** scope this to the tailnet's CIDR only, so `11610` isn't reachable from the regular LAN/WAN at the network layer either (the `X-Gpu-Ocr-Key` shared-secret check in `Caddyfile` remains the actual access control regardless — same tradeoff already accepted for `rokkaai-app:11600` on this box):
   ```powershell
   New-NetFirewallRule -DisplayName "GPU OCR Proxy 11610 (tailnet only)" -Direction Inbound -LocalPort 11610 -Protocol TCP -RemoteAddress 100.64.0.0/10 -Action Allow
   ```
   Then remove the broader unscoped rule above once this scoped one is verified working from an actual tailnet peer. Test this deliberately (try from both a tailnet machine and a plain LAN machine) before relying on it — Windows Firewall interacting with Docker Desktop's WSL2 port-publish bridge already caused one real failure in this exact setup (the network-profile issue that originally forced the public-IP workaround), so treat CIDR-scoping as an optional extra layer to validate, not an assumed-working guarantee.
9. Leave this box running.

## 2. CPU machine — second

1. Install Docker + Docker Compose (no GPU toolkit needed).
2. Clone this repo.
3. Create the repo-root `.env`:
   ```
   GPU_OCR_SHARED_SECRET=<the same value from step 0.2>
   GPU_OCR_BASE_URL=http://<gpu-tailscale-ip>:11610
   OLLAMA_BASE_URL=http://<gpu-tailscale-ip>:11610/ollama
   AUTH_AUTHORITY=<from step 0.3, or leave blank if using DEV_MODE>
   PROJECT_ID=<from step 0.3>
   ORG_ID=<from step 0.3>
   CLIENT_ID=<from step 0.3>
   DEV_MODE=false
   DEV_SECRET=
   ```
   Get `<gpu-tailscale-ip>` from running `tailscale ip -4` on the GPU box (Part 1 step 6). For a dev/test box without AuthNexus access yet, instead set `DEV_MODE=true` and `DEV_SECRET=<a random value>`.
4. `docker compose build`
5. `docker compose up -d` — there is no `tailscale` service in this stack; `glm-ocr-api` is published directly on `12006`.
6. Confirm this box's existing Tailscale login is still active: `tailscale status` directly on the host (e.g. your Linux host/VM already logged into the same account as the GPU box — nothing to log in again here). Should show the GPU box as a peer.
7. Verify CPU→GPU reachability. Note: the CPU-side image has no `wget`/`curl` — use Python directly (it already has `requests` installed):
   ```
   docker compose exec glm-ocr-api python -c "import requests; r = requests.get('http://<gpu-tailscale-ip>:11610/health', timeout=5); print(r.status_code); print(r.text)"
   ```
   If this fails (times out / connection refused), check this host's own firewall (`ufw`/`firewalld`/`iptables`, if any is active) for a rule blocking forwarded container traffic to `100.64.0.0/10` — on a standard Linux Docker host, a container's outbound traffic is routed through the host's own routing table (which already has a route for `100.64.0.0/10` via `tailscale0`), but a restrictive host firewall can still block it.
8. Open `http://<cpu-server-address>:12006/` in a browser (or your real domain once fronted by a reverse proxy) — with `DEV_MODE=false` this redirects to AuthNexus's hosted login page; with `DEV_MODE=true`, the app calls `POST /api/auth/dev/login` for a local test persona instead.
9. Log in and confirm the role-gated UI loads correctly (once the React frontend from Part 3 is in place).

## 3. Final cross-machine checks

1. Submit a real multi-page PDF via the UI (or directly via `POST /api/jobs` with a Bearer token or `X-API-Key`) with `engine=unlimited`, then again with `engine=glm`; confirm both complete.
2. From a machine that is **not** on the tailnet, attempt to reach the GPU box's port `11610`.
   - If the Windows Firewall rule was scoped to the tailnet CIDR (`100.64.0.0/10`, Part 1 step 8's optional hardening) and that scoping was verified working: this should be refused/timeout — the original security property holds.
   - If only the broad (unscoped) firewall rule is in place: this will succeed at the TCP level — by design, the same tradeoff already accepted for `rokkaai-app:11600` on this box — and the `X-Gpu-Ocr-Key` shared-secret header is the actual access control in that case, not network isolation. Confirm which posture is actually in place on this box before treating this check as a hard security boundary.
3. Run the automated test suites before calling the rollout done:
   ```
   pip install -r requirements-dev.txt && pytest
   cd gpu-ocr-service && pip install -r requirements.txt pytest pytest-asyncio && pytest
   ```

## Local testing without a GPU

To run both stacks on a single dev machine with no GPU (e.g. to test end-to-end before a real rollout), use the dedicated local-test files instead of the prod ones above — the prod `docker-compose.yml`/`.env` files in each directory stay untouched:

- `docker-compose.local-test.yml` (root) — the CPU app, with no tailscale sidecar (reusing the prod `TS_AUTHKEY` for a second local node just hangs on `NeedsLogin`); `glm-ocr-api`'s port is published directly instead.
- `gpu-ocr-service/docker-compose.local-test.yml` — the GPU service with no GPU device reservation; `unlimited_ocr_engine.py` already detects `torch.cuda.is_available()==False` and falls back to CPU/float32 on its own. Unlike prod's `docker-compose.yml`, this file still runs its own tailscale sidecar (fine for a personal dev machine with no conflicting host-level login) and uses its own `Caddyfile.local-test` (pinned to `localhost:*` upstreams) instead of the shared `Caddyfile`.
- `.env.local-test` and `gpu-ocr-service/.env.local-test` — standalone env files for each stack (own copy of secrets — not shared with each other or with the prod `.env` files), pre-tuned for CPU speed (lower concurrency, longer poll timeout, `GPU_OCR_BASE_URL` pointed at `host.docker.internal` instead of the tailnet).

Bring both up with:
```powershell
.\run-local-test.ps1
```
Then check `http://localhost:12006/` (CPU app) and `http://localhost:11610/health` (GPU service — `model_loaded` flips to `true` once the model finishes loading, which is slow on first run while weights download into the `hf_cache` volume).

## Creating integration API keys

Once logged in as an `admin`, other projects that need to call this OCR API programmatically get a static key instead of an AuthNexus login:

```
curl -X POST https://<cpu-server>/api/auth/api-keys \
  -H "Authorization: Bearer <admin JWT>" \
  -H "Content-Type: application/json" \
  -d '{"name": "tender-ai-integration", "role": "user"}'
```

The response's `raw_key` is shown once — store it now. The integration then sends it as `X-API-Key: <raw_key>` on every request instead of an `Authorization` header. Revoke with `DELETE /api/auth/api-keys/{key_id}`.
