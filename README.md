# WebDeploy - AI-Powered Website Deployment Pipeline

Automated deployment platform that enables teams to deploy websites to **Google Cloud Platform** with **AI-powered code validation**. Upload a ZIP, pick a mode, and the pipeline handles everything: validation, build, infrastructure provisioning, upload, CDN, and email notifications.

---

## Architecture Overview

```
┌─────────────────────┐       WebSocket (real-time logs)        ┌──────────────────────┐
│                     │◄──────────────────────────────────────►│                      │
│   React Frontend    │         REST API (FastAPI)              │   Python Backend     │
│   Vite + Tailwind   │────────────────────────────────────────►│   8 Pipeline Steps   │
│                     │                                         │                      │
└─────────────────────┘                                         └──────────┬───────────┘
                                                                           │
                          ┌────────────────────────────────────────────────┤
                          │                    │                           │
                    ┌─────▼─────┐     ┌───────▼────────┐        ┌────────▼────────┐
                    │ Claude AI │     │  GCP Services   │        │   Gmail API     │
                    │ Sonnet 4.5│     │  GCS / LB / CDN │        │  Notifications  │
                    │           │     │  Cloud Run      │        │                 │
                    │ OpenRouter│     │  Cloud Build    │        └─────────────────┘
                    │ (fallback)│     │  Cloud DNS      │
                    └───────────┘     └─────────────────┘
```

---

## Deployment Modes

### 1. Demo Mode
Deploys to a **shared load balancer** under a common domain for quick testing.

| Aspect | Detail |
|--------|--------|
| **URL** | `https://digitaldatatest.com/{website_name}/` |
| **Infra** | Shared LB + shared IP (pre-existing) |
| **Creates** | GCS bucket, backend bucket (CDN), URL map path rule |
| **Use case** | Fast prototyping, testing, previews |
| **Cost** | Very low (shared infrastructure) |

### 2. Production Mode
Deploys a **fully isolated stack** on a custom domain with optional SSL and DNS.

| Aspect | Detail |
|--------|--------|
| **URL** | `https://{custom-domain}/` |
| **Infra** | Dedicated IP, LB, URL map, proxies, forwarding rules |
| **Creates** | Full stack: IP + bucket + CDN + URL map + SSL cert + DNS zone |
| **SSL** | Google-managed certificate (auto-renewed) |
| **DNS** | Optional auto-creation of Cloud DNS zone + A/CNAME records |
| **Use case** | Live production websites |

### 3. Cloud Run Mode
Deploys a **containerized application** (Node.js, Python, full-stack) to Cloud Run.

| Aspect | Detail |
|--------|--------|
| **URL** | `https://{service}-{hash}.run.app` |
| **Infra** | Artifact Registry image + Cloud Run service |
| **Creates** | Docker image (via Cloud Build), Cloud Run service |
| **Dockerfile** | Auto-generated based on project type |
| **Use case** | Dynamic apps, APIs, server-side rendering |

---

## The 8 Pipeline Steps

Every deployment runs through this pipeline sequentially. Logs are streamed in real-time via WebSocket.

| # | Step | What it does |
|---|------|-------------|
| 1 | **EXTRACT** | Extracts the ZIP, detects Vite project structure, locates `package.json` and `vite.config` |
| 2 | **AI_INSPECT** | Claude AI scans source code for deployment-breaking issues (hardcoded paths, missing base config, router issues) |
| 3 | **AI_FIX** | Claude auto-fixes critical issues (rewrites vite config base, fixes asset paths, adds router basename) |
| 4 | **BUILD** | Runs `npm install` + `npm run build` with the correct `VITE_BASE` for the deployment mode |
| 5 | **VERIFY** | Starts `vite preview` and verifies HTTP 200 (or builds Docker image for Cloud Run) |
| 6 | **INFRA** | Provisions all GCP infrastructure for the selected mode (buckets, LB, CDN, Cloud Run, etc.) |
| 7 | **UPLOAD** | Uploads built files to GCS (parallel, 10 concurrent) with correct Content-Type and Cache-Control headers, then invalidates CDN cache |
| 8 | **NOTIFY** | Sends HTML email notification (success or failure) with deployment URL and Claude AI summary |

