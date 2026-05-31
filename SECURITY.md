# Security

Report vulnerabilities privately via GitHub Security Advisories.
Do not file public issues for unpatched holes.

We acknowledge reports within two business days and aim to ship a fix
or mitigation within thirty days for high-severity findings.

## Supported versions

We patch the latest tagged release on `main`. There is no separate LTS
branch today.

## Procurement and audit material

* Threat model: [`docs/security/THREAT_MODEL.md`](docs/security/THREAT_MODEL.md)
* Code ownership: [`.github/CODEOWNERS`](.github/CODEOWNERS)
* Dependency hygiene: [`.github/dependabot.yml`](.github/dependabot.yml)
* Supply-chain checks: `.github/workflows/supply-chain.yml`
* Tenant isolation tests: `tests/integration/test_multi_tenant.py`,
  `tests/integration/test_rbac.py`
