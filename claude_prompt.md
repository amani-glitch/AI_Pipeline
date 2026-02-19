# WebDeploy Platform — Full Implementation Specification

> **Target**: Claude Code Opus 4.6 with Agents Team Mode
> **Stack**: FastAPI (Python) backend + React/Vite frontend
> **Mission**: Build a complete internal platform that receives ZIP files from a non-technical team, validates/fixes the code using Claude AI, provisions GCP infrastructure, deploys static websites, and sends email notifications.

---

## 1. BUSINESS CONTEXT

We have two teams:
- **Functional Team** (non-IT): Builds static showcase websites using AI tools (Manus, Bolt, etc.) following a system prompt. They deliver a ZIP containing the Vite source code AND the `dist/` folder.
- **Engineering Team** (us): Currently receives ZIPs manually, validates them, fixes path issues, provisions GCP infrastructure, and deploys. This platform automates the entire workflow.

**Deployment model:**
- **Demo mode**: Website is deployed under a subpath (e.g., `https://digitaldatatest.com/my-website/`) on a shared demo domain using an existing load balancer.
- **Production mode**: Website is deployed under root `/` on its own dedicated domain (e.g., `https://client-website.com/`) with its own load balancer infrastructure.

---

## 2. ARCHITECTURE OVERVIEW

```
┌──────────────────────────────────────────────────────────────────────┐
│                    FRONTEND  (React + Vite + TailwindCSS)            │
│                                                                      │
│  ┌─────────┐   ┌──────────┐   ┌──────────┐   ┌───────────────────┐  │
│  │ Upload  │──▶│ Mode     │──▶│ Live     │──▶│ Deployment Status │  │
│  │ ZIP     │   │ Selector │   │ Pipeline │   │ + History         │  │
│  │ (D&D)   │   │ Demo/Prod│   │ Logs     │   │ + Email Notif     │  │
│  └─────────┘   └──────────┘   └──────────┘   └───────────────────┘  │
└───────────────────────────┬──────────────────────────────────────────┘
                            │  REST + WebSocket (SSE for logs)
┌───────────────────────────▼──────────────────────────────────────────┐
│                     BACKEND  (FastAPI + Python 3.11+)                │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │                    Pipeline Orchestrator                        │ │
│  │  Step 1: Extract & Validate ZIP structure                      │ │
│  │  Step 2: Claude AI Code Inspection & Auto-Fix                  │ │
│  │  Step 3: Build & Preview Verification (npm + Vite)             │ │
│  │  Step 4: GCP Infrastructure Provisioning                       │ │
│  │  Step 5: Upload dist/ to Cloud Storage                         │ │
│  │  Step 6: Send Email Notification (success/failure + link)      │ │
│  └─────────────────────────────────────────────────────────────────┘ │
│                                                                      │
│  Services:                                                           │
│  ├── ZipProcessingService                                            │
│  ├── ClaudeValidationService  (Anthropic API)                        │
│  ├── BuildService             (subprocess: npm install/build/preview)│
│  ├── InfraService             (GCP SDK — adapted from provided code) │
│  ├── UploadService            (Cloud Storage upload)                 │
│  ├── EmailService             (SMTP / SendGrid / GCP)                │
│  └── DeploymentHistoryService (SQLite or PostgreSQL)                 │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 3. PROJECT STRUCTURE

```
webdeploy/
├── backend/
│   ├── main.py                          # FastAPI app entry point
│   ├── config.py                        # All configuration (env vars, GCP settings)
│   ├── requirements.txt
│   ├── .env.example
│   │
│   ├── api/
│   │   ├── __init__.py
│   │   ├── routes/
│   │   │   ├── __init__.py
│   │   │   ├── deployments.py           # POST /deploy, GET /deployments, GET /deployments/{id}
│   │   │   ├── health.py                # GET /health
│   │   │   └── websocket.py             # WS /ws/logs/{deployment_id}
│   │   └── dependencies.py
│   │
│   ├── services/
│   │   ├── __init__.py
│   │   ├── zip_processor.py             # Extract, validate ZIP structure
│   │   ├── claude_validator.py          # Claude API inspection + auto-fix
│   │   ├── build_service.py             # npm install, vite build, vite preview
│   │   ├── infra_service.py             # GCP infra provisioning (refactored from provided scripts)
│   │   ├── upload_service.py            # Upload dist/ to Cloud Storage bucket
│   │   ├── email_service.py             # Send deployment notification emails
│   │   └── pipeline_orchestrator.py     # Orchestrates the full pipeline
│   │
│   ├── models/
│   │   ├── __init__.py
│   │   ├── deployment.py                # Pydantic models + DB models
│   │   └── enums.py                     # DeploymentMode, PipelineStep, Status enums
│   │
│   ├── infra/
│   │   ├── __init__.py
│   │   ├── gcp_helpers.py               # safe_name, naming conventions, wait helpers
│   │   ├── demo_deployer.py             # Demo mode: bucket + backend bucket + URL map path rules
│   │   └── prod_deployer.py             # Prod mode: full infra (IP, bucket, backend, SSL, DNS, LB)
│   │
│   └── db/
│       ├── __init__.py
│       ├── database.py                  # SQLAlchemy / SQLite setup
│       └── crud.py                      # CRUD operations for deployment records
│
├── frontend/
│   ├── package.json
│   ├── vite.config.js
│   ├── tailwind.config.js
│   ├── src/
│   │   ├── main.jsx
│   │   ├── App.jsx
│   │   ├── components/
│   │   │   ├── UploadZone.jsx           # Drag & drop upload with validation
│   │   │   ├── DeploymentForm.jsx       # Mode selector, website name, domain, email
│   │   │   ├── PipelineLogs.jsx         # Real-time log viewer (WebSocket)
│   │   │   ├── DeploymentStatus.jsx     # Status badge + result links
│   │   │   ├── DeploymentHistory.jsx    # Table of past deployments
│   │   │   └── Layout.jsx              # App shell, nav, sidebar
│   │   ├── hooks/
│   │   │   ├── useDeployment.js
│   │   │   └── useWebSocket.js
│   │   ├── services/
│   │   │   └── api.js                   # Axios/fetch wrapper
│   │   └── styles/
│   │       └── globals.css
│   └── index.html
│
├── docker-compose.yml                   # Backend + Frontend containers
├── Dockerfile.backend
├── Dockerfile.frontend
└── README.md
```

---

## 4. DETAILED SERVICE SPECIFICATIONS

### 4.1 ZipProcessingService (`zip_processor.py`)

**Purpose**: Extract the uploaded ZIP and identify the project structure.

**Logic**:
```
1. Save uploaded file to temp directory
2. Extract ZIP
3. Detect structure:
   - Find the root of the Vite project (look for package.json with "vite" dependency)
   - Find dist/ folder (look for index.html inside)
   - Find vite.config.js or vite.config.ts
