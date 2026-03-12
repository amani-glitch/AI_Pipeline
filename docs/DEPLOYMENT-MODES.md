# Modes de Deploiement — Architecture Detaillee

Ce document explique les differents modes de deploiement de la plateforme WebDeploy,
leur architecture reseau/GCP, et les concepts fondamentaux de routing.

---

## Vue d'ensemble

La plateforme supporte **4 modes de deploiement** :

| Mode | URL finale | Routing | SSL | Cas d'usage |
|------|-----------|---------|-----|-------------|
| **Demo (path-based)** | `digitaldatatest.com/mon-site/` | Path-based | Partage (1 cert) | Tests rapides, previews internes |
| **Subdomain** | `mon-site.digitaldatatest.com` | Host-based | Wildcard (`*.digitaldatatest.com`) | Sites clients demo avec URL propre |
| **Production** | `mon-domaine.com` | Host-based | Individuel par domaine | Sites en production avec domaine personnalise |
| **Cloud Run** | `{service}.run.app` | Gere par GCP | Gere par GCP | Applications backend/fullstack |

---

## 1. Mode Demo — Path-Based Routing

### Concept

Tous les sites demo partagent un **seul domaine** (`digitaldatatest.com`) et un
**seul load balancer**. Chaque site est differencie par son **chemin URL** (path).

### Architecture GCP

```
Client → https://digitaldatatest.com/portfolio/index.html
         │
         ▼
   IP Statique Globale (test-lb-ip)
         │
         ▼
   Forwarding Rule (HTTPS:443)
         │
         ▼
   Target HTTPS Proxy ←── SSL Cert (digitaldatatest.com)
         │
         ▼
   URL Map (test-lb)
     │
     ├─ Host: digitaldatatest.com
     │   └─ Path Matcher: main-matcher
     │       ├─ /portfolio/*     → demo-portfolio-backend-demo     → gs://demo-portfolio-bucket-demo
     │       ├─ /ecommerce/*     → demo-ecommerce-backend-demo     → gs://demo-ecommerce-bucket-demo
     │       └─ /client-site/*   → demo-client-site-backend-demo   → gs://demo-client-site-bucket-demo
```

### Ressources creees par deploiement

| Ressource | Nommage | Partagee ? |
|-----------|---------|------------|
| GCS Bucket | `demo-{safe_name}-bucket-demo` | Non (1 par site) |
| Backend Bucket | `demo-{safe_name}-backend-demo` | Non (1 par site) |
| Path Rule dans URL Map | `/{website_name}/*` | Ajoutee au URL Map partage |
| SSL Certificate | `digitaldatatest.com` | Oui (1 pour tous) |
| IP Globale | `test-lb-ip` | Oui |

### Comment le routing fonctionne

1. Le navigateur envoie une requete a `digitaldatatest.com/portfolio/page.html`
2. Le DNS resout `digitaldatatest.com` vers l'IP statique du load balancer
3. Le load balancer recoit la requete HTTPS (SSL termine au proxy)
4. Le **URL Map** examine le **path** (`/portfolio/page.html`)
5. La regle `/portfolio/*` matche → requete routee vers `demo-portfolio-backend-demo`
6. Le backend bucket sert le fichier depuis `gs://demo-portfolio-bucket-demo/portfolio/page.html`

### Structure des fichiers dans le bucket

```
gs://demo-portfolio-bucket-demo/
  └── portfolio/
      ├── index.html
      ├── assets/
      │   ├── main.js
      │   └── style.css
      └── images/
          └── logo.png
```

Les fichiers sont uploades dans un sous-dossier `/{website_name}/` car le path
dans l'URL correspond au prefix du bucket.

---

## 2. Mode Subdomain — Host-Based Routing sur Domaine Partage

### Concept

Chaque site obtient un **sous-domaine** du domaine principal :
`mon-site.digitaldatatest.com`. Le routing se fait par **hostname** (pas par path),
ce qui donne une URL plus propre et professionnelle.

### Architecture GCP

