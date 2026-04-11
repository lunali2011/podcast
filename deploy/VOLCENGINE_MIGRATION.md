# Volcengine Full-Site Migration

## Goal

Serve the whole site from ECS instead of splitting between:

- GitHub Pages for frontend
- ECS for backend

Target shape:

- `https://lunapodcast.top` -> static site from `/root/podcast`
- `https://lunapodcast.top/api/*` -> Python backend on `127.0.0.1:8765`

## DNS

Add these DNS records:

- `A` `@` -> `115.190.112.80`
- `A` `www` -> `115.190.112.80`

## ECS Security Group

Keep these inbound rules:

- TCP 22
- TCP 80
- TCP 443

Port 8765 can be removed later if public direct access is no longer needed.

## Backend

The backend is already expected to run through:

- `systemd` service: `luna-podcast-api`

Check:

```bash
systemctl status luna-podcast-api --no-pager -l
```

## Nginx

Use the template file:

- [`/Users/liyuemei/Desktop/实验agent/podcast/deploy/lunapodcast.nginx.conf`](/Users/liyuemei/Desktop/实验agent/podcast/deploy/lunapodcast.nginx.conf)

Copy to server:

```bash
scp -i /Users/liyuemei/Downloads/podcast-ecs.pem /Users/liyuemei/Desktop/实验agent/podcast/deploy/lunapodcast.nginx.conf root@115.190.112.80:/root/
```

Then on ECS:

```bash
mv /root/lunapodcast.nginx.conf /etc/nginx/sites-available/lunapodcast
ln -sf /etc/nginx/sites-available/lunapodcast /etc/nginx/sites-enabled/lunapodcast
rm -f /etc/nginx/sites-enabled/podcast-api
nginx -t
systemctl reload nginx
```

## HTTPS

After root-domain DNS resolves, run:

```bash
certbot --nginx -d lunapodcast.top -d www.lunapodcast.top
```

Choose redirect to HTTPS when prompted.

## Frontend Runtime Config

For same-origin deployment:

- [`/Users/liyuemei/Desktop/实验agent/podcast/site-config.js`](/Users/liyuemei/Desktop/实验agent/podcast/site-config.js)

should stay:

```js
window.LUNA_API_BASE = "";
```

That makes frontend requests use `/api/...` on the same domain.

## Validation

After cutover:

```bash
curl -I https://lunapodcast.top
curl https://lunapodcast.top/api/albums
```

And browser checks:

- `https://lunapodcast.top`
- `https://lunapodcast.top/studio.html`