4. Validate:
   - package.json exists and has "vite" in devDependencies or dependencies
   - vite.config exists
   - dist/ folder exists with index.html
5. Return structured result:
   {
     "source_path": "/tmp/.../project-root/",
     "dist_path": "/tmp/.../project-root/dist/",
     "vite_config_path": "/tmp/.../project-root/vite.config.js",
     "package_json": { ...parsed content... },
     "has_router": true/false (check for react-router-dom in dependencies),
     "detected_issues": []
   }
```

**Edge cases to handle**:
- ZIP contains a single wrapper folder (e.g., `my-site/package.json` instead of `package.json` at root) — unwrap it
- Multiple `dist/` folders — use the one at the Vite project root level
- Missing `dist/` — flag error, the functional team must include a build

---

### 4.2 ClaudeValidationService (`claude_validator.py`)

**Purpose**: Use Claude API (claude-sonnet-4-5-20250929) to inspect the source code, detect deployment-breaking issues, and auto-fix them.

**CRITICAL — What Claude must inspect and fix**:

```
INSPECTION CHECKLIST:
━━━━━━━━━━━━━━━━━━━━

1. vite.config.js / vite.config.ts:
   - MUST contain: base: process.env.VITE_BASE || '/'
   - If missing or hardcoded, FIX IT

2. Router configuration (if react-router-dom is used):
   - MUST use: <Router basename={import.meta.env.BASE_URL}>
   - or <BrowserRouter basename={import.meta.env.BASE_URL}>
   - If hardcoded basename or missing, FIX IT

