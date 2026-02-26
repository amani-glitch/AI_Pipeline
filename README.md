# WebDeploy - AI-Powered Website Deployment Pipeline

Plateforme de deploiement automatisee permettant aux equipes de deployer des sites web sur **Google Cloud Platform** avec **validation IA du code source**. Uploadez un ZIP, choisissez un mode, et le pipeline gere tout : validation, build, provisionnement d'infrastructure, upload, CDN, DNS, SSL et notifications email.

---

## Table des matieres

- [Architecture Globale](#architecture-globale)
- [Modes de Deploiement](#modes-de-deploiement)
- [Pipeline en 9 Etapes](#pipeline-en-9-etapes)
- [Infrastructure GCP](#infrastructure-gcp)
- [Services Backend](#services-backend)
- [API REST & WebSocket](#api-rest--websocket)
- [Base de Donnees (Firestore)](#base-de-donnees-firestore)
- [Validation IA](#validation-ia)
- [Gestion des Domaines](#gestion-des-domaines)
- [Frontend React](#frontend-react)
- [Statistiques & Rapports](#statistiques--rapports)
- [Notifications Email](#notifications-email)
- [Resilience & Recovery](#resilience--recovery)
- [Dockerfile Auto-Generation (Cloud Run)](#dockerfile-auto-generation-cloud-run)
- [Stack Technique](#stack-technique)
- [Configuration](#configuration)
- [Lancer en Local](#lancer-en-local)
- [Deploiement sur Cloud Run](#deploiement-sur-cloud-run)
- [Structure du Projet](#structure-du-projet)

---

## Architecture Globale

```
                                     WebSocket (logs temps reel)
┌─────────────────────┐ ◄──────────────────────────────────────► ┌──────────────────────────┐
│                     │          REST API (FastAPI)                │                          │
│   React Frontend    │ ────────────────────────────────────────► │    Python Backend        │
│   Vite + Tailwind   │                                           │    9 Pipeline Steps      │
│   Port 3500         │                                           │    Port 8500             │
└─────────────────────┘                                           └────────────┬─────────────┘
                                                                               │
                  ┌────────────────────┬───────────────────────────┬───────────┘
                  │                    │                           │
            ┌─────▼──────┐    ┌───────▼────────────┐     ┌───────▼──────────┐
            │  Claude AI  │    │   GCP Services      │     │   Gmail API      │
            │  Sonnet 4.5 │    │                      │     │   Notifications  │
            │             │    │  Cloud Storage       │     │   Rapports       │
            │  OpenRouter  │    │  Load Balancer       │     │   periodiques    │
            │  (fallback)  │    │  Cloud CDN           │     └──────────────────┘
            └──────────────┘    │  Cloud DNS           │
                                │  Cloud Run           │
                                │  Cloud Build         │
                                │  Cloud Domains       │
                                │  Firestore           │
                                └──────────────────────┘
```

### Flux de donnees

```
Utilisateur                Frontend (React)               Backend (FastAPI)                 GCP
    │                          │                               │                              │
    │── Upload ZIP ──────────► │                               │                              │
    │                          │── POST /api/deploy ─────────► │                              │
    │                          │                               │── Backup ZIP ───────────────► │ Cloud Storage
    │                          │                               │── Create record ────────────► │ Firestore
    │                          │◄─ { deployment_id } ──────── │                              │
    │                          │                               │                              │
    │                          │◄═══ WebSocket logs ═════════ │── EXTRACT ──────────────────►│
    │                          │◄═══ WebSocket logs ═════════ │── AI_INSPECT (Claude) ──────►│
    │                          │◄═══ WebSocket logs ═════════ │── AI_FIX ───────────────────►│
    │                          │◄═══ WebSocket logs ═════════ │── BUILD (npm) ──────────────►│
    │                          │◄═══ WebSocket logs ═════════ │── VERIFY ───────────────────►│
    │                          │◄═══ WebSocket logs ═════════ │── DOMAIN_REGISTER ──────────►│ Cloud Domains
    │                          │◄═══ WebSocket logs ═════════ │── INFRA ────────────────────►│ LB / CDN / DNS
    │                          │◄═══ WebSocket logs ═════════ │── UPLOAD ───────────────────►│ Cloud Storage
    │                          │◄═══ WebSocket logs ═════════ │── NOTIFY ───────────────────►│ Gmail API
    │                          │                               │                              │
    │◄── Resultat + URL ────── │◄─ Deployment complete ────── │                              │
```

---

## Modes de Deploiement

### 1. Mode Demo

Deploie sur un **load balancer partage** sous un domaine commun pour des tests rapides.

| Aspect | Detail |
|--------|--------|
| **URL** | `https://digitaldatatest.com/{website_name}/` |
| **Infra** | LB partage + IP partagee (pre-existante) |
| **Cree** | Bucket GCS, backend bucket (CDN), regle de chemin dans l'URL map |
| **Routage** | Path-based (`/nom-du-site/*`) |
| **Cas d'usage** | Prototypage rapide, tests, previews |
| **Cout** | Tres faible (infrastructure partagee) |

### 2. Mode Production

Deploie sur un **load balancer partage** avec routage par nom d'hote (host-based routing) et un domaine personnalise.

| Aspect | Detail |
|--------|--------|
| **URL** | `https://{domaine-personnalise}/` |
| **Infra partagee** | LB `websites-urlmap-prod`, IP `websites-lb-ip-prod`, HTTPS proxy `websites-https-proxy-prod` |
| **Cree par site** | Bucket GCS + backend bucket CDN + certificat SSL + zone DNS |
| **Met a jour** | Host rule dans l'URL map partagee + certificat SSL dans le proxy HTTPS |
| **SSL** | Certificat Google-managed (auto-renouvele, jusqu'a 15 certs par proxy) |
| **DNS** | Zone Cloud DNS automatique avec enregistrements A + CNAME (www) |
| **Domaines externes** | Support GoDaddy, OVH, Namecheap... (nameservers affiches pour configuration) |
| **Cas d'usage** | Sites web en production |

**Architecture du Load Balancer Partage (Prod) :**

```
        IP Partagee (34.49.18.135)
               │
    ┌──────────▼───────────┐
    │  Forwarding Rules     │  (ports 80 + 443)
    │  (HTTP + HTTPS)       │
    └──────────┬────────────┘
               │
    ┌──────────▼───────────┐
    │  Target Proxies       │  websites-https-proxy-prod
    │  (+ SSL Certs)        │  (certificats SSL de tous les sites)
    └──────────┬────────────┘
               │
    ┌──────────▼───────────┐
    │  URL Map              │  websites-urlmap-prod
    │  (Host-based routing) │
    └──┬───────┬────────┬───┘
       │       │        │
  site1.com  site2.fr  site3.co.uk
       │       │        │
  Backend   Backend   Backend
  Bucket1   Bucket2   Bucket3
       │       │        │
  Storage   Storage   Storage
  Bucket1   Bucket2   Bucket3
```

### 3. Mode Cloud Run

Deploie une **application conteneurisee** (Node.js, Python, full-stack) sur Cloud Run.

| Aspect | Detail |
|--------|--------|
| **URL** | `https://{service}-{hash}.run.app` |
| **Infra** | Image Docker (Artifact Registry) + Service Cloud Run |
| **Cree** | Image Docker (via Cloud Build) + service Cloud Run |
| **Dockerfile** | Auto-genere selon le type de projet |
| **Ressources** | CPU: 1, RAM: 512Mi, Max instances: 10 |
| **Cas d'usage** | Apps dynamiques, APIs, SSR |

---

## Pipeline en 9 Etapes

Chaque deploiement execute ce pipeline sequentiellement. Les logs sont streames en temps reel via WebSocket.

| # | Etape | Description | Skippable |
|---|-------|-------------|-----------|
| 1 | **EXTRACT** | Extrait le ZIP, detecte la structure Vite, localise `package.json` et `vite.config` | Non |
| 2 | **AI_INSPECT** | Claude AI scanne le code source pour les problemes de deploiement (chemins hardcodes, base manquante, router) | Oui (si IA desactivee) |
| 3 | **AI_FIX** | Claude auto-corrige les problemes critiques (reecrit vite config base, corrige les chemins d'assets, ajoute le basename du router) | Oui (si pas de problemes) |
| 4 | **BUILD** | Execute `npm install` + `npm run build` avec le bon `VITE_BASE` selon le mode | Oui (projets statiques) |
| 5 | **VERIFY** | Lance `vite preview` et verifie HTTP 200, ou build l'image Docker pour Cloud Run | Oui (projets statiques) |
| 6 | **DOMAIN_REGISTER** | Achete le domaine via Google Cloud Domains si confirme par l'utilisateur | Oui (si non applicable) |
| 7 | **INFRA** | Provisionne toute l'infrastructure GCP selon le mode (buckets, LB, CDN, DNS, Cloud Run, etc.) | Non |
| 8 | **UPLOAD** | Upload les fichiers build vers GCS (10 workers paralleles) avec Content-Type et Cache-Control corrects | Non |
| 9 | **NOTIFY** | Envoie un email HTML de notification (succes ou echec) avec l'URL et le resume IA | **Toujours** |

> L'etape NOTIFY **s'execute toujours**, meme apres un echec, pour que l'equipe soit informee.

### Logique de skip des etapes

```python
EXTRACT       → Toujours execute
AI_INSPECT    → Skip si ai_enabled=false
AI_FIX        → Skip si pas de problemes detectes ou IA desactivee
BUILD         → Skip si projet statique (HTML/CSS/JS pur)
VERIFY        → Skip si projet statique
DOMAIN_REGISTER → Skip sauf si mode=prod + auto_register=true + purchase_confirmed=true
INFRA         → Toujours execute
UPLOAD        → Toujours execute
NOTIFY        → Toujours execute (meme en cas d'erreur)
```

---

## Infrastructure GCP

### Ressources Partagees (pre-existantes, jamais creees par la pipeline)

| Ressource | Nom | Utilisation |
|-----------|-----|-------------|
| **IP Statique Prod** | `websites-lb-ip-prod` (34.49.18.135) | Adresse IP unique pour tous les sites prod |
| **URL Map Prod** | `websites-urlmap-prod` | Routage host-based vers les backend buckets |
| **HTTPS Proxy Prod** | `websites-https-proxy-prod` | Terminaison SSL (jusqu'a 15 certificats) |
| **Forwarding Rules Prod** | `websites-https-forwarding-rule-prod` + `websites-http-forwarding-rule-prod` | Port 443 + 80 |
| **IP Statique Demo** | `test-lb-ip` | Adresse IP pour le domaine demo |
| **URL Map Demo** | `test-lb` | Routage path-based pour les sites demo |

### Ressources Creees par Site

**Mode Demo :**
- `demo-{safe_name}-bucket-demo` — Bucket Cloud Storage
- `demo-{safe_name}-backend-demo` — Backend Bucket (CDN)
- Path rule dans l'URL map demo

**Mode Production :**
- `{safe_name}-bucket-prod` — Bucket Cloud Storage
- `{safe_name}-backend-prod` — Backend Bucket (CDN)
- `{safe_name}-ssl-cert` — Certificat SSL Google-managed
- `{safe_name}-zone` — Zone DNS Cloud DNS (A + CNAME www)
- Host rule + path matcher dans l'URL map prod

**Mode Cloud Run :**
- Image Docker dans Artifact Registry
- Service Cloud Run `{safe_name}`

### Nommage des Ressources GCP

La fonction `safe_name()` convertit tout nom en nom GCP-valide :
- Tout en minuscules
- Points et underscores remplaces par des tirets
- Caracteres non-alphanumeriques remplaces
- Tronque a 63 caracteres (limite GCP)
- Exemple : `SaintDidier2026-2032.com` → `saintdidier2026-2032-com`

---

## Services Backend

### 1. PipelineOrchestrator (`services/pipeline_orchestrator.py`)

**Role** : Orchestrateur principal qui drive le workflow de deploiement complet.

- Execute les 9 etapes sequentiellement
- Met a jour le statut de chaque etape dans Firestore en temps reel
- Broadcast les logs via WebSocket pour l'affichage frontend
- Timeout global de 15 minutes (`PIPELINE_MAX_TIMEOUT_SECONDS`)
- Thread-safe : les workers en sous-processus peuvent emettre des logs sans conflit

```
run(deployment_id, zip_path, config)
  └─► _run_pipeline()
       ├─► _step_extract()
       ├─► _step_ai_inspect()
       ├─► _step_ai_fix()
       ├─► _step_build()
       ├─► _step_verify()
       ├─► _step_domain_register()
       ├─► _step_infra()
       ├─► _step_upload()
       └─► _step_notify()
```

### 2. ZipProcessingService (`services/zip_processor.py`)

**Role** : Extraction et validation des fichiers ZIP uploades.

- Valide le format ZIP
- Cree un repertoire d'extraction unique
- Deballe les ZIPs a dossier unique (single-folder unwrap)
- Detecte la structure de projet Vite (`package.json` + dep Vite)
- Parse `package.json` pour les dependances
- Detecte les client-side routers (react-router-dom, vue-router)
- Identifie les projets statiques vs. projets avec build

### 3. ClaudeValidationService (`services/claude_validator.py`)

**Role** : Inspection et auto-correction du code source par IA.

- Collecte les fichiers sources pertinents (`.js`, `.jsx`, `.ts`, `.tsx`, `.css`, `.html`)
- Exclut `node_modules`, `dist`, `.git`, `build`, `__MACOSX`
- Limite : 256 KB par fichier, 400 KB total
- Envoie a Claude avec un prompt specialise pour les problemes de deploiement
- Fallback vers OpenRouter (Llama 3.1 8B gratuit) si Claude echoue
- 3 tentatives avec backoff exponentiel (2s, 4s, 8s)
- Applique automatiquement les corrections retournees par l'IA

### 4. BuildService (`services/build_service.py`)

**Role** : Execution de npm/npx pour les projets Vite.

| Methode | Description |
|---------|-------------|
| `install_dependencies()` | `npm install` avec retry `--legacy-peer-deps` en cas d'erreur ERESOLVE |
| `build()` | `npm run build` avec `VITE_BASE` configure selon le mode |
| `preview()` | `vite preview` sur port aleatoire + verification HTTP 200 |

- Timeout : 10 minutes pour install et build
- Detection de lockfile (`package-lock.json` vs `yarn.lock` vs `pnpm-lock.yaml`)

### 5. DockerfileGenerator (`services/dockerfile_generator.py`)

**Role** : Detection de type de projet et generation automatique de Dockerfile.

| Priorite | Detection | Dockerfile genere |
|----------|-----------|-------------------|
| 1 | Dockerfile existant | Utilise tel quel |
| 2 | `package.json` + Vite (statique) | Multi-stage : Node build + nginx |
| 3 | `package.json` + script start | Node.js app |
| 4 | `requirements.txt` ou `pyproject.toml` | Python (Flask/Django) |
| 5 | Fichier `.html` | HTML statique + nginx |
| 6 | Fallback | nginx generique |

Tous les Dockerfiles generes exposent le port **8080** (exigence Cloud Run).

### 6. UploadService (`services/upload_service.py`)

**Role** : Upload des fichiers build vers Google Cloud Storage.

- Parcourt `dist/` recursivement
- 10 workers paralleles pour les uploads
- Mapping MIME types automatique pour les types web courants
- Headers Cache-Control :
  - HTML : `no-cache, no-store, must-revalidate`
  - Assets (JS, CSS, images, fonts) : `public, max-age=3600`

### 7. CloudBuildService (`services/cloud_build_service.py`)

**Role** : Build d'images Docker via Google Cloud Build.

- Cree un tarball du code source (exclut `node_modules`, `.git`, etc.)
- Upload le tarball vers Cloud Storage
- Soumet une requete Cloud Build
- Polling avec backoff exponentiel (timeout 15 min)
- Retourne l'URI de l'image dans Artifact Registry

### 8. DomainService (`services/domain_service.py`)

**Role** : Verification de propriete et achat de domaines.

| Methode | Description |
|---------|-------------|
| `check_domain(domain)` | Verifie dans l'ordre : Cloud Domains → Cloud DNS → searchDomains |
| `register_domain(domain)` | Achete via Cloud Domains API avec contact WHOIS depuis les settings |

**Statuts retournes par `check_domain()` :**
- `"owned"` — Domaine enregistre dans le projet GCP (Cloud Domains ou Cloud DNS)
- `"available"` — Disponible a l'achat (avec prix en USD/an)
- `"external"` — Enregistre chez un registrar externe (GoDaddy, OVH, etc.) — deploiement autorise
- `"unavailable"` — Non disponible a l'achat

### 9. EmailService (`services/email_service.py`)

**Role** : Envoi d'emails via Gmail API avec delegation de domaine.

| Type d'email | Contenu |
|-------------|---------|
| **Notification deploiement** | Succes (vert) ou echec (rouge) avec URL, duree, resume Claude |
| **Rapport periodique** | Resume admin avec statistiques, deployers, tokens IA, couts |
| **Rapport personnalise** | Stats par deployer avec ses sites et couts IA |

- Delegation de domaine via service account
- Templates HTML avec CSS inline
- Messages en francais
- Degradation gracieuse : si Gmail non configure, le pipeline continue

### 10. DailyReportService (`services/daily_report_service.py`)

**Role** : Planification et envoi automatique de rapports periodiques.

| Rapport | Frequence | Heure | Contenu |
|---------|-----------|-------|---------|
| **Quotidien** | Chaque jour | 18h00 (Europe/Paris) | Stats du jour |
| **Hebdomadaire** | Chaque vendredi | 18h00 (Europe/Paris) | Stats des 7 derniers jours |
| **Mensuel** | 1er du mois | 09h00 (Europe/Paris) | Stats du mois precedent |

- Verrouillage distribue via Firestore (evite les envois en double entre instances Cloud Run)
- TTL du verrou : 1 heure (override si le verrou est stale)

### 11. ZipBackupService (`services/zip_backup.py`)

**Role** : Backup et restauration des ZIPs pour la resilience.

| Methode | Description |
|---------|-------------|
| `backup_zip()` | Upload le ZIP vers `gs://{bucket}/uploads/{deployment_id}.zip` |
| `restore_zip()` | Telecharge depuis GCS (pour les retries apres crash de Cloud Run) |

---

## API REST & WebSocket

### Endpoints Deployments (`/api`)

| Methode | Endpoint | Description |
|---------|----------|-------------|
| `POST` | `/api/deploy` | Lancer un deploiement (multipart: ZIP + form params) |
| `GET` | `/api/deployments` | Liste des deploiements (params: `limit`, `offset`) |
| `GET` | `/api/deployments/{id}` | Detail d'un deploiement |
| `GET` | `/api/deployments/{id}/logs` | Logs d'un deploiement |
| `DELETE` | `/api/deployments/{id}` | Supprimer un deploiement + ressources GCP |

### Parametres de POST `/api/deploy`

| Parametre | Type | Obligatoire | Description |
|-----------|------|-------------|-------------|
| `file` | File | Oui | Fichier ZIP ou HTML |
| `mode` | String | Oui | `demo`, `prod`, ou `cloudrun` |
| `website_name` | String | Oui | Slug du site (a-z, 0-9, tirets) |
| `domain` | String | Prod seulement | Domaine personnalise |
| `deployer_first_name` | String | Oui | Prenom du deployer |
| `deployer_last_name` | String | Oui | Nom du deployer |
| `deployer_email` | String | Oui | Email du deployer |
| `notification_emails` | String | Non | Emails de notification (CSV) |
| `ai_enabled` | String | Non | `"true"` pour activer la validation IA |
| `domain_purchase_confirmed` | String | Non | `"true"` pour confirmer l'achat du domaine |

### Endpoints Domaines (`/api/domains`)

| Methode | Endpoint | Description |
|---------|----------|-------------|
| `GET` | `/api/domains/check?domain=example.com` | Verifie propriete/disponibilite du domaine |
| `POST` | `/api/domains/register` | Achete un domaine via Cloud Domains |

### Endpoints Statistiques (`/api/statistics`)

| Methode | Endpoint | Description |
|---------|----------|-------------|
| `GET` | `/api/statistics` | Stats avec `preset` (today/3days/7days/30days) ou `start_date`/`end_date` |
| `POST` | `/api/statistics/send-report` | Envoi de rapport a la demande |

### Endpoints Utilitaires

| Methode | Endpoint | Description |
|---------|----------|-------------|
| `GET` | `/api/health` | Health check (status, timestamp, version) |
| `WebSocket` | `/ws/logs/{deployment_id}` | Streaming de logs en temps reel |

### WebSocket Protocol

```
Client ──► ws://host/ws/logs/{deployment_id}

Serveur ──► messages texte (logs du pipeline)
Serveur ──► ping toutes les 30 secondes (keepalive)

Client deconnecte ──► cleanup automatique
```

Chaque subscriber recoit une `asyncio.Queue`. Les messages sont broadcast de maniere thread-safe via `loop.call_soon_threadsafe()`. Les clients lents sont proteges (messages droppes si la queue est pleine).

---

## Base de Donnees (Firestore)

La plateforme utilise **Google Cloud Firestore** en mode natif.

### Collection `deployments`

| Champ | Type | Description |
|-------|------|-------------|
| `id` | string | UUID du deploiement |
| `website_name` | string | Nom du site |
| `mode` | string | demo / prod / cloudrun |
| `domain` | string | Domaine personnalise (prod) |
| `status` | string | queued / running / success / failed |
| `current_step` | string | Etape en cours |
| `steps_status` | map | Statut JSON de chaque etape |
| `result_url` | string | URL finale du site |
| `error_message` | string | Message d'erreur (si echec) |
| `claude_summary` | string | Resume genere par Claude |
| `deployer_first_name` | string | Prenom du deployer |
| `deployer_last_name` | string | Nom du deployer |
| `deployer_email` | string | Email du deployer |
| `notification_emails` | string | Emails CSV |
| `zip_filename` | string | Nom du fichier ZIP |
| `ai_enabled` | boolean | IA activee |
| `ai_input_tokens` | number | Tokens d'entree Claude |
| `ai_output_tokens` | number | Tokens de sortie Claude |
| `dns_nameservers` | array | Nameservers Cloud DNS (prod) |
| `retry_count` | number | Nombre de retries automatiques |
| `created_at` | timestamp | Date de creation |
| `started_at` | timestamp | Debut d'execution |
| `completed_at` | timestamp | Fin d'execution |

### Collection `deployment_logs`

| Champ | Type | Description |
|-------|------|-------------|
| `deployment_id` | string | Reference au deploiement |
| `timestamp` | timestamp | Date/heure du log |
| `level` | string | INFO / WARNING / ERROR |
| `step` | string | Etape du pipeline |
| `message` | string | Message de log |

### Collection `report_locks`

| Champ | Type | Description |
|-------|------|-------------|
| `locked_at` | timestamp | Quand le verrou a ete pris |
| `locked_by` | string | Instance qui detient le verrou |

---

## Validation IA

### Claude AI (Principal)

**Modele** : `claude-sonnet-4-5-20250929` via Anthropic API

Claude inspecte le code source et verifie :

- **vite.config.js** — doit avoir `base: process.env.VITE_BASE || '/'`
- **References d'assets** — pas de chemins absolus hardcodes (`/images/logo.png`), utiliser des imports
- **index.html** — pas de chemins hardcodes pour script/style/favicon
- **CSS url()** — pas de references absolues `url(/...)`
- **React Router** — doit utiliser `basename={import.meta.env.BASE_URL}` si react-router-dom detecte
- **package.json scripts** — le build doit etre `vite build`

Quand des problemes sont trouves, Claude retourne des remplacements exacts qui sont appliques automatiquement aux fichiers sources avant l'etape de build.

**Format de reponse** : JSON strict avec `status`, `issues[]`, `fixes[]`, et `summary`.

**Logique de retry** : 3 tentatives avec backoff exponentiel (2s, 4s, 8s) pour les rate limits et erreurs serveur.

### OpenRouter Fallback

**Modele** : `meta-llama/llama-3.1-8b-instruct:free` (Meta Llama 3.1 8B)

Si Claude est indisponible, le meme prompt est envoye a OpenRouter via le modele Llama gratuit. Si les deux echouent, le pipeline continue sans validation IA (degradation gracieuse).

### Suivi des Couts IA

| Metrique | Tarif |
|----------|-------|
| Tokens d'entree | $3.00 / 1M tokens |
| Tokens de sortie | $15.00 / 1M tokens |
| Suivi | Par deploiement dans Firestore + agreges dans les statistiques |

---

## Gestion des Domaines

### Flux de Verification (Frontend)

```
Utilisateur tape "example.com" dans le champ domaine
  → debounce 800ms
  → GET /api/domains/check?domain=example.com
  → Backend : verifie via GCP APIs

SI "owned"    : ✓ Vert — "Domaine deja enregistre" → Deploy active
SI "available": ⚠ Ambre — "Attention, achat du domaine — 12.00 USD/an"
                + case a cocher "Je confirme l'achat" → Deploy active apres confirmation
SI "external" : ℹ Bleu — "Domaine chez un registrar externe" → Deploy active
                (nameservers affiches apres deploiement)
SI checking   : ⏳ Spinner — verification en cours
```

### Verification Backend (3 niveaux)

```
1. Cloud Domains registrations → domaine achete via Google ?
2. Cloud DNS managed zones     → zone DNS dans le projet GCP ?
3. Cloud Domains searchDomains → disponible a l'achat ?
```

### Configuration DNS Post-Deploiement

Apres un deploiement prod avec un domaine externe (GoDaddy, OVH, etc.), l'interface affiche :

1. Les **nameservers Google Cloud DNS** dans un tableau avec boutons "Copier"
2. Des **instructions pas a pas** pour configurer le registrar
3. Un **lien vers dnschecker.org** pour verifier la propagation DNS
4. Temps de propagation : 15 minutes a 48 heures

---

## Frontend React

### Pages et Navigation

| Route | Composant | Description |
|-------|-----------|-------------|
| `/` | `DeploymentForm` | Formulaire de deploiement principal |
| `/deployments/:id` | `DeploymentDetail` | Vue pipeline temps reel + resultat |
| `/history` | `DeploymentHistory` | Historique des deploiements (tableau) |
| `/statistics` | `Statistics` | Dashboard statistiques + rapports |

### Composants Principaux

#### UploadZone
- 3 modes d'upload : ZIP (500 MB max), HTML (50 MB), Dossier (500 MB)
- Drag & drop avec validation de format et taille
- Preview du fichier selectionne

#### DeploymentForm
- Formulaire multi-etapes avec affichage progressif
- Selecteur de mode (Demo / Production / Cloud Run)
- Verification de domaine en temps reel (debounce 800ms)
- Confirmation d'achat de domaine avec prix
- Toggle activation IA
- Auto-population des emails de notification
- Slug auto-formate (lowercase, tirets)

#### PipelineLogs
- Stepper visuel 9 etapes avec icones et couleurs
- Indicateur d'etat par etape (pending / running / completed / failed / skipped)
- Duree affichee par etape
- Terminal noir style macOS avec logs colores (vert=info, jaune=warn, rouge=error)
- Auto-scroll vers le bas
- Indicateur Live/Connecting

#### DeploymentStatus
- Banniere de resultat (vert succes / rouge echec)
- Lien cliquable vers l'URL de deploiement
- Resume IA (si disponible)
- Panneau DNS complet (prod avec nameservers) :
  - Tableau des nameservers avec copie individuelle et "Tout copier"
  - 5 etapes detaillees pour configurer le registrar
  - Lien dnschecker.org

#### Composants Statistiques
- `DailyBarChart` — Barres empilees "Avec IA" vs "Sans IA" par jour (Recharts)
- `StatusPieChart` — Donut chart succes/echec/running/queued
- `DeployerTable` — Tableau par deployer avec sites, modes, couts IA
- `SendReportPanel` — Modal d'envoi de rapport a la demande

### Hooks Personnalises

| Hook | Description |
|------|-------------|
| `useDeployment(id)` | Polling du statut toutes les 2 secondes, arret auto en etat terminal |
| `useWebSocket(id)` | Streaming temps reel des logs, reconnexion auto (max 20 tentatives, backoff exponentiel) |

---

## Statistiques & Rapports

### Dashboard (`/statistics`)

- **Presets** : Aujourd'hui, 3 jours, 7 jours, 30 jours, Personnalise
- **Metriques** : Total deploiements, avec IA, sans IA, moyenne/jour, nombre de deployers
- **Graphiques** : Barres quotidiennes (IA vs non-IA), donut par statut
- **Tableau deployers** : Nom, email, sites, modes, cout IA
- **Tokens IA** : Input/output totaux + cout estime en USD

### Rapports Automatiques

| Type | Schedule | Destinataires |
|------|----------|---------------|
| Quotidien | 18h00 chaque jour | Admins |
| Hebdomadaire | Vendredi 18h00 | Admins |
| Mensuel | 1er du mois 09h00 | Admins |

### Rapport a la Demande

Via le bouton "Envoyer un rapport" dans le dashboard :
- Choix de la periode
- Envoi aux deployers (rapport personnalise par personne)
- Envoi aux admins (resume global)

---

## Notifications Email

Emails HTML envoyes via **Gmail API** avec delegation de domaine :

### Email de Deploiement
- **Succes** : Header vert, URL cliquable, resume Claude IA, duree
- **Echec** : Header rouge, message d'erreur (monospace), etape echouee
- Envoye systematiquement (meme en cas d'echec)

### Email de Rapport
- Resume avec tableaux de stats
- Breakdown par deployer
- Statistiques d'utilisation IA et couts
- Graphiques quotidiens

---

## Resilience & Recovery

### Stale Deployment Watchdog

Un **watchdog** tourne en arriere-plan toutes les 2 minutes :

1. Scanne les deploiements en statut `running` ou `queued`
2. Si un deploiement depasse le timeout (`PIPELINE_MAX_TIMEOUT_SECONDS`, defaut 15 min) :
   - Si `retry_count < 2` → re-queue automatiquement avec backup ZIP depuis GCS
   - Sinon → marque comme `failed`

### Recovery au Demarrage

Au demarrage de l'application (`lifespan`) :
1. Recupere tous les deploiements stale (restes en `running`/`queued`)
2. Tente de les re-executer avec le ZIP backup depuis Cloud Storage
3. Maximum 2 retries automatiques par deploiement

### Backup ZIP

Chaque ZIP uploade est sauvegarde dans Cloud Storage (`gs://{project}-deploy-uploads/uploads/{id}.zip`) avant l'execution du pipeline. Cela permet :
- La reprise apres un crash de Cloud Run
- Les retries automatiques sans re-upload par l'utilisateur

---

## Dockerfile Auto-Generation (Cloud Run)

| Type detecte | Strategie |
|-------------|-----------|
| **Vite statique** | 2 stages : `node:20-alpine` build + `nginx:alpine` serve |
| **Node.js app** | `node:20-alpine` avec `npm start` |
| **Python (FastAPI/Flask)** | `python:3.11-slim` avec entrypoint auto-detecte |
| **HTML statique** | `nginx:alpine` avec config SPA |

---

## Stack Technique

### Backend

| Technologie | Usage |
|------------|-------|
| **FastAPI** | Framework web async + API REST |
| **Uvicorn** | Serveur ASGI |
| **Pydantic** | Validation de requetes/reponses + settings |
| **WebSockets** | Streaming de logs temps reel |
| **Anthropic SDK** | Client API Claude AI |
| **google-cloud-firestore** | Base de donnees |
| **google-cloud-storage** | Upload de fichiers vers GCS |
| **google-cloud-domains** | Verification et achat de domaines |
| **google-api-python-client** | APIs Compute Engine, Cloud DNS, Gmail |
| **google-auth** | Authentification service account |
| **httpx** | Client HTTP async (fallback OpenRouter) |

### Frontend

| Technologie | Usage |
|------------|-------|
| **React 18** | Framework UI |
| **Vite 6** | Build tool + dev server |
| **TailwindCSS 4** | CSS utility-first |
| **React Router 6** | Routing client-side |
| **Axios** | Client HTTP |
| **Recharts** | Graphiques (barres, donuts) |
| **Lucide React** | Icones |

### Infrastructure (GCP)

| Service | Usage |
|---------|-------|
| **Cloud Storage** | Hebergement de fichiers statiques |
| **Compute Engine (LB)** | Load balancer, URL maps, backend buckets, forwarding rules |
| **Cloud CDN** | Cache global avec TTLs configurables |
| **Cloud Run** | Hebergement d'applications conteneurisees |
| **Cloud Build** | Build d'images Docker |
| **Artifact Registry** | Stockage d'images Docker |
| **Cloud DNS** | Gestion de zones et records DNS |
| **Cloud Domains** | Verification et achat de noms de domaine |
| **Firestore** | Base de donnees NoSQL pour les deploiements |
| **Gmail API** | Notifications email (delegation de domaine) |

---

## Configuration

### Variables d'environnement

Voir `backend/.env.example` pour la liste complete.

```env
# ── GCP ──────────────────────────────────────────────────────
PROJECT_ID=adp-413110
GOOGLE_APPLICATION_CREDENTIALS=./service-account.json

# ── Demo (infra pre-existante) ───────────────────────────────
DEMO_DOMAIN=digitaldatatest.com
DEMO_URL_MAP_NAME=test-lb
DEMO_GLOBAL_IP_NAME=test-lb-ip

# ── Production (LB partage pre-existant) ─────────────────────
PROD_URL_MAP_NAME=websites-urlmap-prod
PROD_GLOBAL_IP_NAME=websites-lb-ip-prod
PROD_HTTPS_PROXY_NAME=websites-https-proxy-prod

# ── Toggles Prod ─────────────────────────────────────────────
PROD_AUTO_REGISTER_DOMAINS=true
PROD_AUTO_CREATE_DNS_ZONE=true
PROD_AUTO_CREATE_SSL_CERT=true

# ── Contact WHOIS (Cloud Domains) ────────────────────────────
DOMAINS_CONTACT_EMAIL=admin@company.com
DOMAINS_CONTACT_PHONE=+33600000000
DOMAINS_CONTACT_COMPANY=Company Name

# ── IA ───────────────────────────────────────────────────────
ANTHROPIC_API_KEY=sk-ant-...
OPENROUTER_API_KEY=sk-or-...
OPENROUTER_MODEL=meta-llama/llama-3.1-8b-instruct:free

# ── Email ────────────────────────────────────────────────────
GMAIL_DELEGATED_USER=admin@company.com
NOTIFICATION_FROM_EMAIL=deploy@company.com
NOTIFICATION_TO_EMAILS=team@company.com
DAILY_REPORT_EMAILS=admin@company.com,manager@company.com

# ── Cloud Run ────────────────────────────────────────────────
CLOUDRUN_REGION=europe-west1
CLOUDRUN_MEMORY=512Mi
CLOUDRUN_CPU=1
CLOUDRUN_MAX_INSTANCES=10

# ── App ──────────────────────────────────────────────────────
BUILD_TIMEOUT_SECONDS=600
PIPELINE_MAX_TIMEOUT_SECONDS=900
MAX_ZIP_SIZE_MB=500
LOG_LEVEL=INFO
```

### Pre-requis GCP

```bash
# APIs a activer
gcloud services enable compute.googleapis.com
gcloud services enable storage.googleapis.com
gcloud services enable cloudbuild.googleapis.com
gcloud services enable run.googleapis.com
gcloud services enable dns.googleapis.com
gcloud services enable domains.googleapis.com
gcloud services enable firestore.googleapis.com
gcloud services enable artifactregistry.googleapis.com

# Roles IAM du service account
roles/storage.admin
roles/compute.admin
roles/dns.admin
roles/domains.admin
roles/run.admin
roles/cloudbuild.builds.editor
roles/artifactregistry.writer
roles/iam.serviceAccountUser
```

---

## Lancer en Local

### Sans Docker

```bash
# Backend
cd backend
cp .env.example .env           # remplir les valeurs
pip install -r requirements.txt
uvicorn main:app --reload --port 8500

# Frontend (dans un autre terminal)
cd frontend
npm install
npm run dev                     # demarre sur http://localhost:3500
```

Le frontend proxie automatiquement `/api` et `/ws` vers `http://localhost:8500` (configure dans `vite.config.js`).

### Avec Docker Compose

```bash
docker-compose up --build
# Frontend : http://localhost:3000
# Backend  : http://localhost:8000
```

---

## Deploiement sur Cloud Run

```bash
# Build et push l'image backend
gcloud builds submit --config=cloudbuild-backend.yaml .

# Deploy le backend sur Cloud Run
gcloud run deploy webdeploy-backend \
  --image=europe-west1-docker.pkg.dev/PROJECT_ID/cloud-run-images/webdeploy-backend:latest \
  --region=europe-west1 \
  --update-secrets=/app/secrets/service-account.json=webdeploy-sa-key:latest \
  --set-env-vars=GOOGLE_APPLICATION_CREDENTIALS=/app/secrets/service-account.json

# Build et push l'image frontend
gcloud builds submit --config=cloudbuild-frontend.yaml .

# Deploy le frontend sur Cloud Run
gcloud run deploy webdeploy-frontend \
  --image=europe-west1-docker.pkg.dev/PROJECT_ID/cloud-run-images/webdeploy-frontend:latest \
  --region=europe-west1
```

---

## Structure du Projet

```
├── backend/
│   ├── api/
│   │   ├── dependencies.py              # DI FastAPI + WebSocket broadcast
│   │   └── routes/
│   │       ├── deployments.py           # CRUD deploiements + POST /deploy
│   │       ├── domains.py               # Check + register domaines
│   │       ├── health.py                # Health check
│   │       ├── statistics.py            # Stats + envoi rapports
│   │       └── websocket.py             # Streaming logs temps reel
│   ├── db/
│   │   ├── database.py                  # Client Firestore (singleton)
│   │   ├── crud.py                      # CRUD Firestore (deployments + logs)
│   │   └── stats_queries.py             # Agregation statistiques
│   ├── infra/
│   │   ├── gcp_helpers.py               # Utilitaires GCP (auth, nommage, polling)
│   │   ├── demo_deployer.py             # Deployer demo (LB partage, path-based)
│   │   ├── prod_deployer.py             # Deployer prod (LB partage, host-based)
│   │   └── cloudrun_deployer.py         # Deployer Cloud Run (conteneurs)
│   ├── models/
│   │   ├── enums.py                     # Enumerations (modes, statuts, etapes)
│   │   └── deployment.py                # Schemas Pydantic + records Firestore
│   ├── services/
│   │   ├── pipeline_orchestrator.py     # Orchestrateur 9 etapes
│   │   ├── zip_processor.py             # Extraction + validation ZIP
│   │   ├── claude_validator.py          # Inspection IA + auto-fix
│   │   ├── build_service.py             # npm install/build + vite preview
│   │   ├── upload_service.py            # Upload GCS (10 workers paralleles)
│   │   ├── dockerfile_generator.py      # Auto-generation Dockerfile
│   │   ├── cloud_build_service.py       # Build Docker via Cloud Build
│   │   ├── domain_service.py            # Check + achat domaines (Cloud Domains)
│   │   ├── email_service.py             # Notifications Gmail API
│   │   ├── daily_report_service.py      # Rapports periodiques schedules
│   │   └── zip_backup.py               # Backup/restore ZIP vers GCS
│   ├── config.py                        # Settings (pydantic-settings + .env)
│   ├── main.py                          # Point d'entree FastAPI + lifecycle
│   ├── requirements.txt                 # Dependances Python
│   └── .env.example                     # Template de configuration
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── Layout.jsx               # Navigation + layout
│   │   │   ├── DeploymentForm.jsx       # Formulaire deploiement
│   │   │   ├── UploadZone.jsx           # Upload ZIP/HTML/Dossier
│   │   │   ├── DeploymentHistory.jsx    # Historique (tableau)
│   │   │   ├── DeploymentStatus.jsx     # Banniere resultat + DNS config
│   │   │   ├── PipelineLogs.jsx         # Stepper + terminal logs
│   │   │   └── statistics/
│   │   │       ├── DailyBarChart.jsx    # Barres quotidiennes
│   │   │       ├── StatusPieChart.jsx   # Donut par statut
│   │   │       ├── DeployerTable.jsx    # Tableau deployers
│   │   │       └── SendReportPanel.jsx  # Modal envoi rapport
│   │   ├── pages/
│   │   │   ├── DeploymentDetail.jsx     # Vue pipeline + resultat
│   │   │   └── Statistics.jsx           # Dashboard statistiques
│   │   ├── hooks/
│   │   │   ├── useDeployment.js         # Polling statut (2s)
│   │   │   └── useWebSocket.js          # Streaming logs temps reel
│   │   ├── services/
│   │   │   └── api.js                   # Client Axios
│   │   ├── styles/
│   │   │   └── globals.css              # Styles terminal + scrollbar
│   │   ├── App.jsx                      # Routes React Router
│   │   └── main.jsx                     # Point d'entree React
│   ├── package.json
│   ├── vite.config.js                   # Dev server port 3500 + proxy
│   └── tailwind.config.js
├── docker-compose.yml                   # Dev local avec Docker
├── Dockerfile.backend                   # Image dev backend
├── Dockerfile.backend.prod              # Image prod backend (Cloud Run)
├── Dockerfile.frontend                  # Image dev frontend
├── Dockerfile.frontend.prod             # Image prod frontend (nginx)
├── nginx.conf                           # Config nginx frontend prod
├── cloudbuild-backend.yaml              # Cloud Build backend
├── cloudbuild-frontend.yaml             # Cloud Build frontend
└── README.md                            # Ce fichier
```
