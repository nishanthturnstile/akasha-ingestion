# Phase 1 Secret Management

Phase 1 uses SOPS + age as the default secret workflow.

Rules:

1. Store only encrypted SOPS files in the repo.
2. Store only `secret_ref` in PostgreSQL.
3. Keep the age private key on the target VM outside the repo.
4. Decrypt into runtime-only files or Docker secret mounts during deployment.
5. Never print provider credentials, API keys, authorization headers, signed URLs, cookies, or tokens in logs.
6. Store API keys as hashes with owner/name metadata and rotate by adding a new hash before removing the old one.

The Phase 0 plaintext VM-only env file remains a validation artifact and must not be copied into the repository.
