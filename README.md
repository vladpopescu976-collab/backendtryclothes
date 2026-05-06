# TryClothes RunPod Backend

Creat de Vlad Popescu.

Acest folder este backend-ul public pentru `TryClothes`, pregătit în primul rând pentru integrarea cu `FASHN AI`.

Acum repo-ul este optimizat pentru:

- FastAPI
- login / create account / email verification
- stylist AI light
- fit + brands
- virtual try-on prin `FASHN API`
- deploy simplu pe RunPod Serverless sau pe orice host CPU

## Ce pui pe GitHub

Urcă exact conținutul acestui folder:

`/Users/vladpopescu/Documents/New project/tryclothes-runpod-backend`

Nu urca:

- `.env`
- `.venv`
- `storage/`
- baze de date locale
- imagini generate
- cache-uri locale
- chei API

## Ce face backend-ul

- `POST /api/v1/auth/register`
- `POST /api/v1/auth/login`
- `POST /api/v1/auth/verify-email`
- `POST /api/v1/auth/resend-verification`
- `POST /api/v1/stylist/recommend`
- `POST /api/v1/fit/predict`
- `POST /api/v1/tryon/jobs`
- `POST /api/v1/tryon/guest/jobs`
- `GET /api/v1/tryon/jobs/{job_id}`
- `GET /api/v1/tryon/jobs/{job_id}/result`
- `GET /api/v1/health`
- `GET /ping`

## Flow-ul corect pentru aplicație

1. aplicația face login sau guest login
2. userul încarcă poza cu el
3. userul încarcă poza cu haina
4. aplicația trimite fișierele la backend
5. backend-ul vorbește cu `FASHN AI`
6. backend-ul salvează job-ul și rezultatul
7. aplicația cere rezultatul final și îl afișează

Important:

- cheia `FASHN_API_KEY` stă doar pe backend
- aplicația iOS nu trebuie să vorbească direct cu FASHN

## Variabile obligatorii

Pentru FASHN-first deploy, folosește aceste valori:

```env
APP_ENV=production
DEBUG=false
PROJECT_NAME=TryClothes Backend
API_V1_PREFIX=/api/v1

SECRET_KEY=replace-with-a-long-random-secret
APP_PUBLIC_BASE_URL=https://your-runpod-endpoint-url
DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST:5432/DATABASE

EMAIL_DELIVERY_MODE=resend
EMAIL_FROM=TryClothes <noreply@yourdomain.com>
EMAIL_FROM_NAME=TryClothes
RESEND_API_KEY=replace-with-your-resend-api-key

TRYON_PROVIDER=fashn_api
FASHN_API_KEY=replace-with-your-fashn-api-key
FASHN_BASE_URL=https://api.fashn.ai/v1
FASHN_MODEL_NAME=tryon-max
FASHN_GARMENT_PHOTO_TYPE=flat-lay
FASHN_OUTPUT_FORMAT=png
FASHN_SEGMENTATION_FREE=true
FASHN_MODERATION_LEVEL=permissive
FASHN_RETURN_BASE64=true

PORT=8000
PORT_HEALTH=8000
```

Vezi și:

- `.env.example`
- `runpod.env.example`

## Ce pui efectiv pe server

Ai nevoie doar de:

- acest repo
- o bază de date externă PostgreSQL
- `RESEND_API_KEY`
- `FASHN_API_KEY`
- domeniul/email-ul verificat în Resend

Nu ai nevoie de:

- GPU pentru try-on
- CatVTON
- weights locale
- Hugging Face cache pentru MVP-ul cu FASHN

## Docker

Repo-ul are acum:

- `Dockerfile` -> varianta recomandată pentru `FASHN AI`
- `Dockerfile.catvton` -> păstrat pentru viitor, dacă revii la CatVTON

Pentru RunPod + FASHN folosește:

- `Dockerfile`

## Deploy pe RunPod Serverless

Recomandat pentru MVP cu FASHN:

1. `Serverless`
2. `Create new deployment`
3. `Custom deployment`
4. `Deploy from GitHub`
5. alegi repo-ul backend

Setări recomandate:

- endpoint type: `Load Balancer`
- worker type: `CPU` dacă este disponibil
- dacă nu ai CPU în acel flux, alege cel mai ieftin worker disponibil
- active workers: `0`
- max workers: `1`
- port: `8000`
- health port: `8000`

La environment variables:

- copiezi din `runpod.env.example`
- schimbi `APP_PUBLIC_BASE_URL` cu URL-ul final
- schimbi `DATABASE_URL`
- pui `RESEND_API_KEY`
- pui `FASHN_API_KEY`
- pui `SECRET_KEY`

## Ce verifici după deploy

### 1. Health

```bash
curl -s https://URLUL-TAU/api/v1/health
```

Ar trebui să vezi ceva de genul:

```json
{
  "status": "ok",
  "tryon_provider": "fashn_api",
  "tryon_ready": true,
  "email_ready": true
}
```

### 2. Ping

```bash
curl -i https://URLUL-TAU/ping
```

Trebuie să răspundă `200`.

### 3. Signup

- creezi cont
- primești email
- verifici contul
- faci login

### 4. Try-on

- trimiți `person_image`
- trimiți `upper_garment_image`
- optional `lower_garment_image`
- backend-ul creează job-ul și întoarce rezultatul

## Endpoint-ul important pentru aplicație

Din iOS, fluxul principal lovește:

- `POST /api/v1/auth/register`
- `POST /api/v1/auth/login`
- `GET /api/v1/me`
- `PUT /api/v1/me/body-profile`
- `POST /api/v1/stylist/recommend`
- `POST /api/v1/fit/predict`
- `POST /api/v1/tryon/jobs`
- `GET /api/v1/tryon/jobs/{job_id}/result`

## Notă despre warmup

Pentru `FASHN AI`, `warmup` nu mai este necesar ca la CatVTON.

Dar endpoint-urile:

- `POST /api/v1/tryon/warmup`
- `GET /api/v1/tryon/warmup`

au fost păstrate și întorc `ready` pentru providerul `fashn_api`, ca aplicația să nu se rupă dacă deja folosește acel flow.

## Ce mai rămâne pentru aplicația finală

După deploy, mai ai de făcut:

1. aplicația iOS să bată doar spre URL-ul backend-ului tău
2. să scoatem din UI orice text de debug / server / MVP
3. să salvăm istoricul probelor per user
4. să legăm subscriptions
5. să rafinăm UX-ul pentru timpii de așteptare și stările de rezultat