3. Asset references in ALL source files (.jsx, .tsx, .js, .ts, .css, .html):
   - NO hardcoded absolute paths like src="/images/..." or url('/images/...')
   - Public folder assets MUST use the asset() helper pattern:
     const asset = (p) => `${import.meta.env.BASE_URL}${p}`;
   - Or use import from src/assets/ (preferred)
   - FIX any hardcoded absolute asset paths

4. index.html:
   - No hardcoded absolute paths for scripts, styles, favicon
   - Let Vite handle prefixing via its build process
   - FIX if found

5. CSS files:
   - No url('/images/...') or url('/fonts/...')  
   - Should use relative paths: url('./images/...') or url('../assets/...')
   - FIX if found

6. Navigation links:
   - Internal links should use React Router <Link to="..."> not <a href="/...">
   - If <a href="/something"> is used for internal nav, flag it

7. package.json scripts:
   - "build" script should be: "vite build" (not custom scripts that ignore VITE_BASE)
   - "preview" script should be: "vite preview"
   - FIX if needed
```

**Implementation approach**:

```python
# claude_validator.py

import anthropic
import os
from pathlib import Path

class ClaudeValidationService:
    def __init__(self):
        self.client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        self.model = "claude-sonnet-4-5-20250929"
    
    async def validate_and_fix(self, source_path: str, mode: str, website_name: str) -> dict:
        """
        Send all relevant source files to Claude for inspection.
        mode: "demo" or "prod"
        website_name: used for demo subpath (e.g., "my-website" → base: /my-website/)
        """
        # 1. Collect all relevant files (limit to source code, not node_modules or dist)
        files_content = self._collect_source_files(source_path)
        
        # 2. Build the inspection prompt
        base_path = f"/{website_name}/" if mode == "demo" else "/"
        prompt = self._build_inspection_prompt(files_content, mode, base_path)
        
        # 3. Call Claude API
        response = self.client.messages.create(
            model=self.model,
            max_tokens=8000,
            messages=[{"role": "user", "content": prompt}]
        )
        
        # 4. Parse Claude's response (expect JSON with fixes)
        result = self._parse_response(response)
        
        # 5. Apply fixes to files on disk
        if result["fixes"]:
            self._apply_fixes(source_path, result["fixes"])
        
        return result
    
    def _collect_source_files(self, source_path: str) -> dict:
        """Read all .js, .jsx, .ts, .tsx, .css, .html, vite.config.*, package.json files."""
        # EXCLUDE: node_modules/, dist/, .git/, build/
        # INCLUDE: src/**, public/**, vite.config.*, package.json, index.html
        ...
    
    def _build_inspection_prompt(self, files: dict, mode: str, base_path: str) -> str:
        return f"""You are a senior frontend engineer. Inspect this Vite + React project for deployment compatibility.

DEPLOYMENT MODE: {mode}
TARGET BASE PATH: {base_path}

The website will be built with: VITE_BASE={base_path} vite build
It must work correctly when served at {base_path}

INSPECTION RULES — CHECK EVERY FILE:

1. vite.config.js/ts MUST have: base: process.env.VITE_BASE || '/'
2. Router MUST use: basename={{import.meta.env.BASE_URL}}
3. ALL asset references in JSX/TSX/CSS must NOT use hardcoded absolute paths like "/images/..."
   - Use: import from 'src/assets/...' OR the helper: const asset = (p) => `${{import.meta.env.BASE_URL}}${{p}}`;
4. index.html must not have hardcoded absolute paths
5. CSS url() must use relative paths, not absolute
6. package.json "build" must be "vite build" and "preview" must be "vite preview"

FILES TO INSPECT:
{self._format_files(files)}

RESPOND IN STRICT JSON FORMAT (no markdown, no backticks):
{{
  "status": "pass" | "needs_fixes",
  "issues_found": [
    {{
      "file": "relative/path/to/file",
      "line_description": "description of the problematic line or pattern",
      "issue": "what is wrong",
      "severity": "critical" | "warning"
    }}
  ],
  "fixes": [
    {{
      "file": "relative/path/to/file",
      "action": "replace_file",
      "new_content": "...full corrected file content..."
    }}
  ],
  "summary": "Brief human-readable summary of what was found and fixed"
}}

IMPORTANT: 
- If you fix a file, provide the COMPLETE new file content, not patches.
- Only fix critical issues that would break deployment. Don't refactor or change styling.
- If everything is correct, return status "pass" with empty fixes array.
"""
```

**After Claude fixes the source, the service must**:
1. Write fixed files back to disk
2. Rebuild: run `VITE_BASE={base_path} npm run build` in subprocess
3. Return the new dist/ path

---

### 4.3 BuildService (`build_service.py`)

**Purpose**: Run npm install, build with correct VITE_BASE, and verify with preview.

```python
class BuildService:
    async def install_dependencies(self, source_path: str) -> bool:
        """Run npm install in the source directory."""
        # subprocess.run(["npm", "install"], cwd=source_path, ...)
    
    async def build(self, source_path: str, mode: str, website_name: str) -> str:
        """
        Build with correct VITE_BASE.
        Demo:  VITE_BASE=/website-name/ npm run build
        Prod:  VITE_BASE=/ npm run build
        Returns path to dist/ folder.
        """
        base_path = f"/{website_name}/" if mode == "demo" else "/"
        env = {**os.environ, "VITE_BASE": base_path}
        # subprocess.run(["npm", "run", "build"], cwd=source_path, env=env, ...)
        return os.path.join(source_path, "dist")
    
    async def verify_preview(self, source_path: str, mode: str, website_name: str) -> dict:
        """
        Start vite preview, hit the expected URL, verify 200 status.
        Demo:  expect http://localhost:4173/website-name/ → 200
        Prod:  expect http://localhost:4173/ → 200
        Returns {"success": bool, "url_tested": str, "status_code": int}
        """
        # 1. Start "npx vite preview" as subprocess
        # 2. Wait for server to be ready (poll with httpx/aiohttp)
        # 3. Hit the URL
        # 4. Check status code and that HTML contains expected content
        # 5. Kill subprocess
        # 6. Return result
