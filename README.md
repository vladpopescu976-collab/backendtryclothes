# TryClothes RunPod Backend

Creat de Vlad Popescu.

Acest folder este versiunea pregătită pentru:

- GitHub
- RunPod Serverless Load Balancing
- FastAPI + auth + email verification + stylist AI light + brands/fit + CatVTON warmup

Repo-ul acesta conține doar backendul. Aplicația iOS rămâne separată.

## Ce pui pe GitHub

Urcă exact conținutul acestui folder:

`/Users/vladpopescu/Documents/New project/tryclothes-runpod-backend`

Nu urca:

- `.env`
- `.venv`
- `storage/`
- baze de date locale
- rezultate sau poze generate
- cache-uri locale
- weights mari

CatVTON nu este inclus în repo. Docker build-ul îl clonează automat din:

- `https://github.com/Zheng-Chong/CatVTON.git`

și îl fixează la commitul:

- `7818397f25613beedb3d861a34769f607cfcf3b1`

## Ce face repo-ul acesta

- pornește backendul pe `FastAPI`
- expune `POST /api/v1/auth/register`
- expune `POST /api/v1/auth/login`
- expune `POST /api/v1/stylist/recommend`
- expune `POST /api/v1/tryon/jobs`
- expune `POST /api/v1/tryon/warmup`
- expune `GET /api/v1/health`
- expune `GET /ping` pentru health check RunPod

## Fișiere importante

- `Dockerfile` - imaginea folosită de RunPod
- `start-server.sh` - pornește `uvicorn` și setează cache-urile
- `runpod.env.example` - valorile recomandate pentru RunPod
- `.env.example` - varianta generală de configurare

## Important pentru serverless

Pentru `serverless`, login-ul și create account trebuie să folosească o bază de date externă, altfel poți pierde conturile la cold start.

Recomandare simplă:

- `Supabase Postgres`
- sau `Neon Postgres`

Pentru test pe un `GPU Pod` simplu, poți folosi și `SQLite`.

## Variabile obligatorii în RunPod

Folosește valorile din `runpod.env.example`.

Minimul de care ai nevoie:

```env
APP_ENV=production
SECRET_KEY=replace-with-a-long-random-secret
APP_PUBLIC_BASE_URL=https://your-runpod-endpoint-url
DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST:5432/DATABASE
EMAIL_DELIVERY_MODE=resend
EMAIL_FROM=TryClothes <noreply@yourdomain.com>
RESEND_API_KEY=replace-with-your-resend-api-key
TRYON_PROVIDER=catvton
CATVTON_PROJECT_DIR=/opt/CatVTON
CATVTON_PRELOAD_ON_STARTUP=false
PORT=8000
PORT_HEALTH=8000
```

## De ce `CATVTON_PRELOAD_ON_STARTUP=false`

Pentru serverless, vrem ca workerul să pornească mai repede.

Fluxul recomandat este:

1. userul intră în `Garderobe`
2. aplicația cheamă `POST /api/v1/tryon/warmup`
3. backendul începe să încarce modelul
4. aplicația face poll la `GET /api/v1/tryon/warmup`
5. când apare `ready`, userul poate genera rezultatul

Astfel, cold start-ul este ascuns cât timp userul alege pozele.

## Cum urci pe GitHub

Exemplu:

```bash
cd "/Users/vladpopescu/Documents/New project/tryclothes-runpod-backend"
git init
git branch -M main
git add .
git commit -m "Prepare TryClothes backend for RunPod serverless"
git remote add origin https://github.com/USERNAME/tryclothes-backend.git
git push -u origin main
```

## Cum îl legi în RunPod

În RunPod:

1. `Serverless`
2. `Create new deployment`
3. `Custom deployment`
4. `Deploy from GitHub`
5. selectezi repo-ul backend

Setări recomandate:

- endpoint type: `Load Balancing`
- gpu: `RTX 4090` sau similar
- active workers: `0`
- max workers: `1`
- flashboot: `on`
- port: `8000`
- health port: `8000`

La environment variables:

- copiezi din `runpod.env.example`
- schimbi `APP_PUBLIC_BASE_URL` cu URL-ul final RunPod
- schimbi `DATABASE_URL` cu Postgres-ul tău
- schimbi `EMAIL_FROM` cu adresa verificată în Resend
- pui `RESEND_API_KEY`
- pui `SECRET_KEY`

## Verificare după deploy

1. health:

```bash
curl -s https://URLUL-TAU/api/v1/health
```

2. ping:

```bash
curl -i https://URLUL-TAU/ping
```

3. warmup:

```bash
curl -s -X POST https://URLUL-TAU/api/v1/tryon/warmup
curl -s https://URLUL-TAU/api/v1/tryon/warmup
```

4. signup:

- creezi cont
- verifici mailul
- faci login

## Notă importantă

Repo-ul acesta este pregătit pentru deploy, dar serverless VTON cu CatVTON tot va avea un timp de încălzire la primul request după scale-to-zero.

Warmup-ul din aplicație reduce problema, nu o elimină complet.
