# TryClothes Backend

Backend-ul din repo-ul ăsta este API-ul care ține în viață aplicația TryClothes: autentificare, profil user, upload-uri, virtual try-on, photo-to-video, health checks și integrarea cu FASHN.

În momentul ăsta, producția rulează pe un VPS Ubuntu, în spatele unui `nginx`, cu `systemd`, certificat SSL și bază de date PostgreSQL în Supabase.

## Ce este în repo

Stack-ul actual este simplu și destul de pragmatic:

- `FastAPI`
- `SQLAlchemy`
- `Alembic`
- `psycopg` v3
- `Pillow`
- `httpx`
- `FASHN API` pentru try-on și image-to-video
- `OpenAI` doar pentru prompturile Premium
- `Resend` sau `SMTP` pentru email

Repo-ul nu conține chei, fișiere de storage sau date de producție. Astea stau în `.env`, în baza de date și în directoarele locale de storage de pe server.

## Ce face backend-ul acum

### Autentificare și user

- login cu email + parolă
- register cu verificare pe email
- resend verification
- reset password
- Sign in with Apple pe backend
- profil user
- avatar upload / delete

### Try-on

Sunt două moduri de generare:

#### Standard

- model: `tryon-v1.6`
- rapid și mai ieftin
- fără OpenAI
- fără prompturi descriptive
- folosește mapping simplu de categorie pentru FASHN
- păstrează imaginea la calitate mare

#### Premium

- model: `tryon-max`
- configurat cu:
  - `resolution = 1k`
  - `generation_mode = balanced`
  - `num_images = 1`
- folosește OpenAI pentru a genera un prompt scurt, neutru și orientat pe reconstrucție
- culoarea nu mai este inventată din prompt
- imaginea hainei rămâne sursa de adevăr pentru culoare

### Photo to Video

- folosește endpoint-ul image-to-video din FASHN
- pornește de la rezultatul final al try-on-ului
- întoarce URL-ul video și metrici de procesare

## Ce există important în flow-ul de try-on

În backend, job-ul de try-on se creează rapid, iar generarea se execută în background. Asta înseamnă că aplicația primește imediat `job_id`, iar apoi face polling până când rezultatul este gata.

Pe scurt:

1. aplicația încarcă poza cu persoana
2. aplicația încarcă poza hainei
3. backend-ul validează și salvează fișierele
4. backend-ul creează job-ul
5. job-ul este executat în background
6. aplicația întreabă periodic status-ul job-ului
7. când FASHN termină, backend-ul descarcă / persistă rezultatul și îl servește aplicației

## Ce am optimizat deja

Backend-ul actual nu mai este în stadiul de MVP brut. Sunt deja făcute câteva optimizări care contează:

- job creation răspunde repede, iar execuția continuă în background
- Standard și Premium sunt rutate separat, clar
- imaginile pentru FASHN sunt păstrate la calitate mare
- pentru Premium, analiza OpenAI nu mai folosește inutil aceeași imagine mare care pleacă la FASHN
- prompturile Premium sunt cache-uite pe combinația:
  - hash imagine
  - categorie
  - culoare selectată de user, dacă există
- polling-ul către FASHN este mai agresiv decât la început
- răspunsul final de la FASHN nu mai este cerut ca base64 uriaș dacă un URL este suficient
- există debug logs destul de clare pentru payload, hash-uri și timpi

## Endpoint-uri utile

### Health

- `GET /api/v1/health`
- `GET /ping`

### Auth

- `POST /api/v1/auth/register`
- `POST /api/v1/auth/login`
- `POST /api/v1/auth/verify-email`
- `POST /api/v1/auth/resend-verification`
- `POST /api/v1/auth/forgot-password`
- `POST /api/v1/auth/reset-password`
- `POST /api/v1/auth/apple`

### User

- `GET /api/v1/me`
- `PUT /api/v1/me/body-profile`
- `PUT /api/v1/me/avatar`
- `DELETE /api/v1/me/avatar`

### Fit / stylist / brand data

- `GET /api/v1/brands`
- `GET /api/v1/categories`
- `POST /api/v1/fit/predict`
- `POST /api/v1/stylist/recommend`

### Try-on