```

**IMPORTANT**: Use a random port for preview to avoid conflicts when multiple deployments run in parallel. Pass `--port {random_port}` to vite preview.

---

### 4.4 InfraService (`infra_service.py`, `demo_deployer.py`, `prod_deployer.py`)

**Purpose**: Provision GCP infrastructure. This code is ADAPTED from the two Python scripts provided in the requirements document.

**CRITICAL INSTRUCTIONS FOR REFACTORING THE PROVIDED INFRASTRUCTURE CODE**:

1. **Do NOT copy the scripts as-is.** Refactor them into clean, async-compatible service classes.
2. **Remove all global variables.** All configuration comes from `config.py` and is passed via constructor/method params.
3. **Remove all `print()` calls.** Use proper Python logging that also feeds the WebSocket log stream.
4. **Make it idempotent.** Every function must check if resource exists before creating.
5. **Return structured results**, not just log messages.

**GCP Configuration (from config.py, populated by .env)**:

```python
# config.py
from pydantic_settings import BaseSettings

class GCPConfig(BaseSettings):
    PROJECT_ID: str = "adp-413110"
    
    # Demo infrastructure (EXISTING — do not create these)
    DEMO_DOMAIN: str = "digitaldatatest.com"
    DEMO_URL_MAP_NAME: str = "test-lb"           # Existing LB URL map
    DEMO_GLOBAL_IP_NAME: str = "test-lb-ip"      # Existing global IP
    
    # Prod infrastructure settings
    PROD_AUTO_REGISTER_DOMAINS: bool = False
    PROD_AUTO_CREATE_DNS_ZONE: bool = True
    PROD_AUTO_CREATE_SSL_CERT: bool = False
    
    # Bucket settings
    BUCKET_LOCATION: str = "US"
    BUCKET_CORS_MAX_AGE: int = 3600
    
    # CDN settings
    CDN_DEFAULT_TTL: int = 3600
    CDN_MAX_TTL: int = 86400
    CDN_CLIENT_TTL: int = 3600
    CDN_NEGATIVE_CACHING: bool = True
    CDN_NEGATIVE_CACHING_TTL: int = 120
    
    # Service account
    GOOGLE_APPLICATION_CREDENTIALS: str = ""
    
    # Email
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    NOTIFICATION_FROM_EMAIL: str = "webdeploy@bestoftours.co.uk"
    NOTIFICATION_TO_EMAILS: str = ""  # comma-separated
    
    # Claude
    ANTHROPIC_API_KEY: str = ""
    
    class Config:
        env_file = ".env"
