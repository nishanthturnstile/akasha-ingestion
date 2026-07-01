# Phase 0 Provider Access Validation

This document records Workstream C validation approach and current evidence for provider account and access-flow validation.

## Secret handling

Phase 0 authenticated provider validation uses a VM-only env file:

```text
/srv/akasha/secrets/provider-validation.env
```

Rules:

- Fill values directly on `akasha-staging`; do not paste credentials into chat.
- Do not copy the file into this repository.
- Keep `/srv/akasha/secrets` at mode `700`.
- Keep `/srv/akasha/secrets/provider-validation.env` at mode `600`.
- Validation scripts must read credentials from environment variables, not command-line arguments.
- Evidence logs must redact tokens, passwords, cookies, signed URLs, and authorization headers.

Current VM secret scaffold:

| Path | Mode | Owner |
| --- | --- | --- |
| `/srv/akasha/secrets` | `700` | `akashaadmin` |
| `/srv/akasha/secrets/provider-validation.env` | `600` | `akashaadmin` |
| `/srv/akasha/provider-validation/evidence` | writable by `akashaadmin` | local VM evidence staging |

## Current no-secret validation evidence

These checks validate network reachability and public catalogue discovery only. They do not validate provider credentials, product downloads, checksums, quotas, or staging behavior.

| Provider | No-secret result | Status |
| --- | --- | --- |
| Bhoonidhi/NRSC | `https://bhoonidhi.nrsc.gov.in` returned HTTP `200` from `akasha-staging`; egress IP observed as `20.219.3.35` | Network valid, auth/order/download pending |
| CDSE | Anonymous OData catalogue query returned Sentinel-2 L2A products for the Phase 0 AOI/window locally and from `akasha-staging` | Catalogue valid, OAuth/download/checksum pending |
| USGS/M2M | Anonymous LandsatLook STAC query returned Landsat C2 L2 products for the Phase 0 AOI/window locally and from `akasha-staging` | Catalogue valid, M2M/download/checksum pending |
| Earthdata | URS endpoint reachable locally and from `akasha-staging` | Network valid, auth readiness pending |

Sample products discovered without credentials:

| Provider | Sample IDs/names |
| --- | --- |
| CDSE | `S2B_MSIL2A_20260402T050649_N0512_R019_T43PHQ_20260402T115200.SAFE`, `S2A_MSIL2A_20260402T052251_N0512_R062_T43PGR_20260402T102314.SAFE`, `S2B_MSIL2A_20260402T050649_N0512_R019_T43PGQ_20260402T115200.SAFE` |
| USGS LandsatLook STAC | `LC08_L2SP_143052_20260410_20260416_02_T1_SR`, `LC08_L2SP_143051_20260410_20260416_02_T1_SR`, `LC09_L2SP_144051_20260409_20260410_02_T1_SR` |

## Phase 0 AOI/window used for checks

| Input | Value |
| --- | --- |
| Provider search envelope | `[77.023647, 12.537266, 78.131561, 13.61645]` |
| Clear-season sample window | `2026-01-15` to `2026-04-15` |
| Demo date range | `2026-01-01` to `2026-06-30` |

The authoritative AOI remains the geodesic 60 km polygon in `docs/phase-0/bangalore-aoi.geojson`; the bbox is used here as the provider search envelope.

## Authenticated validation sequence

### 1. CDSE / Sentinel-2 L2A

Run locally first, then repeat from `akasha-staging`.

Required evidence:

- OAuth2 token request succeeds.
- Catalogue search returns Sentinel-2 L2A products for the AOI/window.
- One L2A product or small provider-supported package is downloadable.
- Checksum/integrity metadata is found and verified where available.
- Token expiry and retry behavior are recorded.

Useful endpoints:

| Purpose | Endpoint |
| --- | --- |
| Catalogue OData | `https://catalogue.dataspace.copernicus.eu/odata/v1/Products` |
| Token | `https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token` |
| Product download | `https://download.dataspace.copernicus.eu/odata/v1/Products(<id>)/$value` |