```
Client → https://portfolio.digitaldatatest.com/index.html
         │
         ▼
   IP Statique Globale (test-lb-ip)  ← Meme IP que demo !
         │
         ▼
   Forwarding Rule (HTTPS:443)
         │
         ▼
   Target HTTPS Proxy ←── SSL Cert Wildcard (*.digitaldatatest.com)
         │                 + SSL Cert (digitaldatatest.com)
         ▼
   URL Map (test-lb)
     │
     ├─ Host: digitaldatatest.com       ← Demo path-based (existant)
     │   └─ Path Matcher: main-matcher
     │       └─ /old-site/* → ...
     │
     ├─ Host: portfolio.digitaldatatest.com     ← Subdomain !
     │   └─ Path Matcher: pm-sub-portfolio
     │       └─ defaultService → sub-portfolio-backend-demo
     │                            → gs://sub-portfolio-bucket-demo
     │
     └─ Host: ecommerce.digitaldatatest.com
         └─ Path Matcher: pm-sub-ecommerce
             └─ defaultService → sub-ecommerce-backend-demo
                                  → gs://sub-ecommerce-bucket-demo
```

### Difference cle avec le mode Demo

| Aspect | Demo (Path) | Subdomain (Host) |
|--------|-------------|-------------------|
| URL | `digitaldatatest.com/site/` | `site.digitaldatatest.com` |
| Routing | Path rules dans 1 matcher | Host rule + path matcher par site |
| SSL | 1 cert pour le domaine root | 1 cert wildcard `*.domain` |
| Upload GCS | Fichiers dans `/{site}/` | Fichiers a la racine `/` |
| Professionnel | Non (path visible) | Oui (URL propre) |
| DNS | Rien a faire | 1 record DNS wildcard `*.digitaldatatest.com` |

### Ressources creees par deploiement

| Ressource | Nommage | Partagee ? |
|-----------|---------|------------|
| GCS Bucket | `sub-{safe_name}-bucket-demo` | Non (1 par site) |
| Backend Bucket | `sub-{safe_name}-backend-demo` | Non (1 par site) |
| Host Rule | `{website_name}.digitaldatatest.com` | Ajoutee au URL Map partage |
| Path Matcher | `pm-sub-{safe_name}` | Non (1 par site) |
| SSL Wildcard | `wildcard-digitaldatatest-com` | Oui (1 pour tous les subdomains) |
| IP Globale | `test-lb-ip` | Oui (meme que demo) |

### Pre-requis (one-time setup)

1. **Certificat SSL wildcard** : Un certificat Google-managed pour `*.digitaldatatest.com`
   attache au HTTPS proxy partage
2. **DNS wildcard** : Un record A `*.digitaldatatest.com` → IP du load balancer
   (dans Cloud DNS ou chez le registrar)

### Structure des fichiers dans le bucket

```
gs://sub-portfolio-bucket-demo/
  ├── index.html          ← A la racine ! Pas dans un sous-dossier
  ├── assets/
  │   ├── main.js
  │   └── style.css
  └── images/
      └── logo.png
```

Contrairement au mode demo, les fichiers sont a la **racine du bucket** car
l'URL `portfolio.digitaldatatest.com/index.html` n'a pas de prefix de path.

---

## 3. Mode Production — Custom Domain

### Concept

Chaque site a son **propre domaine** (`client-site.com`). La plateforme cree
les certificats SSL, les zones DNS, et ajoute le domaine au load balancer
de production partage.

### Architecture GCP

```
Client → https://client-site.com/index.html
         │
         ▼
   IP Statique Globale (websites-lb-ip-prod)
         │
         ▼
   Forwarding Rule (HTTPS:443)
         │
         ▼
   Target HTTPS Proxy (websites-https-proxy-prod)
     ├── SSL Cert: client-site-com-ssl-cert
     ├── SSL Cert: other-domain-ssl-cert
     └── ...
         │
         ▼
   URL Map (websites-urlmap-prod)
     ├─ Host: client-site.com
     │   └─ Path Matcher: pm-client-site-com
     │       └─ defaultService → client-site-com-backend-prod
     │                            → gs://client-site-com-bucket-prod
     │
     └─ Host: other-domain.fr
         └─ Path Matcher: pm-other-domain-fr
             └─ defaultService → other-domain-fr-backend-prod
```