- `POST /api/v1/tryon/jobs`
- `POST /api/v1/tryon/guest/jobs`
- `GET /api/v1/tryon/jobs/{job_id}`
- `GET /api/v1/tryon/jobs/{job_id}/result`
- `GET /api/v1/tryon/guest/jobs/{job_id}`
- `GET /api/v1/tryon/guest/jobs/{job_id}/result`
- `GET /api/v1/tryon/warmup`
- `POST /api/v1/tryon/warmup`
- `POST /api/v1/tryon/video`
- `POST /api/v1/tryon/guest/video`
- `POST /api/v1/tryon/video/jobs`
- `GET /api/v1/tryon/video/jobs/{job_id}`
- `POST /api/v1/tryon/guest/video/jobs`
- `GET /api/v1/tryon/guest/video/jobs/{job_id}`

## Structura repo-ului

Ce merită să știi când intri în repo:

- `app/api/routes/`
  - endpoint-urile FastAPI
- `app/services/`
  - logica reală de business
- `app/models/`
  - modelele SQLAlchemy
- `app/schemas/`
  - request / response schemas
- `alembic/`
  - migrațiile de DB
- `storage/`
  - fișiere locale generate în development sau pe server
- `start-server.sh`
  - script simplu de pornire care rulează și migrațiile

## Variabile de mediu importante

Poți porni local folosind `.env.example`, dar în practică cele mai importante variabile sunt astea:

```env
APP_ENV=production
DEBUG=false
APP_PUBLIC_BASE_URL=https://try-clothes.com

DATABASE_URL=postgresql+psycopg://...
SECRET_KEY=...

EMAIL_DELIVERY_MODE=resend
EMAIL_FROM=TryClothes <noreply@yourdomain.com>
EMAIL_FROM_NAME=TryClothes
RESEND_API_KEY=...

TRYON_PROVIDER=fashn_api
FASHN_API_KEY=...
FASHN_BASE_URL=https://api.fashn.ai/v1

OPENAI_API_KEY=...
APPLE_SIGN_IN_AUDIENCES=n.TryClothesMVP
```

Dacă folosești Supabase pooler / PgBouncer, nu pune parametri de prepared statements în `DATABASE_URL`. Fixul este deja în cod, în `create_engine(..., connect_args={"prepare_threshold": None})`.

## Cum pornești local

Varianta simplă:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
python3 -m alembic upgrade head
python3 -m uvicorn app.main:app --reload
```

Sau poți folosi scriptul:

```bash
./start-server.sh
```

Scriptul:

- pregătește cache-urile temporare
- rulează `alembic upgrade head`
- pornește `uvicorn`

## Deploy-ul care rulează acum

În momentul ăsta, backend-ul live este servit pe:

- [https://try-clothes.com](https://try-clothes.com)

Setup-ul de producție este:

- Ubuntu 24.04
- `systemd` pentru procesul backend
- `nginx` ca reverse proxy
- `Let's Encrypt` pentru SSL
- Supabase PostgreSQL

Flow-ul de deploy pe server este clasic:

1. `git pull`
2. activezi / verifici `.env`
3. `systemctl restart tryclothes-backend`
4. verifici:
   - `journalctl -u tryclothes-backend.service`
   - `https://try-clothes.com/api/v1/health`

## Ce să nu urci pe GitHub

Nu urca:

- `.env`
- `.venv`
- `storage/`
- fișiere generate local
- cache-uri
- chei API
- date brute de debug din producție

## Câteva note practice

- cheia `FASHN_API_KEY` stă doar pe backend
- cheia `OPENAI_API_KEY` stă doar pe backend
- aplicația iOS nu trebuie să vorbească direct nici cu FASHN, nici cu OpenAI
- pentru Premium, promptul este doar un ajutor de reconstrucție, nu sursa adevărului pentru culoare
- imaginea hainei rămâne sursa reală pentru culoare și textură
- `dress / rochie` este forțat intern pe Premium

## Dacă ceva se strică, verifică în ordinea asta

1. `GET /api/v1/health`
2. logurile `systemd`
3. `DATABASE_URL`
4. `FASHN_API_KEY`
5. `OPENAI_API_KEY`
6. migrațiile Alembic

De obicei, când apare o problemă în producție, una dintre astea e cauza reală.

## Licență

Repo-ul include un fișier `LICENSE`. Dacă proiectul ajunge public mai larg, merită revizuită și partea de licențiere împreună cu branding-ul și politicile aplicației.
