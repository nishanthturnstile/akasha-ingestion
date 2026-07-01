# Satellite Catalog & Selection Guide

> Source of truth: [CIDSA Satellite Finder](https://cidsaglobal.com/akasha/satellite-finder) — captured and consolidated for the `agri-app` project.
> Scope: 20 satellite platforms / sensors that the CIDSA agri-remote-sensing team has vetted for precision-agriculture, crop-monitoring and land-analytics workflows across India.

---

## How to use this document

1. **Need a quick "which satellite for X?" answer →** jump to [§3 Decision matrices](#3-decision-matrices).
2. **Comparing two or three candidates side-by-side →** use the [§2 Master comparison table](#2-master-comparison-table).
3. **Need every spec for one platform →** see the [§4 Detailed catalog](#4-detailed-catalog) entry.
4. **Not sure where to start →** read the [§5 Recipe playbook](#5-recipe-playbook--common-scenarios) for canonical CIDSA workflows.
5. **Want to know what each spectral band covers (in nanometres) →** see the [§7 Spectral band wavelength reference](#7-spectral-band-wavelength-reference).
6. **Need to jump straight into a satellite's archive search →** use the deep-links in [§8 Slug & archive deep-link reference](#8-slug--archive-deep-link-reference).

Legend:

| Symbol | Meaning |
|--------|---------|
| 🟢 Live | Currently operational, data flowing |
| 🟡 Upcoming | Launched / in commissioning |
| ⚪ Archive | Decommissioned — historical only |
| 🇮🇳 | ISRO mission |
| 💼 | Commercial (paid tasking) |
| 🆓 | Open / free archive |

---

## 1. Inventory at a glance

20 platforms grouped by **primary data type**. Click any name to jump to its detailed entry.

### Optical (very-high-resolution / sub-metre)

| Satellite | Provider | Resolution | Revisit | Status |
|-----------|----------|-----------|---------|--------|
| [Cartosat-3](#cartosat-3) 🇮🇳 | ISRO | 0.25 m | 4–5 d | 🟢 Live |
| [SuperView NEO-1](#superview-neo-1) | SIIS (China) 💼 | 0.3 m | Daily | 🟢 Live |
| [BlackSky Gen 3](#blacksky-gen-3) | BlackSky 💼 | 0.35 m | 15× / day | 🟢 Live |
| [KOMPSAT-3A](#kompsat-3a) | KARI / SIIS 💼 | 0.4 m | 1.5 d | 🟢 Live |
| [SkySat](#skysat) | Planet Labs 💼 | 0.5 m | Multiple / day | 🟢 Live |

### Multispectral (medium-resolution agronomic baseline)

| Satellite | Provider | Resolution | Revisit | Status |
|-----------|----------|-----------|---------|--------|
| [PlanetScope](#planetscope) | Planet Labs 💼 | 3–5 m | Daily | 🟢 Live |
| [ResourceSat-2A](#resourcesat-2a) 🇮🇳 | ISRO 🆓 | 5.0 m native LISS-4 scenes / 5.8 m composites | 5 d | 🟢 Live |
| [Sentinel-2](#sentinel-2) | ESA 🆓 | 10 m | 2–5 d | 🟢 Live |
| [Landsat 9](#landsat-9) | NASA / USGS 🆓 | 15 m | 16 d | 🟢 Live |
| [Landsat 8](#landsat-8) | NASA / USGS 🆓 | 15–30 m | 16 d | 🟢 Live |
| [MODIS (Terra / Aqua)](#modis-terra--aqua) | NASA 🆓 | 250 m | Daily | 🟢 Live |
| [EOS-06 (OceanSat-3)](#eos-06-oceansat-3) 🇮🇳 | ISRO 🆓 | 360 m | 2 d | 🟢 Live |

### Radar / SAR (cloud-piercing, all-weather)

| Satellite | Provider | Resolution | Revisit | Band | Status |
|-----------|----------|-----------|---------|------|--------|
| [Sentinel-1](#sentinel-1) | ESA 🆓 | 20 m | 6 d | C | 🟢 Live |
| [ALOS-2 (PALSAR-2)](#alos-2-palsar-2) | JAXA | 3–10 m | 14 d | L | 🟢 Live |
| [EOS-04 (RISAT)](#eos-04-risat) 🇮🇳 | ISRO 🆓 | 1–50 m | 12 d | C | 🟢 Live |
| [NISAR](#nisar) 🇮🇳 | ISRO / NASA 🆓 | 3–10 m | 12 d | L + S | 🟢 Live (30 Jul 2025) |

### Aerial / UAV reference

| Platform | Provider | Resolution | Revisit | Coverage | Status |
|----------|----------|-----------|---------|----------|--------|
| [NAIP (USDA Aerial)](#naip-usda-aerial) | USDA 🆓 | 1 m | Yearly | USA-only | 🟢 Live |

### Historical archives (no new acquisitions)

| Satellite | Provider | Resolution | Revisit | Era |
|-----------|----------|-----------|---------|-----|
| [Landsat 7](#landsat-7) | NASA / USGS 🆓 | 30 m | 16 d | 1999 – 2024 ⚪ |
| [Landsat 5](#landsat-5) | NASA / USGS 🆓 | 30–60 m | 16 d | 1984 – 2013 ⚪ |
| [IRS-1C](#irs-1c) 🇮🇳 | ISRO | 5.8 m | 24 d | 1995 – 2007 ⚪ |

---

## 2. Master comparison table

Sort mentally by whichever column matters for your task. All numbers are nominal best-mode values — multi-mode SARs carry resolution / swath ranges.

| # | Satellite | Type | Provider | Resolution | Revisit | Swath | Bands | Since | Cost model |
|---|-----------|------|----------|-----------|---------|-------|-------|------:|------------|
| 1 | Cartosat-3 🇮🇳 | Optical | ISRO | **0.25 m** | 4–5 d | 16 km | Pan, B, G, R, NIR | 2019 | Tasking + archive (NRSC) |
| 2 | SuperView NEO-1 | Optical | SIIS (China) | 0.3 m | Daily | 12 km | Pan, B, G, R, NIR | 2022 | 💼 Commercial |
| 3 | BlackSky Gen 3 | Optical | BlackSky | 0.35 m | **15× / day** | 5 km | Pan, B, G, R, NIR | 2023 | 💼 Commercial |
| 4 | KOMPSAT-3A | Optical | KARI / SIIS | 0.4 m | 1.5 d | 13 km | Pan, B, G, R, NIR + MWIR | 2015 | 💼 Commercial |
| 5 | SkySat | Optical | Planet Labs | 0.5 m | Multiple / day | 8 km | Pan, B, G, R, NIR | 2013 | 💼 Commercial |
| 6 | PlanetScope | Multispectral | Planet Labs | 3–5 m | Daily | 24 km | B, G, R, RE, NIR | 2016 | 💼 Commercial |
| 7 | ResourceSat-2A 🇮🇳 | Multispectral | ISRO | 5.0 m native LISS-4 scenes / 5.8 m composites | 5 d | 70 km | G, R, NIR, SWIR | 2016 | 🆓 Open (NRSC) |
| 8 | Sentinel-2 | Multispectral | ESA | 10 m | 2–5 d | **290 km** | Coastal, B, G, R, RE, NIR, SWIR | 2017 | 🆓 Open (Copernicus) |
| 9 | Landsat 9 | Optical | NASA / USGS | 15 m | 16 d | 185 km | Coastal, B, G, R, NIR, SWIR, Pan, Thermal | 2021 | 🆓 Open (USGS) |
| 10 | Landsat 8 | Optical | NASA / USGS | 15–30 m | 16 d | 185 km | Coastal, B, G, R, NIR, SWIR, Pan, Thermal | 2013 | 🆓 Open (USGS) |
| 11 | MODIS (T/A) | Multispectral | NASA | 250 m | **Daily** | **2330 km** | B, G, R, NIR, SWIR, Thermal | 2000 | 🆓 Open |
| 12 | EOS-06 (OceanSat-3) 🇮🇳 | Multispectral | ISRO | 360 m | 2 d | 1440 km | B, G, R, NIR, SWIR | 2022 | 🆓 Open (NRSC) |
| 13 | Sentinel-1 | Radar (SAR) | ESA | 20 m | 6 d | 250 km | C-band | 2014 | 🆓 Open (Copernicus) |
| 14 | ALOS-2 (PALSAR-2) | Radar (SAR) | JAXA | 3–10 m | 14 d | 50–70 km | L-band | 2014 | 💼 Mostly commercial |
| 15 | EOS-04 (RISAT) 🇮🇳 | Radar (SAR) | ISRO | 1–50 m | 12 d | 25–223 km | C-band | 2022 | 🆓 Limited open / NRSC |
| 16 | NISAR 🇮🇳 | Radar (SAR) | ISRO + NASA | 3–10 m | 12 d | 240 km | **L + S band** | 2025 | 🆓 Open (NRSC / NASA Earthdata) |
| 17 | NAIP | Aerial / UAV | USDA | 1 m | Yearly | n/a | B, G, R, NIR | 2003 | 🆓 Open (USA only) |
| 18 | Landsat 7 ⚪ | Optical | NASA / USGS | 30 m | 16 d | 185 km | B, G, R, NIR, SWIR, Pan, Thermal | 1999 – 2024 | 🆓 Open archive |
| 19 | Landsat 5 ⚪ | Optical | NASA / USGS | 30–60 m | 16 d | 185 km | B, G, R, NIR, SWIR, Thermal | 1984 – 2013 | 🆓 Open archive |
| 20 | IRS-1C ⚪ 🇮🇳 | Multispectral | ISRO | 5.8 m | 24 d | 70 km | Pan, G, R, NIR, SWIR | 1995 – 2007 | 🆓 Open archive |

> **Reading the resolution column:** smaller-is-better — 0.25 m means each pixel covers 25 cm on the ground. 250 m means a pixel spans more than two football fields.
> **Reading the revisit column:** smaller-is-better — "Daily" beats "16 d" for time-series freshness, but 16-day platforms cover a much larger swath per pass.

---

## 3. Decision matrices

### 3.1 By crop (Indian agriculture defaults from CIDSA)

| Crop | Recommended use cases | Tier-1 satellite | Tier-2 / supporting | Why |
|------|----------------------|------------------|---------------------|-----|
| **Rice** | Flood/Disaster Response, Crop Health Monitoring, Yield Forecasting | **Sentinel-1** (cloud-proof SAR) | Sentinel-2 + ALOS-2 (L-band for paddy phenology) | Monsoon paddy is under cloud most of kharif — only SAR penetrates. |
| **Wheat** | Crop Health Monitoring, Yield Forecasting, Soil Mapping | **Sentinel-2** | Landsat 8/9, ResourceSat-2A | Rabi season is largely cloud-free; 5-day red-edge cadence catches every phenophase. |
| **Cotton** | Crop Health Monitoring, Irrigation Management, Soil Mapping | **PlanetScope** (daily 3–5 m) | Sentinel-2, Sentinel-1 (irrigation moisture) | Long-duration crop with subtle stress signatures — daily NDVI is gold. |
| **Sugarcane** | Crop Health Monitoring, Yield Forecasting, Change Detection | **Landsat 8 + 9 paired** (8-day effective) | ResourceSat-2A, PlanetScope | 12–14 month cycle benefits from biweekly continuity + thermal bands for stress. |
| **Maize** | Crop Health Monitoring, Yield Forecasting | **Sentinel-2** | PlanetScope, MODIS for regional yield | Short kharif window; 5-day Sentinel-2 brackets vegetative → tasseling → grain-fill. |
| **Pulses** | Crop Health Monitoring, Soil Mapping | **Sentinel-2** | ResourceSat-2A, Sentinel-1 (soil moisture) | Rainfed crop in semi-arid belts; multispectral + SAR soil moisture covers both axes. |

### 3.2 By use case (the canonical mapping)

| Use case | Best free option | Best premium option | Why |
|----------|------------------|---------------------|-----|
| **Crop Health Monitoring (NDVI / NDRE)** | Sentinel-2 (red-edge bands) | PlanetScope (daily) | Red-edge bands isolate chlorophyll stress before it shows in standard NDVI. |
| **Soil Mapping** | Sentinel-1 (C-band SAR) + ResourceSat-2A | NISAR (L-band; primary recommendation post-launch as analysis-ready products ship); ALOS-2 (L-band) remains the production fallback in the interim | SAR backscatter is sensitive to soil moisture and roughness. |
| **Irrigation Management** | Sentinel-1 (moisture) + Sentinel-2 (NDWI) | PlanetScope (daily) | SAR detects irrigation events through clouds; NDWI tracks canopy water. |
| **Flood / Disaster Response** | **Sentinel-1** (6-day, all-weather) | BlackSky Gen 3 (15× / day) | SAR penetrates cyclone clouds — Sentinel-1 is the global default; BlackSky for hour-scale optical confirmation. |
| **Yield Forecasting** | Sentinel-2 + Landsat 8/9 + MODIS | PlanetScope (commercial) | Combine 10 m phenology, 30 m thermal stress, and 250 m daily phenology curves. |
| **Change Detection** | Sentinel-2, Landsat archive (40 yr) | Cartosat-3, BlackSky | Free Landsat for decadal trends; sub-metre tasking for parcel-level disputes. |
| **Carbon & Biomass Estimation** | **NISAR (primary, post-launch ARD ship; L+S-band)** with **ALOS-2** as today's production fallback (L-band penetrates canopy) | — | L-band SAR is the only option to see through tropical canopy. |

### 3.3 By spatial resolution bucket

| Bucket | Definition | Satellites | Typical agri use |
|--------|-----------|------------|------------------|
| **Sub-metre (< 1 m)** | Very high — can see individual trees / vehicles | Cartosat-3, SuperView NEO-1, BlackSky Gen 3, KOMPSAT-3A, SkySat | Field-boundary digitisation, orchard inventory, claim verification |
| **High (1 – 4 m)** | Single-row crops resolvable | NAIP (1 m), PlanetScope (3 m) | Plot-level NDVI, smallholding mapping |
| **Medium (5 – 20 m)** | Field-scale; the agronomic sweet spot | ResourceSat-2A LISS-4 (5.0 m native scenes / 5.8 m composites), Sentinel-2 (10 m), Landsat 9 (15 m), Sentinel-1 (20 m) | NDVI/NDRE time-series, irrigation, FASAL acreage |
| **Regional (> 20 m)** | District- and state-scale phenology | Landsat 8 (30 m), MODIS (250 m), EOS-06 (360 m) | State yield forecasts, drought monitoring, fire mapping |

### 3.4 By revisit cadence

| Cadence | Satellites | Best for |
|---------|------------|----------|
| **Sub-daily (multiple per day)** | BlackSky Gen 3 (15×), SkySat, SuperView NEO-1 | Disaster response, mill scheduling, security |
| **Daily** | PlanetScope, MODIS | High-cadence NDVI, fire / flood, regional phenology |
| **2 – 5 days** | Sentinel-2 (2–5 d), EOS-06 (2 d) | Operational crop health, kharif/rabi tracking |
| **5 – 10 days** | ResourceSat-2A (5 d), Sentinel-1 (6 d) | Routine FASAL, monsoon SAR cadence |
| **10 – 20 days** | Landsat 8/9 (16 d), EOS-04 (12 d), NISAR (12 d), ALOS-2 (14 d) | Decadal change, biomass, soil moisture |
| **> 20 days** | IRS-1C (24 d) | Historical baselines only |

### 3.5 By data type

| Type | When to choose | Limitations | Platforms |
|------|----------------|-------------|-----------|
| **Optical (panchromatic / RGB)** | When sub-metre detail is needed and skies are clear | Useless under clouds, shadows; no spectral diversity | Cartosat-3, SuperView NEO-1, BlackSky, KOMPSAT-3A, SkySat |
| **Multispectral** | NDVI, NDRE, NDWI, EVI, SAVI — anything chlorophyll/water based | Cloud-blocked; needs atmospheric correction | Sentinel-2, Landsat 8/9, PlanetScope, ResourceSat-2A, MODIS, EOS-06 |
| **Radar (SAR)** | Cloud penetration, soil moisture, biomass, flood mapping, night | Speckle noise; harder to interpret; no colour | Sentinel-1, ALOS-2, EOS-04, NISAR |
| **Aerial / UAV** | Ground-truthing, calibration, sub-field experiments | Tiny footprint; expensive at scale | NAIP (USA-only reference) |

### 3.6 By cost & access

| Tier | Platforms | Access |
|------|-----------|--------|
| **🆓 Free open archive** | Sentinel-1, Sentinel-2, Landsat 5/7/8/9, MODIS, NAIP, ResourceSat-2A (NRSC), EOS-04 MRS / CRS modes (NRSC; **note FRS-1 fine modes are *not* free**), EOS-06 (NRSC), IRS-1C, NISAR (open via NRSC + NASA Earthdata, live since 30 Jul 2025) | Copernicus, USGS Earth Explorer, NASA Earthdata, Bhuvan / NRSC ([Bhoonidhi](https://bhoonidhi.nrsc.gov.in)) |
| **🇮🇳 ISRO licensed** | Cartosat-3 (high-res tier) — **free for Government Entities (GE) on declaration; priced via NSIL for Non-Government Entities (NGE)** per Indian Space Policy 2023 | NRSC commercial licence for NGE; Bhoonidhi free track for GE |
| **💼 Commercial tasking** | SuperView NEO-1, BlackSky Gen 3, KOMPSAT-3A, SkySat, PlanetScope, ALOS-2 | Per-km² or subscription |

---

## 4. Detailed catalog

> Each entry follows the same template: status / provider → headline specs → spectral bands → indices → description → Indian agri context.

---

### Sentinel-2

🟢 **Live** · ESA · Open / free (Copernicus)

| Spec | Value |
|------|-------|
| Type | Multispectral / Optical |
| Resolution | **10 m** |
| Revisit | 2 – 5 days |
| Swath | 290 km |
| Archive since | 2017 |
| Acquisition modes | Archive, Tasking |
| Spectral bands | Coastal, Blue, Green, Red, **Red Edge**, NIR, SWIR |
| Indices supported | NDVI, **NDRE**, EVI, NDWI, MSAVI, SAVI |
| Use cases | Crop Health Monitoring, Change Detection, Yield Forecasting, Soil Mapping |

**About.** A Copernicus Programme constellation (Sentinel-2A and 2B) operated by ESA. Provides systematic, global, free multispectral imagery with strong red-edge bands — the **default agronomic baseline at CIDSA** for NDVI, NDWI, MSAVI, EVI and NDRE workflows.

**Indian agri context.** Backbone of CIDSA crop-health workflows for wheat, rice and cotton across Punjab, Haryana, Maharashtra and Andhra Pradesh — its 5-day revisit captures every phenological phase of a kharif or rabi season.

**Ingestion state.** Provider adapter: `cdse`; cadence class: `2_to_5_days`; schedule state: `disabled` (CDSE OAuth2/Keycloak adapter not yet validated); product exposure: `hidden`. Source ID: `sentinel-2-l2a`.

---

### Sentinel-1

🟢 **Live** · ESA · Open / free (Copernicus)

| Spec | Value |
|------|-------|
| Type | Radar (SAR) — C-band |
| Resolution | 20 m |
| Revisit | 6 days |
| Swath | 250 km |
| Archive since | 2014 |
| Acquisition modes | Archive, Tasking |
| Spectral bands | C-band |
| Indices supported | RVI, VV/VH ratio, σ⁰ backscatter |
| Use cases | Flood / Disaster Response, Soil Mapping, Irrigation Management, Crop Health Monitoring |

**About.** A C-band Synthetic Aperture Radar (SAR) constellation. Cuts through clouds and works at night, making it the workhorse for monsoon-season monitoring where optical sensors fall behind.

**Indian agri context.** Critical for monsoon paddy mapping in West Bengal, Odisha and Tamil Nadu — Sentinel-1 holds the cadence even under unbroken cloud cover, enabling near-real-time flood-extent mapping during cyclones.

**Ingestion state.** Provider adapter: `cdse`; cadence class: `5_to_10_days`; schedule state: `disabled` (SAR backscatter validation profile not yet implemented; no optical indices per GEO-002); product exposure: `hidden`. Source ID: `sentinel-1-grd`.

---

### Landsat 8

🟢 **Live** · NASA / USGS · Open / free (USGS Earth Explorer)

| Spec | Value |
|------|-------|
| Type | Optical / Multispectral |
| Resolution | 15 – 30 m (15 m Pan, 30 m multispectral) |
| Revisit | 16 days |
| Swath | 185 km |
| Archive since | 2013 |
| Acquisition modes | Archive |
| Spectral bands | Coastal, Blue, Green, Red, NIR, SWIR, Pan, **Thermal (TIRS)** |
| Indices supported | NDVI, EVI, NDWI, NBR, **LST** |
| Use cases | Yield Forecasting, Change Detection, Carbon & Biomass Estimation |

**About.** USGS / NASA flagship in the longest-running Earth-observation programme. The 30 m grid plus thermal bands (TIRS) make it ideal for long-term land-use change studies and surface-temperature mapping.

**Indian agri context.** Used to build decadal cropping-pattern baselines for the Indo-Gangetic Plain (IGP) region and for surface-temperature maps that flag heat-stress events in rabi wheat.

**Ingestion state.** Provider adapter: `usgs`; cadence class: `10_to_20_days`; schedule state: `disabled` (USGS STAC+COG adapter not yet implemented; cloud-native COG path preferred); product exposure: `hidden`. Source ID: `landsat-8-c2-l2`.

---

### Landsat 9

🟢 **Live** · NASA / USGS · Open / free (USGS Earth Explorer)

| Spec | Value |
|------|-------|
| Type | Optical / Multispectral |
| Resolution | 15 m (Pan), 30 m (multispectral) |
| Revisit | 16 days |
| Swath | 185 km |
| Archive since | 2021 |
| Acquisition modes | Archive |
| Spectral bands | Coastal, Blue, Green, Red, NIR, SWIR, Pan, Thermal |
| Indices supported | NDVI, EVI, NDWI, NBR, LST |
| Use cases | Crop Health Monitoring, Change Detection |

**About.** Landsat-9 is in orbit 8 days offset from Landsat-8. Together they halve the effective revisit time and continue the Landsat data continuum into the 2030s with improved 14-bit OLI-2 radiometric depth.

**Indian agri context.** Pairs with Landsat-8 to give an effective **8-day revisit** over Indian agricultural districts — useful for tracking sugarcane and orchard crops where 16 days is too coarse.

**Ingestion state.** Provider adapter: `usgs`; cadence class: `10_to_20_days`; schedule state: `disabled` (USGS STAC+COG adapter not yet implemented; multi-source 8-day merge not yet designed); product exposure: `hidden`. Source ID: `landsat-9-c2-l2`.

---

### MODIS (Terra / Aqua)

🟢 **Live** · NASA · Open / free

| Spec | Value |
|------|-------|
| Type | Multispectral / Optical |
| Resolution | 250 m |
| Revisit | **Daily** |
| Swath | **2330 km** |
| Archive since | 2000 |
| Acquisition modes | Archive |
| Spectral bands | Blue, Green, Red, NIR, SWIR, Thermal |
| Indices supported | NDVI, EVI, NDSI, LST, Active Fire |
| Use cases | Yield Forecasting, Change Detection, Flood / Disaster Response, Carbon & Biomass Estimation |

**About.** Two-decade daily-cadence record at moderate resolution. Go-to source for regional and continental-scale phenology, vegetation indices, active-fire mapping and snow-cover.

**Indian agri context.** Drives **state-level kharif / rabi yield forecasts** and **stubble-burn mapping** across Punjab and Haryana every winter.

**Ingestion state.** Provider adapter: `earthdata`; cadence class: `daily` (16-day composites MOD13Q1); schedule state: `disabled` (Earthdata token adapter not yet implemented; context raster profile required per GEO-003); product exposure: `hidden`. Source ID: `modis-13q1-061`.

---

### ResourceSat-2A

🟢 **Live** · 🇮🇳 ISRO · Open / NRSC

| Spec | Value |
|------|-------|
| Type | Multispectral |
| Resolution | 5.0 m native scenes and 5.8 m operational composites (LISS-4); 23.5 m (LISS-3); 56 m (AWiFS) |
| Revisit | 5 days |
| Swath | 70 km |
| Archive since | 2016 |
| Acquisition modes | Archive |
| Spectral bands | Green, Red, NIR, SWIR |
| Indices supported | NDVI, NDWI, SAVI |
| Use cases | Crop Health Monitoring, Soil Mapping, Yield Forecasting |

**About.** ISRO Cartosat-class multispectral mission carrying LISS-3 + LISS-4 + AWiFS instruments — optimised for Indian agricultural landscapes.

**Indian agri context.** ISRO's mainstay for the Department of Agriculture's **national crop-area assessment programme (FASAL)**. Extensively used for sugarcane and horticulture mapping in Maharashtra and Karnataka.

**Ingestion state.** Provider adapter: `bhoonidhi`; cadence class: `5_to_10_days`; three active source rows — **LISS-3** (`resourcesat-2a-liss3-boa`): schedule state `routine`, product exposure `product_active` (MVP baseline; optical composite, 95% coverage threshold); **LISS-4** (`resourcesat-2a-liss4-mx70-l2`): schedule state `routine`, product exposure `product_active` (field enhancement; narrow-swath); **AWiFS** (`resourcesat-2a-awifs-boa`): schedule state `routine`, product exposure `product_active` for regional/coarse analytics with a 60% minimum usable-coverage threshold. All rows: staging_bhoonidhi host pool.

---

### Cartosat-3

🟢 **Live** · 🇮🇳 ISRO · **Free for GE on declaration / NRSC commercial licence (priced via NSIL) for NGE** per Indian Space Policy 2023 — tasking + archive

| Spec | Value |
|------|-------|
| Type | Optical |
| Resolution | **0.25 m** (panchromatic) |
| Revisit | 4 – 5 days |
| Swath | 16 km |
| Archive since | 2019 |
| Acquisition modes | Archive, Tasking |
| Spectral bands | Pan, Blue, Green, Red, NIR |
| Indices supported | Pan-sharpened NDVI |
| Use cases | Crop Health Monitoring, Change Detection |

**About.** ISRO's third-generation Cartosat — sub-25 cm resolution puts it in the same class as Maxar WorldView and Pleiades NEO.

**Indian agri context.** Powers **field-boundary digitisation for high-value horticulture** (mango, grape, pomegranate) at sub-metre accuracy across Maharashtra and Andhra Pradesh.

**Ingestion state.** Provider adapter: `vendor` (manual); cadence class: `5_to_10_days`; schedule state: `manual_only` (no programmatic Bhoonidhi catalogue path confirmed; GE entities via Bhoonidhi declaration, NGE via NSIL licence; VHR visual validation profile required); product exposure: `hidden`. Source ID: `cartosat-3-gated`.

---

### EOS-04 (RISAT)

🟢 **Live** · 🇮🇳 ISRO · NRSC — **MRS / CRS modes free; FRS-1 fine modes are *not* free** (priced licence + tasking)

| Spec | Value |
|------|-------|
| Type | Radar (SAR) — C-band |
| Resolution | 1 – 50 m (mode-dependent) |
| Revisit | 12 days |
| Swath | 25 – 223 km |
| Archive since | 2022 |
| Acquisition modes | Archive, Tasking; Stripmap, FRS, MRS, CRS |
| Spectral bands | C-band |
| Indices supported | σ⁰ backscatter, Coherence |
| Use cases | Flood / Disaster Response, Soil Mapping, Crop Health Monitoring |

**About.** C-band SAR replacement for RISAT-1. Operates day-and-night, all-weather, with multiple acquisition modes (Stripmap, FRS, MRS, CRS).

**Indian agri context.** ISRO's primary tool for **monsoon paddy acreage estimation** and rapid flood-damage assessment under MHA / NDMA disaster-response protocols.

**Ingestion state.** Provider adapter: `bhoonidhi`; cadence class: `10_to_20_days`; schedule state: `disabled` (SAR backscatter validation profile not yet implemented; MRS/CRS modes only; GEO-002 — no optical indices); product exposure: `hidden`. Source ID: `eos-04-sar-mrs-l2b`.

---

### EOS-06 (OceanSat-3)

🟢 **Live** · 🇮🇳 ISRO · Open / NRSC

| Spec | Value |
|------|-------|
| Type | Multispectral |
| Resolution | 360 m |
| Revisit | 2 days |
| Swath | 1440 km |
| Archive since | 2022 |
| Acquisition modes | Archive |
| Spectral bands | Blue, Green, Red, NIR, SWIR |
| Indices supported | Chlorophyll-a, NDVI |
| Use cases | Carbon & Biomass Estimation, Yield Forecasting |

**About.** OceanSat-3 carries OCM-3 (Ocean Colour Monitor), SSTM (sea-surface temperature), Ku-band scatterometer and ARGOS data-collection payloads.

**Indian agri context.** Coastal-agriculture intelligence for **Tamil Nadu, Andhra Pradesh and Gujarat** — chlorophyll concentrations support fisheries forecasting alongside coastal-paddy monitoring.

**Ingestion state.** Provider adapter: `bhoonidhi`; cadence class: `2_to_5_days`; schedule state: `disabled` (precomputed NDVI context only per GEO-003; context raster validation profile required; not field-level statistics); product exposure: `hidden`. Source ID: `eos-06-ocm-lac-ndvi-8day-360m`.

---

### ALOS-2 (PALSAR-2)

🟢 **Live** · JAXA · Mostly commercial

| Spec | Value |
|------|-------|
| Type | Radar (SAR) — **L-band** |
| Resolution | 3 – 10 m |
| Revisit | 14 days |
| Swath | 50 – 70 km |
| Archive since | 2014 |
| Acquisition modes | Archive, Tasking |
| Spectral bands | L-band |
| Indices supported | σ⁰ backscatter, Polarimetric decomposition |
| Use cases | Flood / Disaster Response, Carbon & Biomass Estimation, Soil Mapping |

**About.** L-band SAR — penetrates vegetation canopy better than C-band, making it the **best open option for forest biomass** and rice paddy phenology.

**Indian agri context.** Used for forest-biomass and rice paddy phenology in the **Eastern Ghats and Western Ghats** biospheres.

**Ingestion state.** Provider adapter: `jaxa`; two source rows — commercial scenes (`alos2-palsar2`): schedule state `disabled`, commercial state `commercial_blocked` (paid scenes require JAXA/reseller subscription; SRC-005); free annual mosaic (`alos2-mosaic-25m`): cadence class `archive_on_demand`, schedule state `disabled` (fetch adapter not yet implemented; regional SAR context only). Both rows: product exposure `hidden`; SAR backscatter validation profile; GEO-002 applies.

---

### SuperView NEO-1

🟢 **Live** · SIIS (China) · 💼 Commercial

| Spec | Value |
|------|-------|
| Type | Optical |
| Resolution | 0.3 m |
| Revisit | Daily |
| Swath | 12 km |
| Archive since | 2022 |
| Acquisition modes | Archive, Tasking |
| Spectral bands | Pan, Blue, Green, Red, NIR |
| Indices supported | Pan-sharpened NDVI |
| Use cases | Change Detection, Crop Health Monitoring |

**About.** Sub-30 cm commercial constellation with daily revisit — useful for fast-cadence operational monitoring.

**Indian agri context.** Suited to high-value crops (**vineyards, polyhouses**) where sub-metre detail and daily revisit justify the commercial cost.

**Ingestion state.** Provider adapter: `vendor`; cadence class: `multiple_per_day`; schedule state: `disabled`; commercial state: `commercial_blocked` (no vendor contract or quota; paid tasking disabled by default per SRC-005/SEC-007); product exposure: `hidden`. Source ID: `superview-neo-1`.

---

### PlanetScope

🟢 **Live** · Planet Labs · 💼 Commercial

| Spec | Value |
|------|-------|
| Type | Multispectral |
| Resolution | 3 – 5 m |
| Revisit | **Daily** |
| Swath | 24 km |
| Archive since | 2016 |
| Acquisition modes | Archive, Tasking |
| Spectral bands | Blue, Green, Red, **Red Edge**, NIR |
| Indices supported | NDVI, NDRE, GNDVI |
| Use cases | Crop Health Monitoring, Change Detection, Yield Forecasting |

**About.** Planet's **Dove constellation** — the largest commercial Earth-imaging fleet ever launched, with daily revisit at 3 m resolution.

**Indian agri context.** Daily NDVI time-series across Indian agri-export plots — **strawberry, table-grape and pomegranate** orchards rely on this cadence for stress detection.

**Ingestion state.** Provider adapter: `planet`; cadence class: `daily`; schedule state: `disabled`; commercial state: `commercial_blocked` (no Planet API subscription; search-only until contract/quota/readiness signed off per SRC-005/SEC-007); product exposure: `hidden`. Source ID: `planetscope`.

---

### SkySat

🟢 **Live** · Planet Labs · 💼 Commercial

| Spec | Value |
|------|-------|
| Type | Optical |
| Resolution | 0.5 m |
| Revisit | Multiple per day |
| Swath | 8 km |
| Archive since | 2013 |
| Acquisition modes | Archive, Tasking |
| Spectral bands | Pan, Blue, Green, Red, NIR |
| Indices supported | Pan-sharpened NDVI |
| Use cases | Change Detection, Crop Health Monitoring |

**About.** High-resolution tasking constellation with sub-50 cm imagery and intra-day revisit. Ideal for crop-scouting and verification.

**Indian agri context.** Used for scouting and verifying field-level claims in **commodity-trading workflows (sugar, palm-oil)** when ResourceSat resolution isn't enough.

**Ingestion state.** Provider adapter: `planet`; cadence class: `multiple_per_day`; schedule state: `disabled`; commercial state: `commercial_blocked` (no Planet API subscription; paid task/order disabled by default per SRC-005/SEC-007); product exposure: `hidden`. Source ID: `skysat`.

---

### BlackSky Gen 3

🟢 **Live** · BlackSky · 💼 Commercial

| Spec | Value |
|------|-------|
| Type | Optical |
| Resolution | 0.35 m |
| Revisit | **15 × per day** (highest in catalogue) |
| Swath | 5 km |
| Archive since | 2023 |
| Acquisition modes | Archive, Tasking |
| Spectral bands | Pan, Blue, Green, Red, NIR |
| Indices supported | Pan-sharpened NDVI |
| Use cases | Flood / Disaster Response, Change Detection |

**About.** Highest revisit cadence in the catalogue — up to 15 visits per day per AOI for hour-scale operational monitoring.

**Indian agri context.** **Hour-scale damage assessment** after cyclones (Tauktae, Biparjoy class) and time-critical sugarcane mill scheduling.

**Ingestion state.** Provider adapter: `vendor`; cadence class: `multiple_per_day`; schedule state: `disabled`; commercial state: `commercial_blocked` (no BlackSky vendor contract; paid task/order disabled by default per SRC-005/SEC-007); product exposure: `hidden`. Source ID: `blacksky-gen-3`.

---

### KOMPSAT-3A

🟢 **Live** · KARI / SIIS · 💼 Commercial

| Spec | Value |
|------|-------|
| Type | Optical |
| Resolution | 0.4 m |
| Revisit | 1.5 days |
| Swath | 13 km |
| Archive since | 2015 |
| Acquisition modes | Archive, Tasking |
| Spectral bands | Pan, Blue, Green, Red, NIR + **MWIR (mid-wave infrared)** |
| Indices supported | Pan-sharpened NDVI |
| Use cases | Crop Health Monitoring, Change Detection |

**About.** Korea Aerospace Research Institute (KARI) sub-50 cm satellite with mid-wave infrared instrument — useful for thermal-stress detection in crops.

**Indian agri context.** Mid-wave infrared from KOMPSAT-3A is uniquely suited to **thermal-stress detection in greenhouse and protected-cultivation systems**.

**Ingestion state.** Provider adapter: `vendor`; cadence class: `2_to_5_days`; schedule state: `disabled`; commercial state: `commercial_blocked` (no KARI/SIIS vendor contract; paid task/order disabled by default per SRC-005/SEC-007; MWIR payload may require additional export licensing); product exposure: `hidden`. Source ID: `kompsat-3a`.

---

### Landsat 7

⚪ **Archive 1999 – 2024** · NASA / USGS · Open / free

| Spec | Value |
|------|-------|
| Type | Optical / Multispectral |
| Resolution | 30 m |
| Revisit | 16 days |
| Swath | 185 km |
| Archive | 1999 → 2024 (decommissioned) |
| Acquisition modes | Archive only |
| Spectral bands | Blue, Green, Red, NIR, SWIR, Pan, Thermal |
| Indices supported | NDVI, EVI, NBR |
| Use cases | Change Detection, Carbon & Biomass Estimation |

**About.** ETM+ instrument, decommissioned in 2024. Notable **SLC-off line gaps after 2003** — most modern pipelines auto-fill them.

**Indian agri context.** Bridges the gap between Landsat-5 and Landsat-8 archives — essential for any **1999 – 2013 baseline** of India's cropping intensity.

**Ingestion state.** Provider adapter: `usgs`; cadence class: `archive_on_demand`; schedule state: `archive_only` (decommissioned 2024; not a routine current-monitoring source per SRC-007; USGS STAC+COG adapter not yet implemented; SLC-off scan-line gaps post-2003); product exposure: `hidden`. Source ID: `landsat-7-c2-l2`.

---

### Landsat 5

⚪ **Archive 1984 – 2013** · NASA / USGS · Open / free

| Spec | Value |
|------|-------|
| Type | Optical / Multispectral |
| Resolution | 30 m (multispectral), 60 m (thermal) |
| Revisit | 16 days |
| Swath | 185 km |
| Archive | 1984 → 2013 (decommissioned) |
| Acquisition modes | Archive only |
| Spectral bands | Blue, Green, Red, NIR, SWIR, Thermal |
| Indices supported | NDVI, EVI, NBR |
| Use cases | Change Detection, Carbon & Biomass Estimation |

**About.** Guinness-record holder for the **longest-operating Earth-observation satellite (29 years)**. Foundation of any pre-2000 land-use, deforestation or climate-change study.

**Indian agri context.** Backbone of every long-term cropping-intensity and land-use-change study in the **IGP and Deccan plateau**.

**Ingestion state.** Provider adapter: `usgs`; cadence class: `archive_on_demand`; schedule state: `archive_only` (decommissioned 2013; not a routine current-monitoring source per SRC-007; USGS STAC+COG adapter not yet implemented); product exposure: `hidden`. Source ID: `landsat-5-c2-l2`.

---

### IRS-1C

⚪ **Archive 1995 – 2007** · 🇮🇳 ISRO

| Spec | Value |
|------|-------|
| Type | Multispectral / Optical |
| Resolution | 5.8 m (Pan); 23 m (LISS-3) |
| Revisit | 24 days |
| Swath | 70 km |
| Archive | 1995 → 2007 (decommissioned) |
| Acquisition modes | Archive only |
| Spectral bands | Pan, Green, Red, NIR, SWIR |
| Indices supported | NDVI, NDWI |
| Use cases | Change Detection, Crop Health Monitoring |

**About.** ISRO's first true sub-10 m mission — ran from 1995 to 2007 and put India squarely on the high-resolution Earth-observation map.

**Indian agri context.** Reference baseline for any **1995 – 2007 study** on Indian cropping patterns, urban encroachment of farmland, and groundwater-irrigation expansion.

**Ingestion state.** Provider adapter: `bhoonidhi`; cadence class: `archive_on_demand`; schedule state: `archive_only` (decommissioned 2007; not a routine current-monitoring source per SRC-007; archive optical validation profile required before any product exposure); product exposure: `hidden`. Source ID: `irs-1c-liss3-archive`.

---

### NAIP (USDA Aerial)

🟢 **Live** · USDA · Open / free (USA only)

| Spec | Value |
|------|-------|
| Type | Aerial / UAV (manned aircraft) |
| Resolution | 1 m |
| Revisit | Yearly (during U.S. growing season) |
| Swath | n/a (aerial mosaics) |
| Archive since | 2003 |
| Acquisition modes | Archive |
| Spectral bands | Blue, Green, Red, NIR (4-band) |
| Indices supported | 4-band NDVI |
| Use cases | Crop Health Monitoring, Change Detection, Soil Mapping |

**About.** USDA aerial imagery acquired by manned aircraft during the U.S. growing season. Public-domain, 1 m resolution.

**Indian agri context.** **Reference dataset for ground-truth field-boundary work** in CIDSA labs — Indian projects use the NAIP methodology even though NAIP coverage itself is U.S.-only.

**Ingestion state.** Provider adapter: `usda`; cadence class: `reference`; schedule state: `disabled`; AOI scope: `reference_only` (out-of-AOI for `bangalore-60km` and all India deployments per SRC-006; no executable ingestion pipeline for India); product exposure: `reference_only`. Source ID: `naip-reference-only`.

---

### NISAR

🟢 **Live (launched 30 July 2025)** · 🇮🇳 ISRO + NASA · Open / free (NRSC / NASA Earthdata)

| Spec | Value |
|------|-------|
| Type | Radar (SAR) — **L-band + S-band** (dual-frequency) |
| Resolution | 3 – 10 m |
| Revisit | 12 days |
| Swath | 240 km |
| Archive since | 2025 (CAL/VAL in progress; analysis-ready products expected from 2026) |
| Acquisition modes | Tasking |
| Spectral bands | L-band, S-band |
| Indices supported | σ⁰ backscatter, Polarimetric decomposition, **InSAR** |
| Use cases | Carbon & Biomass Estimation, Soil Mapping, Flood / Disaster Response |

**About.** Joint NASA–ISRO mission carrying both L-band and S-band SAR — designed for ecosystem disturbance, biomass and ice-sheet dynamics at 3 – 10 m resolution.

**Indian agri context.** The mission **launched 30 July 2025** and is in CAL/VAL; first analysis-ready products are expected from 2026 onward. NISAR is now positioned as the **global standard for soil-moisture and above-ground biomass measurement** and a flagship dataset for India's carbon-credit and climate-resilient-agriculture work — promoted to **primary L-band recommendation** for soil and biomass workflows once ARD ships, with ALOS-2 retained as the production fallback in the interim.

**Ingestion state.** Provider adapter: `bhoonidhi` (primary) / `asf` (alternate); cadence class: `10_to_20_days`; schedule state: `disabled` (data-gated: calibrated ARD/GCOV products not yet validated; SAR backscatter profile required; GEO-002 — no optical indices; dual-provider adapter selection pending); product exposure: `hidden`. Source ID: `nisar-ssar-beta-gcov`.

---

## 5. Recipe playbook — common scenarios

These are the canonical CIDSA-style stacks. Each recipe lists the **primary** sensor (always-on), **secondary** sensors (fallback or confirmation), and the **trigger** that flips you between them.

### 5.1 Wheat NDVI / NDRE growth-stage tracker (rabi season)

- **Primary:** Sentinel-2 (5-day, red-edge bands, free)
- **Secondary:** Landsat 9 (gap-fill, thermal stress)
- **Tier-up:** PlanetScope when daily cadence is needed for stress events
- **Trigger to switch:** sustained > 30 % cloud cover for > 10 days → fall back to Sentinel-1 RVI.

### 5.2 Kharif rice acreage & flood damage

- **Primary:** **Sentinel-1** (SAR, all-weather, 6-day)
- **Secondary:** EOS-04 RISAT (ISRO C-band confirmation, FRS mode)
- **Tier-up:** ALOS-2 PALSAR-2 (L-band) for paddy phenology classification
- **Disaster response:** BlackSky Gen 3 for hour-scale post-cyclone damage assessment.

### 5.3 Sugarcane mill yield forecast

- **Primary:** Landsat 8 + 9 (8-day effective; 30 m + thermal)
- **Secondary:** Sentinel-2 (cross-check NDRE)
- **Long-cycle baseline:** ResourceSat-2A LISS-3
- **Operational scouting:** SkySat tasking pre-harvest.

### 5.4 Cotton precision irrigation

- **Primary:** PlanetScope (daily NDRE for stress)
- **Secondary:** Sentinel-1 (soil-moisture proxy via VV/VH backscatter)
- **Soil mapping baseline:** ResourceSat-2A SWIR
- **Validation:** Sentinel-2 NDWI weekly.

### 5.5 Field-boundary digitisation (FPOs, KCC verification)

- **Primary:** **Cartosat-3** (0.25 m, ISRO, India-licensable)
- **Alternative (commercial):** SuperView NEO-1, KOMPSAT-3A, SkySat
- **Reference / methodology:** NAIP 1 m (US benchmark workflows).

### 5.6 Decadal land-use change (1985 → today)

- **1984 – 2013:** Landsat 5
- **1999 – 2013:** Landsat 7 (SLC-off after 2003 — fill required)
- **2013 → today:** Landsat 8 → 9 (continuous)
- **2017 → today:** Sentinel-2 (overlay for higher detail)
- **India-specific 1995 – 2007 layer:** IRS-1C.

### 5.7 Carbon, biomass, REDD+ / climate-resilient agriculture

- **Primary today:** ALOS-2 PALSAR-2 (L-band, 14-day)
- **Primary from 2025+:** **NISAR** (dual-band L+S, 12-day, free)
- **Regional context:** MODIS NDVI / EVI long record
- **Cross-check:** Sentinel-1 backscatter time-series.

### 5.8 Stubble-burn / fire detection (Punjab, Haryana — Oct/Nov)

- **Primary:** MODIS Active Fire (Terra + Aqua, 4 passes / day)
- **Secondary:** Sentinel-2 short-wave infrared (NBR for burn-scar mapping)
- **Confirmation:** Landsat 8/9 thermal band.

---

## 6. Quick-reference cheat sheet

```
┌─────────────────────────────────────────────────────────────────────────┐
│  YOUR QUESTION                              →  SATELLITE                │
├─────────────────────────────────────────────────────────────────────────┤
│  "I need NDVI today, free, 10 m"            →  Sentinel-2               │
│  "I need NDVI today, daily, paid OK"        →  PlanetScope              │
│  "It's cloudy / monsoon"                    →  Sentinel-1 (SAR)         │
│  "I need to see individual trees"           →  Cartosat-3 / SkySat      │
│  "I need a 40-year baseline"                →  Landsat 5 → 9            │
│  "I need state-level phenology daily"       →  MODIS                    │
│  "I need biomass under forest canopy"       →  ALOS-2 (or NISAR 2025+)  │
│  "Cyclone hit yesterday — assess damage"    →  BlackSky Gen 3 + S-1     │
│  "FASAL / national crop area"               →  ResourceSat-2A           │
│  "Coastal paddy / chlorophyll"              →  EOS-06 OceanSat-3        │
│  "Stubble burn fires"                       →  MODIS Active Fire        │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 7. Spectral band wavelength reference

Every detail-drawer on the Satellite Finder shows a **wavelength bar (nm)** that places each band on a 400 → 2500 nm axis. The visual is purely a function of the wavelength range each band name maps to. Use this lookup whenever a satellite entry lists a band name and you need to know the underlying spectrum:

| Band | Wavelength range (nm) | Why it matters in agriculture |
|------|----------------------:|-------------------------------|
| **Coastal** | 400 – 450 | Atmospheric correction, shallow-water bathymetry, aerosol load. |
| **Blue** | 450 – 500 | True-colour visualisation; chlorophyll absorption peak. |
| **Green** | 500 – 580 | Vegetation vigour (peak reflectance for healthy leaves); GNDVI. |
| **Red** | 620 – 700 | Chlorophyll absorption — denominator of NDVI / EVI / SAVI. |
| **Red Edge** | 700 – 760 | Most sensitive to chlorophyll content / stress; **NDRE**. Sentinel-2 and PlanetScope only. |
| **NIR** (Near-Infrared) | 760 – 900 | Cell-structure reflectance — numerator of NDVI / EVI; biomass. |
| **SWIR** (Short-Wave IR) | 1400 – 2500 | Plant water content (NDWI), soil moisture, mineral mapping, burn severity (NBR). |
| **Pan** (Panchromatic) | 450 – 750 | Single broadband channel at the platform's highest resolution — pan-sharpening. |
| **Thermal** | 10 000 – 12 000 | Land Surface Temperature (LST) — heat-stress, evapotranspiration. |
| **C-band SAR** | ~5.4 GHz / ~5.6 cm | Soil moisture, flood mapping, all-weather change detection. |
| **L-band SAR** | ~1.3 GHz / ~24 cm | Penetrates vegetation canopy → forest biomass, paddy phenology. |
| **S-band SAR** | ~3.2 GHz / ~10 cm | Mid-canopy penetration; complementary to L-band on NISAR. |
| **X-band SAR** | ~9.6 GHz / ~3 cm | Highest-detail SAR — infrastructure & urban (not currently in this catalogue). |

> The first 9 rows are optical / multispectral and use **nanometres**. The last 4 are radar bands quoted in **frequency (GHz) / wavelength (cm)** because SAR sensors operate in the microwave region (millions of times longer than visible light).

### Per-satellite spectral footprint

This table answers "which satellite has Red Edge?" / "who carries Thermal?" at a glance. ✅ = band present.

| Satellite | Coastal | Blue | Green | Red | Red Edge | NIR | SWIR | Pan | Thermal | SAR band |
|-----------|:------:|:----:|:-----:|:---:|:--------:|:---:|:----:|:---:|:-------:|:---------|
| Sentinel-2 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | – | – | – |
| Sentinel-1 | – | – | – | – | – | – | – | – | – | C |
| Landsat 8 | ✅ | ✅ | ✅ | ✅ | – | ✅ | ✅ | ✅ | ✅ | – |
| Landsat 9 | ✅ | ✅ | ✅ | ✅ | – | ✅ | ✅ | ✅ | ✅ | – |
| MODIS (T/A) | – | ✅ | ✅ | ✅ | – | ✅ | ✅ | – | ✅ | – |
| ResourceSat-2A | – | – | ✅ | ✅ | – | ✅ | ✅ | – | – | – |
| Cartosat-3 | – | ✅ | ✅ | ✅ | – | ✅ | – | ✅ | – | – |
| EOS-04 (RISAT) | – | – | – | – | – | – | – | – | – | C |
| EOS-06 (OceanSat-3) | – | ✅ | ✅ | ✅ | – | ✅ | ✅ | – | – | – |
| ALOS-2 (PALSAR-2) | – | – | – | – | – | – | – | – | – | L |
| SuperView NEO-1 | – | ✅ | ✅ | ✅ | – | ✅ | – | ✅ | – | – |
| PlanetScope | – | ✅ | ✅ | ✅ | ✅ | ✅ | – | – | – | – |
| SkySat | – | ✅ | ✅ | ✅ | – | ✅ | – | ✅ | – | – |
| BlackSky Gen 3 | – | ✅ | ✅ | ✅ | – | ✅ | – | ✅ | – | – |
| KOMPSAT-3A | – | ✅ | ✅ | ✅ | – | ✅ | – | ✅ | – | – |
| Landsat 7 ⚪ | – | ✅ | ✅ | ✅ | – | ✅ | ✅ | ✅ | ✅ | – |
| Landsat 5 ⚪ | – | ✅ | ✅ | ✅ | – | ✅ | ✅ | – | ✅ | – |
| IRS-1C ⚪ | – | – | ✅ | ✅ | – | ✅ | ✅ | ✅ | – | – |
| NAIP | – | ✅ | ✅ | ✅ | – | ✅ | – | – | – | – |
| NISAR | – | – | – | – | – | – | – | – | – | L + S |

KOMPSAT-3A also carries an extra **MWIR (mid-wave infrared)** payload not shown above — used for thermal-stress detection.

---

## 8. Slug & archive deep-link reference

Every detail drawer on the Satellite Finder ends with a **"Search archive"** call-to-action that links to a per-satellite finder URL of the form:

```
https://cidsaglobal.com/akasha/find-satellite?source={slug}
```

Use this table to jump directly into the right archive search for any platform in the catalogue. The slug is also the stable identifier you should use when referencing a satellite in code, schemas, or analytics events.

| Satellite | Slug | Archive search deep-link |
|-----------|------|--------------------------|
| Sentinel-2 | `sentinel-2` | [Search archive](https://cidsaglobal.com/akasha/find-satellite?source=sentinel-2) |
| Sentinel-1 | `sentinel-1` | [Search archive](https://cidsaglobal.com/akasha/find-satellite?source=sentinel-1) |
| Landsat 8 | `landsat-8` | [Search archive](https://cidsaglobal.com/akasha/find-satellite?source=landsat-8) |
| Landsat 9 | `landsat-9` | [Search archive](https://cidsaglobal.com/akasha/find-satellite?source=landsat-9) |
| MODIS (Terra / Aqua) | `modis` | [Search archive](https://cidsaglobal.com/akasha/find-satellite?source=modis) |
| ResourceSat-2A | `resourcesat-2a` | [Search archive](https://cidsaglobal.com/akasha/find-satellite?source=resourcesat-2a) |
| Cartosat-3 | `cartosat-3` | [Search archive](https://cidsaglobal.com/akasha/find-satellite?source=cartosat-3) |
| EOS-04 (RISAT) | `eos-04-risat` | [Search archive](https://cidsaglobal.com/akasha/find-satellite?source=eos-04-risat) |
| EOS-06 (OceanSat-3) | `eos-06-oceansat-3` | [Search archive](https://cidsaglobal.com/akasha/find-satellite?source=eos-06-oceansat-3) |
| ALOS-2 (PALSAR-2) | `alos-2-palsar-2` | [Search archive](https://cidsaglobal.com/akasha/find-satellite?source=alos-2-palsar-2) |
| SuperView NEO-1 | `superview-neo-1` | [Search archive](https://cidsaglobal.com/akasha/find-satellite?source=superview-neo-1) |
| PlanetScope | `planetscope` | [Search archive](https://cidsaglobal.com/akasha/find-satellite?source=planetscope) |
| SkySat | `skysat` | [Search archive](https://cidsaglobal.com/akasha/find-satellite?source=skysat) |
| BlackSky Gen 3 | `blacksky-gen-3` | [Search archive](https://cidsaglobal.com/akasha/find-satellite?source=blacksky-gen-3) |
| KOMPSAT-3A | `kompsat-3a` | [Search archive](https://cidsaglobal.com/akasha/find-satellite?source=kompsat-3a) |
| Landsat 7 | `landsat-7` | [Search archive](https://cidsaglobal.com/akasha/find-satellite?source=landsat-7) |
| Landsat 5 | `landsat-5` | [Search archive](https://cidsaglobal.com/akasha/find-satellite?source=landsat-5) |
| IRS-1C | `irs-1c` | [Search archive](https://cidsaglobal.com/akasha/find-satellite?source=irs-1c) |
| NAIP (USDA Aerial) | `naip` | [Search archive](https://cidsaglobal.com/akasha/find-satellite?source=naip) |
| NISAR | `nisar` | [Search archive](https://cidsaglobal.com/akasha/find-satellite?source=nisar) |

In addition to **Search archive**, every detail drawer exposes two further CTAs:

- **Request data access** → `https://cidsaglobal.com/contact` (single contact form, no per-satellite parameter).
- **Download spec sheet** → triggers a client-side PDF/CSV export of the open drawer's data (no public URL).

### Scheduler source-state mapping

The provider-agnostic ingestion scheduler uses the catalogue slug as the stable business key, but
one catalogue platform may map to multiple Akasha source rows when instruments/products have
different cadence, resolution, validation profiles, or product exposure. Every scheduler source row
must include `catalogSlug`, `catalogPlatform`, `sourceId`, `providerAdapter`, `productFamily`,
`instrumentMode`, `productVariant`, `analysisLevel`, `validationProfile`, and `productExposure`.

| Catalogue slug | Initial Akasha source row(s) | Provider adapter | Initial scheduler/product state |
|---|---|---|---|
| `sentinel-2` | `sentinel-2-l2a` | `cdse` | Gated/operator-validation; product exposure disabled until CDSE validation. |
| `sentinel-1` | `sentinel-1-grd` | `cdse` | Gated SAR; no optical indices; SAR validation profile required. |
| `landsat-8` | `landsat-8-c2-l2` | `usgs` | Gated/operator-validation; cloud STAC+COG preferred. |
| `landsat-9` | `landsat-9-c2-l2` | `usgs` | Gated/operator-validation; cloud STAC+COG preferred. |
| `modis` | `modis-13q1-061` | `earthdata` | Regional context only; not field analytics. |
| `resourcesat-2a` | `resourcesat-2a-liss3-boa`, `resourcesat-2a-liss4-mx70-l2`, `resourcesat-2a-awifs-boa` | `bhoonidhi` | LISS-3 active baseline; LISS-4 active field enhancement; AWiFS active regional/coarse product with 60% minimum usable coverage. |
| `cartosat-3` | `cartosat-3-gated` | `vendor` / manual | Manual/VHR context placeholder; no programmatic Bhoonidhi catalog path yet. |
| `eos-04-risat` | `eos-04-sar-mrs-l2b` | `bhoonidhi` | Gated SAR; MRS/CRS only; SAR validation profile required. |
| `eos-06-oceansat-3` | `eos-06-ocm-lac-ndvi-8day-360m` | `bhoonidhi` | Regional precomputed NDVI context; not field analytics. |
| `alos-2-palsar-2` | `alos2-palsar2` / `alos2-mosaic-25m` | `jaxa` | Commercial scenes blocked; free mosaic may be regional SAR/context after validation. |
| `superview-neo-1` | `superview-neo-1` | `vendor` | Commercial blocked; paid task/order disabled by default. |
| `planetscope` | `planetscope` | `planet` | Commercial blocked; search-only until contract/quota/readiness. |
| `skysat` | `skysat` | `planet` | Commercial blocked; paid task/order disabled by default. |
| `blacksky-gen-3` | `blacksky-gen-3` | `vendor` | Commercial blocked; paid task/order disabled by default. |
| `kompsat-3a` | `kompsat-3a` | `vendor` | Commercial blocked; paid task/order disabled by default. |
| `landsat-7` | `landsat-7-c2-l2` | `usgs` | Archive-only/on-demand; not routine scheduled. |
| `landsat-5` | `landsat-5-c2-l2` | `usgs` | Archive-only/on-demand; not routine scheduled. |
| `irs-1c` | `irs-1c-liss3-archive` | `bhoonidhi` | Archive-only/on-demand; validation profile required. |
| `naip` | none for India AOIs | `usda`/cloud | Reference-only/out-of-AOI for `bangalore-60km`. |
| `nisar` | `nisar-ssar-beta-gcov` | `bhoonidhi` / `asf` | Data-gated until calibrated ARD/GCOV products are validated. |

---

## 9. Glossary

| Term | Meaning |
|------|---------|
| **Pan / panchromatic** | Single broadband visible channel — produces grayscale but at the highest resolution of the platform. |
| **Multispectral** | Discrete bands in visible + NIR + sometimes SWIR — enables vegetation indices like NDVI / NDRE. |
| **SAR (Synthetic Aperture Radar)** | Active microwave imaging; sees through clouds and at night. |
| **C-band / L-band / S-band / X-band** | SAR frequency bands. L-band penetrates canopy; C-band balances penetration & resolution; X-band gives finest detail. |
| **Red Edge** | Spectral region (700 – 760 nm) most sensitive to plant chlorophyll stress — exclusive to Sentinel-2, PlanetScope, RapidEye. |
| **Swath** | Width of the strip imaged in a single pass. Bigger swath = fewer passes to cover a country. |
| **Revisit** | How often a satellite re-images the same point on Earth. |
| **Acquisition: Archive** | Pre-existing imagery you can download / order from the catalogue. |
| **Acquisition: Tasking** | You commission a future acquisition over your AOI (usually paid). |
| **NDVI / NDRE / NDWI / EVI / SAVI / MSAVI** | Vegetation / water indices computed from multispectral bands. |
| **σ⁰ (sigma-nought) backscatter** | SAR signal strength reflected from the ground — proxy for surface roughness, soil moisture, biomass. |
| **InSAR** | Interferometric SAR — uses phase differences between SAR passes for surface-deformation and DEM. |
| **FRS / MRS / CRS** | Fine, Medium and Coarse Resolution Stripmap modes (RISAT / EOS-04). |
| **FASAL** | Forecasting Agricultural output using Space, Agro-meteorology and Land-based observations — ISRO/MoA national programme. |
| **NRSC** | National Remote Sensing Centre, ISRO's data distribution arm ([Bhoonidhi portal](https://bhoonidhi.nrsc.gov.in)). |

---

## 10. Source & maintenance

- **Origin:** Data extracted from CIDSA's Akasha Satellite Finder SPA (`https://cidsaglobal.com/akasha/satellite-finder`) and verified against the page's detail-modal content.
- **Last refresh:** 2026-05-13.
- **Update procedure:** when new platforms are added to the Satellite Finder, re-run the extraction and append entries below the existing tables — keep the platform ordering by **resolution (best first)** in §1 and §2 to match the source page's default sort.
