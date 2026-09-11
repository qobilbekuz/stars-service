# Telegram Stars Service

Telegram **Stars**, **Premium** va **Gift** sotib olishni avtomatlashtiruvchi production-ready REST API.

FastAPI + PostgreSQL + Redis/ARQ. Buyurtmalar asinxron bajariladi, natija webhook orqali yetkaziladi.

---

## Nima qila oladi

| Endpoint | Vazifa | Provayder |
|---|---|---|
| `POST /v1/orders/stars` | Foydalanuvchiga N dona Stars sotib olib berish | `fragment` |
| `POST /v1/orders/premium` | Premium obuna sovg'a qilish (3/6/12 oy) | `fragment`, `botapi` |
| `POST /v1/orders/gift` | Telegram sovg'asi jo'natish | `botapi` |
| `GET /v1/orders/{id}` | Buyurtma holati va hodisalar tarixi | — |
| `GET /v1/catalog/gifts` | Mavjud sovg'alar katalogi | `botapi` |

Asosiy xususiyatlar:

- **Idempotentlik** — `Idempotency-Key` sarlavhasi bilan tarmoq uzilishida takroriy to'lov bo'lmaydi
- **Avtomatik qayta urinish** — vaqtinchalik xatoliklarda eksponensial backoff bilan
- **Imzolangan webhooklar** — HMAC-SHA256, Stripe uslubida
- **Audit izi** — har bir status o'zgarishi `order_events` jadvalida
- **Rate limiting** — Redis sliding-window, mijoz bo'yicha
- **Provayder abstraktsiyasi** — kredensiallaringizga qarab moslashtirasiz

---

## Provayderlar

Servis uchta provayderni qo'llab-quvvatlaydi. `.env` da `DEFAULT_PROVIDER` orqali tanlanadi,
yoki har bir so'rovda `"provider": "..."` bilan bekor qilinadi.

### `mock` — dev/test uchun
Hech qanday tashqi chaqiruv qilmaydi. Deterministik test ssenariylari:

| Username prefiksi | Xatti-harakat |
|---|---|
| `fail_...` | Doimiy xatolik (retry qilinmaydi) |
| `retry_...` | Vaqtinchalik xatolik (retry qilinadi) |
| `slow_...` | 3 soniya kechikish |

