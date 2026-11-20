# Deployment guide: CPU server + GPU server

This service now runs across two machines:

- **CPU server** — runs `docker-compose.yml` at the repo root: the FastAPI app (`glm-ocr-api`), job orchestration, the frontend, and AuthNexus SSO/RBAC. This is the machine end users and integrating projects talk to.
- **GPU server** — runs `gpu-ocr-service/docker-compose.yml`: the OCR model inference (`gpu-ocr-api`) and Ollama, reachable **only** from the CPU server over a private Tailscale network.

Order matters: do the one-time admin steps first, then **bring up the GPU machine before the CPU machine** — the CPU box's `.env` needs the GPU box's Tailscale MagicDNS name, which only exists after the GPU box has come up once.

## 0. One-time admin steps

These are done once for the whole setup, not per-machine.

1. **Tailscale ACL tags.** In the Tailscale admin console (Access controls → Definitions), declare two tags, `tag:ocr-cpu` and `tag:ocr-gpu`. Then under Access controls → Policies → Add rule, add a grant with Source `tag:ocr-cpu`, Destination `tag:ocr-gpu`, Port and protocol `11610` (tcp) — that's the single consolidated port for everything on the GPU box (see below; confirm the actual allowed port on that machine before setting this, a typo here cost real debugging time once already). Remove the default "All ports and protocols" entry so nothing else is reachable.
2. **Tailscale auth keys.** Generate two separate reusable, tagged auth keys — one allowed to use `tag:ocr-cpu`, one allowed to use `tag:ocr-gpu`. Each goes into exactly one machine's `.env` below.
3. **Shared secret.** Generate the CPU↔GPU shared secret once:
   ```
   python -c "import secrets; print(secrets.token_hex(32))"
   ```
   This exact value goes into **both** machines' `.env` as `GPU_OCR_SHARED_SECRET`.
4. **AuthNexus project registration.** Register OCR as a new project/client in the same AuthNexus/Zitadel org tenant the sibling "itender" project uses. This depends on whoever administers that tenant — you need: `AUTH_AUTHORITY` (the tenant base URL), a new `PROJECT_ID`, the `ORG_ID`, and a new `CLIENT_ID`, with the redirect URI registered as `https://<cpu-server-address>/callback`.

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
5. `docker compose up -d` — this uses the loaded image directly (no rebuild) for `gpu-ocr-api`, and pulls `ollama`, `caddy`, and `tailscale` fresh from their registries.
6. Verify Tailscale came up: `docker compose exec tailscale tailscale status` — should show itself connected, and (once the CPU box is up too) the CPU box as a peer.
7. Note this box's MagicDNS name — check the Tailscale admin console's device list for the `ocr-gpu` device, or `docker compose exec tailscale tailscale status --self=true --json`. Write down the `<name>.<tailnet>.ts.net` value; the CPU machine needs it.
8. Sanity-check Ollama: `docker compose exec ollama ollama list`.
9. Sanity-check the consolidated proxy from inside this box — everything on this machine goes through the single port `11610`, path-routed by `Caddyfile` (`/ollama/*` → Ollama, everything else → gpu-ocr-api):
   ```powershell
   docker compose exec gpu-proxy wget -q -O- --header="X-Gpu-Ocr-Key: wrong" http://localhost:11610/ollama/api/tags   # expect 401
   docker compose exec gpu-proxy wget -q -O- --header="X-Gpu-Ocr-Key: $env:GPU_OCR_SHARED_SECRET" http://localhost:11610/ollama/api/tags  # expect 200
   docker compose exec gpu-proxy wget -q -O- http://localhost:11610/health   # expect 200, no header needed
   ```
   Also confirm the GPU machine's own firewall (Windows Firewall, if applicable) actually allows inbound TCP on `11610` — a locally-blocked port causes the exact same symptom (connection timeout) as a Tailscale ACL or routing problem, and is easy to mistake for one:
   ```powershell
   New-NetFirewallRule -DisplayName "GPU OCR Proxy 11610" -Direction Inbound -LocalPort 11610 -Protocol TCP -Action Allow
   ```
10. Leave this box running.

## 2. CPU machine — second

1. Install Docker + Docker Compose (no GPU toolkit needed).
2. Clone this repo.
3. Create the repo-root `.env`:
   ```
   TS_AUTHKEY=<the tag:ocr-cpu key from step 0.2>
   GPU_OCR_SHARED_SECRET=<the same value from step 0.3>
   GPU_OCR_BASE_URL=http://<gpu-tailscale-ip>:11610
   OLLAMA_BASE_URL=http://<gpu-tailscale-ip>:11610/ollama
   AUTH_AUTHORITY=<from step 0.4, or leave blank if using DEV_MODE>
   PROJECT_ID=<from step 0.4>
   ORG_ID=<from step 0.4>
   CLIENT_ID=<from step 0.4>
   DEV_MODE=false
   DEV_SECRET=
   ```
   For a dev/test box without AuthNexus access yet, instead set `DEV_MODE=true` and `DEV_SECRET=<a random value>`.
4. `docker compose build`
5. `docker compose up -d`
6. Verify Tailscale: `docker compose exec tailscale tailscale status` — should show the GPU box as a peer.
7. Verify CPU→GPU reachability. Note: the CPU-side image has no `wget`/`curl` — use Python directly (it already has `requests` installed). Also note MagicDNS names may not resolve inside the container even when the tailnet itself works fine — use the GPU box's raw Tailscale IP (`tailscale ip -4` on that box, or the admin console's device list) if the hostname fails to resolve:
   ```
   docker compose exec glm-ocr-api python -c "import requests; r = requests.get('http://<gpu-tailscale-ip>:11610/health', timeout=5); print(r.status_code); print(r.text)"
   ```
8. Open `http://<cpu-server-address>:12006/` in a browser (or your real domain once fronted by a reverse proxy) — with `DEV_MODE=false` this redirects to AuthNexus's hosted login page; with `DEV_MODE=true`, the app calls `POST /api/auth/dev/login` for a local test persona instead.
9. Log in and confirm the role-gated UI loads correctly (once the React frontend from Part 3 is in place).

## 3. Final cross-machine checks

1. Submit a real multi-page PDF via the UI (or directly via `POST /api/jobs` with a Bearer token or `X-API-Key`) with `engine=unlimited`, then again with `engine=glm`; confirm both complete.
2. From a machine that is **not** on the tailnet, confirm the GPU box's port `11610` is unreachable — connection refused/timeout. This is the core security property: nothing on the GPU box is exposed to the public internet.
3. Run the automated test suites before calling the rollout done:
   ```
   pip install -r requirements-dev.txt && pytest
   cd gpu-ocr-service && pip install -r requirements.txt pytest pytest-asyncio && pytest
   ```

## Creating integration API keys

Once logged in as an `admin`, other projects that need to call this OCR API programmatically get a static key instead of an AuthNexus login:

```
curl -X POST https://<cpu-server>/api/auth/api-keys \
  -H "Authorization: Bearer <admin JWT>" \
  -H "Content-Type: application/json" \
  -d '{"name": "tender-ai-integration", "role": "user"}'
```

The response's `raw_key` is shown once — store it now. The integration then sends it as `X-API-Key: <raw_key>` on every request instead of an `Authorization` header. Revoke with `DELETE /api/auth/api-keys/{key_id}`.