### Ressources creees par deploiement

| Ressource | Nommage | Partagee ? |
|-----------|---------|------------|
| GCS Bucket | `{safe_domain}-bucket-prod` | Non |
| Backend Bucket | `{safe_domain}-backend-prod` | Non |
| SSL Certificate | `{safe_domain}-ssl-cert` | Non |
| DNS Zone | `{safe_domain}-zone` | Non |
| A Record | `{domain}.` → IP du LB | Non |
| CNAME Record | `www.{domain}.` → `{domain}.` | Non |
| Host Rule | `{domain}` dans URL Map partage | Ajoutee |
| Path Matcher | `pm-{safe_domain}` | Non |

### Etapes du deploiement Production

1. Recuperer l'IP du LB partage (`websites-lb-ip-prod`)
2. Creer le bucket GCS + permissions publiques
3. Creer le backend bucket (CDN active)
4. Ajouter host rule + path matcher au URL Map partage
5. Creer le certificat SSL Google-managed
6. Attacher le cert au HTTPS proxy partage
7. Creer la zone DNS + records A et CNAME
8. Uploader les fichiers
9. Invalider le cache CDN

### Workflow DNS (domaine externe)

Si le domaine est enregistre chez un registrar externe (GoDaddy, OVH...) :

```
1. WebDeploy cree une zone DNS dans Cloud DNS
2. Cloud DNS fournit des nameservers (ns1.google.com, ns2.google.com, etc.)
3. L'utilisateur va chez son registrar et change les NS pour pointer vers Google
4. Google gere ensuite le DNS (A record → IP du LB)
5. Le certificat SSL se provisionne automatiquement (validation DNS)
```

---

## 4. Mode Cloud Run — Applications Conteneurisees

### Concept

Pour les applications qui ne sont pas des sites statiques (Node.js, Python, etc.),
le code est conteneurise et deploye sur Cloud Run.

### Etapes specifiques

1. Detection du type de projet (Node.js, Python, Go, etc.)
2. Generation automatique d'un Dockerfile
3. Build de l'image Docker via Cloud Build
4. Push vers Artifact Registry
5. Deploiement sur Cloud Run

L'URL finale est geree par Cloud Run : `https://{service}-{hash}.run.app`

---

## Comparaison des couts

| Mode | IP | SSL | DNS | CDN | Total/mois |
|------|-----|------|------|------|------------|
| Demo | Partage | 0 $ (1 cert) | 0 $ | Partage | ~0 $ supplementaire |
| Subdomain | Partage | 0 $ (wildcard) | 0 $ (wildcard A) | Partage | ~0 $ supplementaire |
| Production | Partage | ~0.75 $/cert | ~0.20 $/zone | Partage | ~1 $/domaine |
| Cloud Run | N/A | Inclus | N/A | N/A | ~0-5 $/service |

---

## Glossaire

- **URL Map** : Composant GCP qui route les requetes HTTP(S) vers les backend buckets en fonction du hostname et du path
- **Host Rule** : Regle dans le URL Map qui matche un hostname (ex: `site.com`)
- **Path Matcher** : Ensemble de regles de path associe a un host rule
- **Path Rule** : Regle qui matche un pattern de path (ex: `/portfolio/*`)
- **Backend Bucket** : Ressource GCP qui connecte un URL Map a un bucket GCS (avec CDN)
- **SSL Certificate (managed)** : Certificat SSL provisionne et renouvele automatiquement par Google
- **Wildcard SSL** : Certificat qui couvre `*.domain.com` (tous les sous-domaines d'un niveau)
- **Forwarding Rule** : Regle qui associe une IP:port a un target proxy
- **Target HTTPS Proxy** : Proxy qui termine le SSL et route vers le URL Map
- **CDN** : Cache distribue mondialement qui accelere les temps de chargement
- **safe_name** : Fonction qui convertit un nom en format compatible GCP (lowercase, hyphens, 63 chars max)