```

**Demo Deployer** (`demo_deployer.py`) — Adapted from the provided demo script:

```
WHAT IT DOES (for a website named "my-website"):
1. Create storage bucket: "demo-my-website-bucket-demo"
2. Configure bucket: uniform access, CORS for DEMO_DOMAIN, public read, versioning
3. Create backend bucket: "demo-my-website-backend-demo" linked to storage bucket, CDN enabled
4. Add path rule "/{my-website}/*" to EXISTING URL map (DEMO_URL_MAP_NAME)
   - Find the path matcher for DEMO_DOMAIN
   - Append new pathRule without touching existing rules

WHAT IT DOES NOT DO:
- Does NOT create global IP, SSL, proxies, forwarding rules, DNS
- These already exist for the demo domain

RESULT URL: https://{DEMO_DOMAIN}/{website_name}/
```

**Prod Deployer** (`prod_deployer.py`) — Adapted from the provided prod script:

```
WHAT IT DOES (for domain "client-website.com"):
1. Ensure global IP address
2. Create storage bucket: "client-website-com-bucket-prod"
3. Configure bucket: uniform access, CORS, public read, versioning
4. Create backend bucket with CDN
5. Create managed SSL certificate (if AUTO_CREATE_SSL_CERT)
6. Create/update URL map with host rules for the domain
7. Create/update target proxies (HTTP + HTTPS if SSL)
8. Create forwarding rules (port 80 + 443 if SSL)
9. Create DNS zone and A/CNAME records (if AUTO_CREATE_DNS_ZONE)

RESULT URL: https://{domain}/
```

**KEY REFACTORING RULES for the provided infrastructure code**:

```python
# PATTERN TO FOLLOW for every GCP resource function:

class DemoDeployer:
    def __init__(self, config: GCPConfig, log_callback: Callable):
        self.config = config
        self.log = log_callback  # Sends to WebSocket + Python logger
        self.creds = self._get_credentials()
        self.compute = build("compute", "v1", credentials=self.creds, cache_discovery=False)
        self.storage_api = build("storage", "v1", credentials=self.creds, cache_discovery=False)
        self.storage_client = storage.Client(project=config.PROJECT_ID, credentials=self.creds)
    
    async def deploy(self, website_name: str) -> DeploymentResult:
        """Full demo deployment pipeline. Returns structured result."""
        result = DeploymentResult(mode="demo", website_name=website_name)
        
        try:
            # Step 1: Storage bucket
            self.log(f"Creating storage bucket for {website_name}...")
            bucket_name = await self._ensure_bucket(website_name)
            result.storage_bucket = bucket_name
            
            # Step 2: Backend bucket
            self.log(f"Creating backend bucket for {website_name}...")
            bb_name = await self._ensure_backend_bucket(website_name)
            result.backend_bucket = bb_name
            
            # Step 3: URL map path rule
            self.log(f"Adding path rule to load balancer...")
            await self._add_to_url_map(website_name)
            result.url_map_updated = True
            
            result.success = True
            result.url = f"https://{self.config.DEMO_DOMAIN}/{website_name}/"
            
        except Exception as e:
            result.success = False
            result.error = str(e)
            self.log(f"ERROR: {e}")
        
        return result