> The NOTIFY step **always runs**, even after a failure, so the team is informed of what went wrong.

---

## AI Validation

### Claude AI (Primary)

**Model**: `claude-sonnet-4-5-20250929` via Anthropic API

Claude inspects the source code and checks for:

- **vite.config.js** — must have `base: process.env.VITE_BASE || '/'`
- **Asset references** — no hardcoded absolute paths (`/images/logo.png`), must use imports
- **index.html** — no hardcoded script/style/favicon paths
- **CSS url()** — no absolute `url(/...)` references
- **React Router** — must use `basename={import.meta.env.BASE_URL}` if react-router-dom is detected
- **package.json scripts** — build must be `vite build`

When issues are found, Claude returns exact string replacements that are applied automatically to the source files before the build step.

**Response format**: Strict JSON with `status`, `issues[]`, `fixes[]`, and `summary`.

**Retry logic**: 3 retries with exponential backoff (2s, 4s, 8s) for rate limits and server errors.

### OpenRouter Fallback

**Model**: `meta-llama/llama-3.1-8b-instruct:free` (Meta Llama 3.1 8B)

If Claude is unavailable, the same prompt is sent to OpenRouter using the free Llama model. If both fail, the pipeline continues without AI validation (graceful degradation).

---

## Tech Stack

### Backend

| Technology | Purpose |
|-----------|---------|
| **FastAPI** | Async web framework + REST API |
| **Uvicorn** | ASGI server |
| **SQLAlchemy** | ORM (SQLite database) |
| **Pydantic** | Request/response validation + settings |
| **WebSockets** | Real-time log streaming |
| **Anthropic SDK** | Claude AI API client |
| **google-cloud-storage** | GCS file uploads |
| **google-api-python-client** | Compute Engine, Cloud DNS, Gmail APIs |
| **google-auth** | Service account authentication |
| **Jinja2** | HTML email templates |
| **httpx** | Async HTTP client (OpenRouter fallback) |

### Frontend

| Technology | Purpose |
|-----------|---------|
| **React 18** | UI framework |
| **Vite 6** | Build tool + dev server |
| **TailwindCSS 4** | Utility-first CSS |
| **React Router 6** | Client-side routing |
| **Axios** | HTTP client |
| **Lucide React** | Icon library |

### Infrastructure (GCP)

| Service | Usage |
|---------|-------|
| **Cloud Storage** | Static file hosting |
| **Compute Engine** (LB) | Load balancer, URL maps, backend buckets, forwarding rules |
| **Cloud CDN** | Global caching with configurable TTLs |
| **Cloud Run** | Containerized app hosting |
| **Cloud Build** | Docker image builds |
| **Artifact Registry** | Docker image storage |
| **Cloud DNS** | DNS zone and record management |
| **Gmail API** | Email notifications (domain-wide delegation) |
| **Secret Manager** | Service account key storage |

---

## Dockerfile Auto-Generation (Cloud Run)

For Cloud Run deployments, the pipeline auto-detects the project type and generates an optimized Dockerfile:

| Detected Type | Strategy |
|--------------|----------|
| **Static Vite** | 2-stage: `node:20-alpine` build + `nginx:alpine` serve |
| **Node.js app** | `node:20-alpine` with `npm start` |
| **Python (FastAPI/Flask)** | `python:3.11-slim` with auto-detected entrypoint |
| **Full-stack (Python + JS)** | 2-stage: frontend build + Python backend with static files |
| **Static HTML** | `nginx:alpine` with SPA config |

All generated Dockerfiles expose port **8080** (Cloud Run requirement).

---

## Frontend Features

