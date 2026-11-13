# Running Nyro on a VPS that already runs QuickBites (or anything else)

Nyro adds **one small container** (`nyro-relay`, 256 MB limit). It never binds ports 80/443 and never touches
your other containers. QuickBites keeps running exactly as it does now.

## 0. Before you start
- Add a **separate subdomain** for Nyro, e.g. `nyro.yourdomain.com`, with a DNS A record to the VPS.
  (The phone app needs HTTPS for the microphone, so it needs its own certificate.)
- Look at how QuickBites is exposed: `docker ps` and note which container owns ports 80/443.
  - **A reverse proxy** (nginx, Nginx Proxy Manager, Caddy, Traefik) -> follow "A" below.
  - **QuickBites publishes its own port** (e.g. `3000:3000`), nothing on 80/443 -> follow "B".

## 1. Start the relay (both cases)
```bash
git clone <this repo> nyro && cd nyro
cp vps/.env.example .env           # set NYRO_PAIR_TOKEN (openssl rand -hex 32); NYRO_DOMAIN only matters for option B
docker compose up -d --build       # relay only, listening on 127.0.0.1:8000
curl -s http://127.0.0.1:8000/healthz
```
If port 8000 is taken, set `NYRO_PORT=8010` in `.env`. Only ever use `docker compose` inside the `nyro` folder
(it has its own project name), and avoid `docker system prune` / `docker compose down --remove-orphans` elsewhere.

## A. You already have a reverse proxy -> add one site block for Nyro
The upstream is `127.0.0.1:8000` (proxy on the host). If your proxy itself runs **in Docker** and cannot see the
host's 127.0.0.1, attach the relay to its network instead and use `nyro-relay:8000` as the upstream:
```bash
# in .env: NYRO_PROXY_NETWORK=<your proxy's network from `docker network ls`>
docker compose -f docker-compose.yml -f docker-compose.network.yml up -d --build
```
WebSockets must be allowed and the idle timeout must be long. **Set** (do not append) the client IP header.

**nginx**
```nginx
server {
  server_name nyro.yourdomain.com;
  # listen 443 ssl; + your certificate lines (certbot / existing setup)
  location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_read_timeout 3600s;
  }
}
```
**Caddy (existing)**: add `nyro.yourdomain.com { reverse_proxy 127.0.0.1:8000 }` (Caddy handles WebSockets itself).

**Nginx Proxy Manager**: new Proxy Host, domain `nyro.yourdomain.com`, forward to `nyro-relay` port `8000`
(needs the network override above) or the host IP, tick **Websockets Support**, request an SSL certificate.

**Traefik**: add labels to the relay with a `Host(\`nyro.yourdomain.com\`)` router on your websecure entrypoint and
service port 8000 (use the network override above so Traefik can reach it).

## B. QuickBites owns no 80/443 -> let Nyro's Caddy serve both
Run the bundled Caddy (`docker compose --profile caddy up -d`) and edit `vps/Caddyfile` to add QuickBites as a
second site, e.g.
```
quickbites.yourdomain.com {
	reverse_proxy host.docker.internal:3000
}
```
(add `extra_hosts: ["host.docker.internal:host-gateway"]` to the caddy service). Then the DNS for both names
points at the VPS and Caddy gets both certificates automatically.

## 2. Pair
Open `https://nyro.yourdomain.com` on the phone, enter the token, "Add to Home screen". On the laptop, set
`relay.url` to `wss://nyro.yourdomain.com/ws/host` (the installer asks for it) and the same token.

## Resource check (4 GB VPS)
Relay <= 256 MB, optional Caddy <= 128 MB, almost no CPU when idle. Check with `docker stats --no-stream`.

## Safe to roll back
`docker compose down` inside the `nyro` folder removes only Nyro. Your QuickBites containers, volumes and
proxy config are never modified by Nyro's files.