### `fragment` — Stars va Premium
[`fragment-api-lib`](https://pypi.org/project/fragment-api-lib/) orqali ishlaydi. To'lov sizning
TON hamyoningizdan yechiladi.

```bash
pip install fragment-api-lib
```

```ini
FRAGMENT_ENABLED=true
FRAGMENT_SEED="24 so'zli TON seed fraza"
FRAGMENT_KYC=false            # true bo'lsa FRAGMENT_COOKIES ham kerak
FRAGMENT_COOKIES='{...}'      # Fragment.com'dan eksport qilingan cookie'lar
```

> ⚠️ Fragment'ning rasmiy API'si yo'q — `fragment-api-lib` uchinchi tomon xizmati ustidan ishlaydi.
> Seed-frazangiz to'lov qilish huquqini beradi: `.env` faylni `chmod 600` qiling va hech qachon
> git'ga qo'shmang. Production'da avval kichik summada sinab ko'ring.

### `botapi` — Premium va Gift
Rasmiy [Telegram Bot API](https://core.telegram.org/bots/api) (`sendGift`,
`giftPremiumSubscription`, `getAvailableGifts`). To'lov **botning o'z Stars balansidan** amalga
oshadi — TON hamyon kerak emas. Balansni `createInvoiceLink` (XTR valyutasi) orqali to'ldirasiz.

```ini
BOTAPI_ENABLED=true
BOT_TOKEN=123456:ABC-DEF...
```

Cheklovlar:
- Stars sotib olib berishni qo'llab-quvvatlamaydi (buning uchun `fragment`)
- `recipient_user_id` talab qiladi (username emas) — foydalanuvchi botingiz bilan muloqot qilgan bo'lishi kerak

---

## Tez boshlash

### 1. Bog'liqliklar

```bash
make install          # .venv yaratadi va requirements-dev.txt o'rnatadi
cp .env.example .env  # sozlamalarni to'ldiring
```

`.env` da majburiy o'zgartiriladigan qiymatlar:

```ini
ADMIN_API_KEY=<openssl rand -hex 32>
WEBHOOK_SECRET=<openssl rand -hex 32>
POSTGRES_PASSWORD=<kuchli parol>
```

### 2. Baza

```bash
sudo -u postgres psql -c "CREATE USER stars WITH PASSWORD 'stars_password';"
sudo -u postgres psql -c "CREATE DATABASE stars_service OWNER stars;"
make migrate
```

### 3. Ishga tushirish

```bash
make api      # API server  -> http://localhost:8000/docs
make worker   # fon worker  (alohida terminalda)
```

Ikkalasi ham kerak: API buyurtmani qabul qiladi, worker uni bajaradi.

### 4. Birinchi mijoz

```bash
make client name="Mening botim"
```

Chiqqan API kalitni saqlab qo'ying — u qayta ko'rsatilmaydi (bazada faqat sha256 hash saqlanadi).

---

## Foydalanish

### Stars sotib olish

```bash
curl -X POST http://localhost:8000/v1/orders/stars \
  -H "X-API-Key: sk_live_..." \
  -H "Idempotency-Key: buyurtma-12345" \
  -H "Content-Type: application/json" \
  -d '{"recipient_username": "@durov", "quantity": 100}'
```

`202 Accepted` qaytadi — buyurtma navbatga tushdi:

```json
{
  "id": "b424db85-43e5-400d-a7f2-4ae0b3f682e9",
  "type": "stars",
  "status": "queued",
  "recipient_username": "durov",
  "quantity": 100,
  "provider": "fragment"
}
```

### Natijani olish

```bash
curl http://localhost:8000/v1/orders/b424db85-... -H "X-API-Key: sk_live_..."
```

```json
{
  "status": "completed",
  "provider_ref": "97a3f1...",
  "amount": "0.430000000",
  "currency": "TON",
  "attempts": 1,
  "events": [
    {"to_status": "pending",    "note": "Buyurtma yaratildi"},
    {"to_status": "queued",     "note": "Navbatga qo'yildi"},
    {"to_status": "processing", "note": "Bajarilmoqda (urinish 1)"},
    {"to_status": "completed",  "note": "Muvaffaqiyatli bajarildi"}
  ]
}
```

### Buyurtma holatlari

```
pending ──> queued ──> processing ──> completed
                            │
                            ├──> failed      (doimiy xatolik yoki urinishlar tugadi)
                            └──> pending     (vaqtinchalik xatolik -> qayta urinish)
```

`cancelled` — foydalanuvchi bekor qilgan (faqat `pending`/`queued` holatida mumkin).

---

## Webhooklar

Mijozga `webhook_url` o'rnatilgan bo'lsa, buyurtma yakunlanganda `order.completed` yoki
`order.failed` hodisasi yuboriladi. Yetkazilmasa 6 martagacha qayta uriniladi
(1m, 2m, 4m, 8m, 16m, 32m).

```json
{
  "event": "order.completed",
  "created_at": "2026-07-20T11:53:37Z",
  "data": { "id": "...", "status": "completed", "provider_ref": "...", "amount": "0.43" }
}
```

### Imzoni tekshirish (majburiy)

Har bir so'rovda `X-Webhook-Signature: t=<unix_ts>,v1=<hmac_sha256>` keladi.
Imzolanadigan matn — `"{timestamp}.{body}"`, kalit — mijozning `webhook_secret`i.

```python
import hashlib, hmac, time

def verify(body: bytes, header: str, secret: str, tolerance: int = 300) -> bool:
    parts = dict(p.split("=", 1) for p in header.split(","))
    ts = int(parts["t"])
    if abs(int(time.time()) - ts) > tolerance:      # replay hujumidan himoya
        return False
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, parts["v1"])
```

Endpointingiz `2xx` qaytarishi kerak — aks holda qayta yuboriladi. Shuning uchun ishlov berish
**idempotent** bo'lsin (`data.id` bo'yicha dedupe qiling).

---

## Admin API

`X-Admin-Key` sarlavhasi talab qilinadi (`.env` dagi `ADMIN_API_KEY`).