Expected credential variables:

```text
CDSE_USERNAME
CDSE_PASSWORD
```

### 2. USGS/M2M / Landsat 8/9 C2 L2

Run locally first, then repeat from `akasha-staging`.

Required evidence:

- M2M API token works for the account.
- Scene search returns Landsat 8/9 Collection 2 Level 2 products for the AOI/window.
- QA_PIXEL asset access is confirmed.
- Download options/request flow is documented.
- Checksum or fallback integrity behavior is recorded.
- Link expiry and quota/rate behavior are recorded.

Useful endpoints:

| Purpose | Endpoint |
| --- | --- |
| M2M API root | `https://m2m.cr.usgs.gov/api/api/json/stable/` |
| Anonymous STAC search | `https://landsatlook.usgs.gov/stac-server/search` |

Expected credential variable:

```text
USGS_M2M_TOKEN
```

USGS M2M access may require explicit approval for the account before API validation can pass.

### 3. Earthdata / future MODIS readiness

Run locally first, then repeat from `akasha-staging`.

Required evidence:

- Earthdata Login credentials or token are valid.
- A protected Earthdata/DAAC endpoint can be accessed without exposing credentials.
- Any future MODIS access constraints are documented.

Expected credential variables:

```text
EARTHDATA_USERNAME
EARTHDATA_PASSWORD
```

Earthdata is not an MVP field-analytics source, so auth readiness is enough for Phase 0.

### 4. Bhoonidhi/NRSC / ResourceSat-2A

Run from `akasha-staging` only.

Required evidence:

- Provider login/API access succeeds from egress IP `20.219.3.35`.
- ResourceSat-2A search returns LISS-4, LISS-3, and/or AWiFS candidates for the AOI/window.
- Ordering/staging behavior is documented.
- Download link expiry is measured or documented.
- Checksum support is confirmed or a fallback integrity policy is recorded.
- Product licensing/exposure constraints are captured.

Expected credential variables:

```text
BHOONIDHI_USERNAME
BHOONIDHI_PASSWORD
BHOONIDHI_API_KEY
```

The actual required variables depend on the Bhoonidhi/NRSC account method.

## Provider-access matrix status rules

Use these status values during Phase 0:

| Status | Meaning |
| --- | --- |
| `pending_local_validation` | No local validation completed yet |
| `network_validated_auth_pending` | Network/auth surface reachable, credentials or auth validation pending |
| `catalogue_validated_auth_pending` | Public catalogue/search works, authenticated provider flow pending |
| `validated_local` | Auth/search/download evidence captured locally, VM repeat still pending |
| `validated_from_vm` | Auth/search/download evidence captured on VM |
| `validated_complete` | Required local and VM validations are complete for that provider |
| `blocked_on_credentials` | Required credentials/token/account access unavailable |
| `blocked_on_provider` | Provider service, whitelist, account approval, or product access blocks validation |

## Decisions still required

Authenticated validation cannot proceed until the appropriate provider credentials/tokens are entered on the VM or made available interactively by the operator.

Current credential availability:

| Provider | Credential status | Next action |
| --- | --- | --- |
| Bhoonidhi/NRSC | Available manually | Enter into `/srv/akasha/secrets/provider-validation.env` on `akasha-staging`, then run VM-only auth/order/download validation |
| CDSE | Not yet created/provided | Create Copernicus Data Space account or provide approved credential path |
| USGS/M2M | Not yet created/provided | Create USGS account, request/confirm M2M API access, and provide application token |
| Earthdata | Not yet created/provided | Create Earthdata Login account or provide approved credential path |

Do not proceed to sample downloads for a provider until its Workstream C row is at least `validated_local` for local-first sources or `validated_from_vm` for Bhoonidhi/NRSC.