- **Drag & drop upload** — ZIP files up to 500 MB
- **Mode selector** — Demo / Production / Cloud Run with dynamic form fields
- **Real-time pipeline logs** — Dark terminal UI with color-coded log levels (WebSocket)
- **Visual step tracker** — 8-step progress bar with spinner, durations, and status icons
- **Deployment history** — Sortable/filterable table of all past deployments
- **Success/failure banners** — Clickable live URL on success, error details on failure

---

## Email Notifications

HTML-formatted emails sent via **Gmail API** with domain-wide delegation:

- **Success**: green header, live URL link, Claude AI summary
- **Failure**: red header, error details (monospace), failed step info
- **Always sent**: even if the deployment fails, the team gets notified
- **Graceful degradation**: if Gmail isn't configured, the pipeline continues without email

---

## Configuration

Key environment variables (see `backend/.env.example` for full list):

```env
# GCP
PROJECT_ID=your-project-id
GOOGLE_APPLICATION_CREDENTIALS=./service-account.json

# AI
ANTHROPIC_API_KEY=sk-ant-...
OPENROUTER_API_KEY=sk-or-...                          # optional fallback
OPENROUTER_MODEL=meta-llama/llama-3.1-8b-instruct:free

# Demo mode (pre-existing infra)
DEMO_DOMAIN=digitaldatatest.com
DEMO_URL_MAP_NAME=test-lb
DEMO_GLOBAL_IP_NAME=test-lb-ip

# Production mode toggles
PROD_AUTO_CREATE_DNS_ZONE=true
PROD_AUTO_CREATE_SSL_CERT=false

# Email
GMAIL_DELEGATED_USER=user@company.com
NOTIFICATION_TO_EMAILS=team@company.com

# Cloud Run
CLOUDRUN_REGION=europe-west1
CLOUDRUN_MEMORY=512Mi
CLOUDRUN_MAX_INSTANCES=10
```

---

## Running Locally

```bash
# Backend
cd backend
cp .env.example .env           # fill in your values
pip install -r requirements.txt
uvicorn main:app --reload --port 8000

# Frontend
cd frontend
npm install
npm run dev
```

### With Docker Compose

```bash
docker-compose up --build
```

---

## Deploying to Cloud Run

```bash
# Build and push the backend image
gcloud builds submit --config=cloudbuild-backend.yaml .

# Deploy to Cloud Run
gcloud run deploy webdeploy-backend \
  --image=europe-west1-docker.pkg.dev/PROJECT_ID/cloud-run-images/webdeploy-backend:latest \
  --region=europe-west1 \
  --update-secrets=/app/secrets/service-account.json=webdeploy-sa-key:latest \
  --set-env-vars=GOOGLE_APPLICATION_CREDENTIALS=/app/secrets/service-account.json
```

---

## Project Structure

```
├── backend/
│   ├── api/routes/          # REST endpoints (deploy, history, health, websocket)
│   ├── db/                  # SQLAlchemy models + CRUD
│   ├── infra/               # GCP deployers (demo, prod, cloudrun) + helpers
│   ├── services/            # Pipeline services
│   │   ├── pipeline_orchestrator.py   # Main orchestrator (8 steps)
│   │   ├── claude_validator.py        # AI inspection + auto-fix
│   │   ├── build_service.py           # npm install/build + vite preview
│   │   ├── upload_service.py          # GCS parallel upload + CDN invalidation
│   │   ├── email_service.py           # Gmail API notifications
│   │   ├── zip_processor.py           # ZIP extraction + Vite detection
│   │   ├── dockerfile_generator.py    # Auto Dockerfile for Cloud Run
│   │   └── cloud_build_service.py     # Cloud Build API
│   ├── config.py            # All settings (env vars)
│   └── main.py              # FastAPI app entrypoint
├── frontend/
│   └── src/
│       ├── components/      # UploadZone, DeploymentForm, PipelineLogs, etc.
│       ├── hooks/           # useDeployment, useWebSocket
│       ├── pages/           # DeploymentDetail
│       └── services/        # API client (axios)
├── Dockerfile.backend.prod  # Production backend image
├── docker-compose.yml       # Local development
├── cloudbuild-backend.yaml  # Cloud Build config
└── README.md
```
