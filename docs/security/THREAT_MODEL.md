# Threat Model

Owner: Sanjay Santhanam (@Sanjays2402)
Last reviewed: 2026-05-31
Scope: clawhum API service, Next.js dashboard, indexer worker, and the
data they hold (humming clips, embeddings, workspace metadata, audit
log, API key material).

This document exists so that a procurement security review can be
completed without reverse-engineering the codebase. It is updated when
new attack surface is introduced, and at minimum every six months.

## 1. System overview

Components in scope:

| Component | Stack | Trust boundary |
|---|---|---|
| Public API | FastAPI, Python 3.11+ | Internet ingress |
| Indexer worker | Python, faiss/HNSW | Internal only, reads same store |
| Web dashboard | Next.js 15 App Router | Browser to API |
| Persistent store | JSONL append-only files under `data/` | Disk on the API host |
| Outbound webhooks | API service to customer URL | Outbound egress |

Stored data classes:

* **Workspace metadata** (display name, plan, allowlists, SSO config).
* **Members** (email, role assignment, MFA enrolment, recovery codes).
* **API keys / PATs** (stored as hash plus prefix; never plaintext).
* **Audit log entries** (actor, action, target, IP, before/after diff).
* **Humming clips and embeddings** (treated as customer content).
* **Webhook delivery log** (target URL, status, body hash).

Out of scope: developer laptops, end-user devices, the upstream CLAP
model weights download host.

## 2. Trust boundaries

```
  Internet  ->  [TLS edge]  ->  FastAPI app  ->  Disk
                                     |
                                     +-->  Outbound webhook target
                                     +-->  OpenTelemetry collector
```

The TLS edge is operator-supplied (nginx, Cloudflare, fly proxy). The
app assumes it sits behind exactly one trusted hop and reads
`X-Forwarded-For[0]` for client IP attribution. Operators terminating
TLS further out must strip untrusted XFF headers at the edge or set
`ip_allowlist_enabled=false` and gate at the edge instead.

## 3. STRIDE walkthrough

### Spoofing
* **Threat:** attacker forges API requests as another tenant.
  * Mitigation: every authenticated request resolves a `tenant_id`
    from the API key or PAT record; the value lives on `request.state`
    and is the only source of truth used by data layer queries.
  * Mitigation: PAT secrets are hashed at rest; only the prefix and
    last-used timestamp are queryable.
* **Threat:** attacker spoofs webhook source.
  * Mitigation: every outbound webhook is signed with a per-hook HMAC
    secret. Replay protection: signature includes a timestamp; clients
    are documented to reject deliveries older than 5 minutes.

### Tampering
* **Threat:** audit log entries are deleted or rewritten to hide an
  attack.
  * Mitigation: audit log is JSONL append-only. Entries include a
    monotonic sequence id and a content hash chained from the prior
    entry. The export endpoint surfaces the chain so an auditor can
    detect gaps.
* **Threat:** an attacker modifies a stored embedding to poison
  search results.
  * Mitigation: the indexer reads from the same canonical store the
    API writes to; rebuilds are deterministic; any change is visible
    in the audit log because uploads go through the mutating API.

### Repudiation
* **Threat:** "I never ran that delete." Workspace owner denies an
  action.
  * Mitigation: every mutating route writes to the audit log with the
    actor identifier (api key name or `pat:<name>`), source IP, MFA
    used flag, request id, and a before/after diff for updates.
  * Mitigation: admin step-up actions require MFA when the actor has
    enrolled, recorded as `mfa_used=true` in the entry.

### Information disclosure
* **Threat:** cross-tenant data leak via a forgotten query scope.
  * Mitigation: queries take `tenant_id` as a required argument; the
    integration suite includes `test_multi_tenant.py` and
    `test_rbac.py` that prove a viewer in tenant A receives 404 (not
    403) for tenant B resources. New routes must extend these tests.
* **Threat:** secrets in logs.
  * Mitigation: structured logging strips `authorization`,
    `x-api-key`, `x-mfa-code` headers in middleware; webhook bodies
    are hashed not logged in the delivery log.
* **Threat:** SSRF via outbound webhooks targeting internal IPs.
  * Mitigation: `webhook_safety.py` resolves the target host and
    rejects RFC1918, loopback, link-local, multicast, and metadata
    service ranges before connecting. `test_webhook_ssrf.py` covers
    the common bypass tricks (DNS rebinding via repeated lookups,
    IPv6 mapped addresses, 0.0.0.0).

### Denial of service
* **Threat:** a single workspace exhausts CPU on the search path.
  * Mitigation: per-workspace plan quotas (RPM + daily) enforced in
    middleware; 429 responses carry `Retry-After` and the
    `X-RateLimit-*` family of headers so clients can back off.
  * Mitigation: per-PAT rate limits override the workspace limit
    downward when set.
* **Threat:** unbounded upload size.
  * Mitigation: FastAPI body size limit enforced at the ingress proxy;
    audio decoder bounds duration before resampling.

### Elevation of privilege
* **Threat:** viewer escalates to admin via a misconfigured PAT.
  * Mitigation: PAT scopes are an intersection with the minting
    actor's role set; a viewer-minted PAT cannot grant write or admin
    scopes. The `require_scopes` dependency enforces the call-site
    requirement at runtime.
* **Threat:** stolen long-lived admin key.
  * Mitigation: keys are rotatable in place with a configurable grace
    window; revocation is immediate; `last_used_at` surfaces unused
    keys for cleanup. Destructive admin actions step up to MFA when
    the actor has enrolled.

## 4. Top risks and accepted residual risk

1. **JSONL persistence is single-host.** Suitable for the current
   self-hosted deployment story. A managed multi-region offering must
   move to Postgres with row-level security before GA; the abstraction
   already lives behind `tenant.py` so the call sites do not change.
2. **No customer-managed encryption keys yet.** Disk-at-rest
   encryption is the operator's responsibility (full-disk encryption
   or cloud-volume encryption). CMK / BYOK is a roadmap item; data
   residency hint is exposed today.
3. **Inbound IP allowlist relies on a single trusted proxy hop.**
   Documented in section 2. Operators with deeper proxy chains must
   either trim XFF at the edge or disable the feature and gate at the
   edge.

## 5. Continuous controls

| Control | How |
|---|---|
| Dependency CVEs | `.github/dependabot.yml` weekly + security advisories |
| Static analysis | `ruff`, `mypy` in `.github/workflows/ci.yml` |
| Supply chain | `.github/workflows/supply-chain.yml` (SBOM + audit) |
| Secret scanning | GitHub secret scanning enabled at repo level |
| Code review | `.github/CODEOWNERS` requires owner sign-off on security paths |
| Backup of audit log | Operator runbook in `docs/runbooks/` |

## 6. Reporting a vulnerability

See `SECURITY.md`. Private disclosure preferred; we acknowledge within
two business days.