```

---

### 4.5 UploadService (`upload_service.py`)

**Purpose**: Upload the validated dist/ contents to the correct Cloud Storage bucket.

```
DEMO MODE:
  - Bucket: "demo-{website_name}-bucket-demo"
  - Upload dist/* to: gs://bucket/{website_name}/
  - Example: dist/index.html → gs://bucket/my-website/index.html
  - Example: dist/assets/style.css → gs://bucket/my-website/assets/style.css

PROD MODE:
  - Bucket: "{domain-safe}-bucket-prod"
  - Upload dist/* to: gs://bucket/  (root)
  - Example: dist/index.html → gs://bucket/index.html
```

**Must handle**:
- Set correct Content-Type for each file (text/html, text/css, application/javascript, image/*, etc.)
- Set Cache-Control headers appropriately
- Upload in parallel for speed (use concurrent.futures or asyncio)
- Report progress (X of Y files uploaded) via log callback

---

### 4.6 EmailService (`email_service.py`)

**Purpose**: Send deployment status notification email after pipeline completes.

**Email content must include**:
- Website name
- Deployment mode (Demo / Production)
- Status: SUCCESS or FAILED
- If success: the live URL (clickable link)
- If failed: the step that failed + error message
- Timestamp
- Summary of what Claude AI found and fixed (if any)

**Email template** (use HTML email):

```
Subject: [WebDeploy] ✅ {website_name} deployed successfully — {mode} mode
   or:  [WebDeploy] ❌ {website_name} deployment failed — {mode} mode

Body:
  - Website: {website_name}
  - Mode: Demo / Production
  - Status: ✅ SUCCESS / ❌ FAILED
  - URL: https://... (if success)
  - AI Validation: {summary of what Claude found/fixed}
  - Failed Step: {step name} (if failed)
  - Error: {error message} (if failed)
  - Deployed at: {timestamp}
```

---

### 4.7 PipelineOrchestrator (`pipeline_orchestrator.py`)

**Purpose**: Orchestrates the full deployment pipeline, step by step, with logging and error handling.

```python
class PipelineOrchestrator:
    """
    PIPELINE STEPS (executed sequentially):
    
    1. EXTRACT       — Extract and validate ZIP structure
    2. AI_INSPECT    — Claude inspects code for deployment issues  
    3. AI_FIX        — Claude applies fixes (if any issues found)
    4. BUILD         — npm install + VITE_BASE=... vite build
    5. VERIFY        — vite preview + HTTP health check
    6. INFRA         — Provision GCP infrastructure (demo or prod)
    7. UPLOAD        — Upload dist/ to Cloud Storage
    8. NOTIFY        — Send email notification
    
    Each step logs to WebSocket in real-time.
    If any step fails, pipeline stops and sends failure notification.
    """
    
    async def run(self, deployment_id: str, zip_path: str, config: DeploymentConfig):
        """
        config contains:
          - mode: "demo" | "prod"
          - website_name: str (slug, e.g., "my-portfolio")
          - domain: str (only for prod mode, e.g., "client-site.com")
          - notification_emails: list[str]
        """
        steps = [
            ("EXTRACT", self._step_extract),
            ("AI_INSPECT", self._step_ai_inspect),
            ("AI_FIX", self._step_ai_fix),
            ("BUILD", self._step_build),
            ("VERIFY", self._step_verify),
            ("INFRA", self._step_infra),
            ("UPLOAD", self._step_upload),
            ("NOTIFY", self._step_notify),
        ]
        
        context = PipelineContext(deployment_id=deployment_id, zip_path=zip_path, config=config)
        
        for step_name, step_fn in steps:
            try:
                self._update_status(deployment_id, step_name, "running")
                self._log(deployment_id, f"▶ Starting step: {step_name}")
                await step_fn(context)
                self._update_status(deployment_id, step_name, "completed")
                self._log(deployment_id, f"✓ Completed: {step_name}")
            except Exception as e:
                self._update_status(deployment_id, step_name, "failed", error=str(e))
                self._log(deployment_id, f"✗ Failed at {step_name}: {e}")
                # Send failure email
                await self._step_notify(context, failed_step=step_name, error=str(e))
                raise
```

---

## 5. API ENDPOINTS

```
POST   /api/deploy
  - Multipart form: zip_file, mode (demo|prod), website_name, domain?, notification_emails
  - Returns: { deployment_id, status: "queued" }
  - Starts pipeline in background task (asyncio / BackgroundTasks)

GET    /api/deployments
  - Returns list of all deployments with status, mode, timestamps

GET    /api/deployments/{deployment_id}
  - Returns full deployment detail: status per step, logs, result URL, Claude findings

GET    /api/deployments/{deployment_id}/logs
  - Returns full log text for a deployment

WS     /ws/logs/{deployment_id}
  - WebSocket endpoint for real-time log streaming during pipeline execution

GET    /api/health
  - Returns system health + GCP connectivity status
```

---

## 6. FRONTEND SPECIFICATIONS

### 6.1 Pages

**Main page — Deploy** (`/`):
- Hero section with platform name "WebDeploy" and brief description
- Large drag-and-drop zone for ZIP upload (accepts .zip only, max 500MB)
- After upload, show a form:
  - Mode selector: "Demo" / "Production" (radio buttons or toggle, prominent)
  - Website name (text input, auto-slugified, required)
  - Domain (text input, only shown in Production mode, e.g., "client-site.com")
  - Notification emails (text input, comma-separated, pre-filled from config)
  - "Deploy" button (primary, large)
- After clicking Deploy:
  - Redirect to or show the Pipeline view

**Pipeline View** (`/deployments/{id}`):
- Stepper/timeline showing all 8 pipeline steps
- Each step shows: name, status (pending/running/completed/failed), duration
- Running step has a spinner animation
- Below the stepper: live log terminal (dark background, monospace font, auto-scrolls)
- On completion:
  - SUCCESS: Show green banner with the live URL (clickable), confetti animation optional
  - FAILED: Show red banner with the failed step and error message

**History page** (`/history`):
- Table with columns: Website Name, Mode, Status, URL, Date, Actions (view logs)
- Sortable, filterable
- Click a row to see full deployment details

### 6.2 Design Guidelines
- Clean, modern, professional UI
- Use TailwindCSS
- Color scheme: blue primary (#2563EB), green for success, red for failure
- Responsive (works on laptop screens, no need for mobile)
- The log terminal should look like a real terminal (dark bg, green/white text)

---

## 7. DATABASE SCHEMA

Use SQLite for simplicity (single file, no external DB needed). Use SQLAlchemy.

```sql
CREATE TABLE deployments (
    id TEXT PRIMARY KEY,              -- UUID
    website_name TEXT NOT NULL,
    mode TEXT NOT NULL,                -- "demo" or "prod"
    domain TEXT,                       -- only for prod mode
    status TEXT NOT NULL DEFAULT 'queued',  -- queued, running, success, failed
    current_step TEXT,
    
    -- Step statuses (JSON string)
    steps_status TEXT,                 -- {"EXTRACT": "completed", "AI_INSPECT": "running", ...}
    
    -- Results
    result_url TEXT,                   -- final deployment URL
    claude_summary TEXT,               -- what Claude found/fixed
    error_message TEXT,                -- if failed
    
    -- Metadata
    notification_emails TEXT,          -- comma-separated
    zip_filename TEXT,
    
    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP
);

CREATE TABLE deployment_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deployment_id TEXT NOT NULL REFERENCES deployments(id),
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    level TEXT DEFAULT 'INFO',         -- INFO, WARNING, ERROR
    step TEXT,                          -- which pipeline step
    message TEXT NOT NULL
);
```

---

## 8. ENVIRONMENT VARIABLES (.env)

```env
# GCP
PROJECT_ID=adp-413110
GOOGLE_APPLICATION_CREDENTIALS=./infra/sa_credentials.json

# Demo infrastructure (existing)
DEMO_DOMAIN=digitaldatatest.com
DEMO_URL_MAP_NAME=test-lb
DEMO_GLOBAL_IP_NAME=test-lb-ip

# Prod infrastructure
PROD_AUTO_REGISTER_DOMAINS=false
PROD_AUTO_CREATE_DNS_ZONE=true
PROD_AUTO_CREATE_SSL_CERT=false

# Bucket
BUCKET_LOCATION=US

# CDN
CDN_DEFAULT_TTL=3600
CDN_MAX_TTL=86400
CDN_CLIENT_TTL=3600

# Claude AI
ANTHROPIC_API_KEY=sk-ant-...

# Email notifications
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
NOTIFICATION_FROM_EMAIL=webdeploy@bestoftours.co.uk
NOTIFICATION_TO_EMAILS=team@bestoftours.co.uk

# App
UPLOAD_DIR=./uploads
TEMP_DIR=./tmp
LOG_LEVEL=INFO
```

---

## 9. CRITICAL IMPLEMENTATION RULES

1. **All GCP operations must be idempotent.** Check if resource exists before creating. Never fail on "already exists."

2. **The pipeline must be async.** Use FastAPI's `BackgroundTasks` or `asyncio.create_task` so the API returns immediately after queueing.

3. **Every pipeline step must log to both Python logger AND the WebSocket stream** so the frontend shows real-time progress.

4. **Claude API calls must have retry logic** (3 retries with exponential backoff). Claude response parsing must be defensive (handle malformed JSON).

5. **ZIP extraction must be sandboxed.** Use a unique temp directory per deployment. Clean up temp files after pipeline completes (success or failure).

6. **For demo mode, the dist/ files are uploaded UNDER the website_name prefix** in the bucket: `gs://bucket/{website_name}/index.html`. For prod mode, they go to root: `gs://bucket/index.html`.

7. **The build step must use subprocess with timeout** (5 minutes max). Capture stdout and stderr for logging.

8. **Node.js and npm must be available** on the server. Document this as a prerequisite.

9. **Content-Type must be set correctly** when uploading files to GCS. Map file extensions to MIME types. At minimum: `.html` → `text/html`, `.css` → `text/css`, `.js` → `application/javascript`, `.json` → `application/json`, `.svg` → `image/svg+xml`, `.png` → `image/png`, `.jpg` → `image/jpeg`, `.woff2` → `font/woff2`, `.woff` → `font/woff`.

10. **Email notification is the LAST step** and must ALWAYS execute, even if a prior step failed. The orchestrator catches exceptions and sends a failure email before re-raising.

---

## 10. DOCKER SETUP

```yaml
# docker-compose.yml
version: '3.8'
services:
  backend:
    build:
      context: .
      dockerfile: Dockerfile.backend
    ports:
      - "8000:8000"
    env_file:
      - .env
    volumes:
      - ./uploads:/app/uploads
      - ./tmp:/app/tmp
      - ./infra:/app/infra          # for SA credentials
      - ./data:/app/data            # for SQLite DB
    depends_on: []
  
  frontend:
    build:
      context: .
      dockerfile: Dockerfile.frontend
    ports:
      - "3000:3000"
    environment:
      - VITE_API_URL=http://localhost:8000
```

```dockerfile
# Dockerfile.backend
FROM python:3.11-slim
RUN apt-get update && apt-get install -y curl gnupg
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && apt-get install -y nodejs
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

## 11. TESTING INSTRUCTIONS

After implementation, verify the following end-to-end scenarios:

**Test 1 — Demo deployment with clean code**:
1. Upload a valid Vite + React ZIP that already follows all rules
2. Select "Demo" mode, website name: "test-site"
3. Expect: Claude says "pass", build succeeds, infra created, files uploaded
4. Verify: `https://digitaldatatest.com/test-site/` loads correctly

**Test 2 — Demo deployment with broken code**:
1. Upload a ZIP where vite.config.js has `base: '/'` hardcoded and images use `/images/...`
2. Select "Demo" mode
3. Expect: Claude detects issues, fixes vite.config and asset paths, rebuild succeeds
4. Verify: site works at subpath

**Test 3 — Prod deployment**:
1. Upload a valid ZIP
2. Select "Prod" mode, domain: "test-domain.com"
3. Expect: Full infra created (bucket, backend bucket, URL map, etc.)
4. Verify: GCP resources exist

**Test 4 — Failure handling**:
1. Upload an invalid ZIP (no package.json)
2. Expect: Pipeline fails at EXTRACT step, failure email sent

---

## 12. IMPLEMENTATION ORDER (RECOMMENDED)

For the agents team, implement in this order:

1. **config.py + models/enums.py** — Foundation
2. **db/** — Database setup
3. **zip_processor.py** — Can be tested independently
4. **build_service.py** — Can be tested with a sample Vite project
5. **claude_validator.py** — Can be tested with sample files
6. **infra/gcp_helpers.py** — Shared utilities from provided scripts
7. **infra/demo_deployer.py** — Refactored from provided demo script
8. **infra/prod_deployer.py** — Refactored from provided prod script
9. **upload_service.py** — Simple GCS upload
10. **email_service.py** — SMTP email
11. **pipeline_orchestrator.py** — Ties everything together
12. **API routes** — FastAPI endpoints
13. **Frontend** — React app
14. **Docker** — Containerization
15. **End-to-end testing**

---

Now implement the complete platform following this specification. Start with the backend, then the frontend. Ensure every service is fully functional, well-typed with Pydantic models, and properly error-handled. The GCP infrastructure code must be adapted from the provided scripts (not written from scratch) — keep the same resource naming conventions and idempotent patterns, but refactored into clean service classes.