| Endpoint | Vazifa |
|---|---|
| `POST /v1/admin/clients` | Yangi mijoz + API kalit |
| `GET /v1/admin/clients` | Mijozlar ro'yxati |
| `PATCH /v1/admin/clients/{id}` | Sozlamalarni yangilash |
| `POST /v1/admin/clients/{id}/rotate-key` | Kalitni almashtirish |
| `DELETE /v1/admin/clients/{id}` | Faolsizlantirish |
| `GET /v1/admin/stats` | Buyurtmalar statistikasi |

---

## Deploy

### Docker (tavsiya etiladi)

```bash
cp .env.example .env   # to'ldiring
make docker-up
```

Postgres, Redis, migratsiya, API va workerni birga ko'taradi. API `127.0.0.1:8000` da
ochiladi — oldiga nginx qo'ying.

### systemd (hozirgi o'rnatma)

```bash
sudo cp deploy/stars-api.service deploy/stars-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now stars-api stars-worker

sudo systemctl status stars-api
journalctl -u stars-api -f          # loglar journald'da
```

Jonli manzil: **https://api.qobilbek.dev/stars/**

| | |
|---|---|
| API | `https://api.qobilbek.dev/stars/v1/...` |
| Docs | `https://api.qobilbek.dev/stars/docs` |
| Health | `https://api.qobilbek.dev/stars/v1/health` |
| Ichki port | `127.0.0.1:8710` (2 ta uvicorn worker) |

nginx `/stars/` prefiksini olib tashlab uzatadi, uvicorn esa `--root-path /stars`
bilan ishlaydi — shuning uchun `/docs` va `openapi.json` to'g'ri manzillarni ko'rsatadi.

`.env` fayl `www:www` egaligida va `chmod 600` bo'lishi shart — servis `www`
nomidan ishlaydi va aks holda ishga tushmaydi.

### Production tekshiruv ro'yxati

- [ ] `ENV=production`, `DEBUG=false`, `LOG_JSON=true`
- [ ] `ADMIN_API_KEY` va `WEBHOOK_SECRET` — tasodifiy 32+ bayt
- [ ] `CORS_ORIGINS` — `["*"]` emas, aniq domenlar
- [ ] `.env` fayl `chmod 600`, git'da yo'q
- [ ] Postgres va Redis tashqaridan ochiq emas
- [ ] HTTPS (nginx + certbot)
- [ ] Baza zaxira nusxasi sozlangan

---

## Ishlab chiqish

```bash
make test     # testlar
make cov      # qamrov hisoboti
make lint     # ruff tekshiruvi
make fmt      # avtoformatlash
make revision m="izoh"   # yangi migratsiya
```

Testlar alohida `stars_service_test` bazasida, `mock` provayder bilan ishlaydi —
hech qanday real to'lov amalga oshmaydi.

---

## Arxitektura

```
                  ┌──────────┐
   HTTP ─────────>│   API    │──> Postgres  (buyurtmalar, audit, mijozlar)
                  │ FastAPI  │──> Redis     (navbat, rate limit)
                  └──────────┘
                        │ enqueue
                        v
                  ┌──────────┐
                  │  Worker  │──> Provayder (fragment / botapi)
                  │   ARQ    │──> Webhook   (imzolangan, retry bilan)
                  └──────────┘
```

```
app/
├── api/v1/       # HTTP endpointlar (orders, catalog, admin, health)
├── core/         # config, logging, security, exceptions, redis
├── db/           # SQLAlchemy engine va sessiya
├── models/       # ORM modellar
├── providers/    # fragment / botapi / mock — almashtiriladigan adapterlar
├── schemas/      # Pydantic validatsiya
├── services/     # biznes mantiq (orders, webhooks, rate_limit)
└── workers/      # ARQ worker va navbat
```

Yangi provayder qo'shish uchun `app/providers/base.py` dagi `BaseProvider` ni meros qilib
oling va `registry.py` ga ro'yxatdan o'tkazing — qolgan kod o'zgarmaydi.

---

## Litsenziya va mas'uliyat

Bu servis Telegram va Fragment'ning **rasmiy mahsuloti emas**. Fragment integratsiyasi
norasmiy uchinchi tomon kutubxonasiga tayanadi va istalgan vaqtda ishlamay qolishi mumkin.
Foydalanishdan oldin Telegram va Fragment shartlarini o'qib chiqing. Moliyaviy risk
butunlay sizning zimmangizda.
