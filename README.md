# Terraform Feature Lag Tracker

![Pillar](https://img.shields.io/badge/Pillar-Modern%20Infrastructure%20%26%20IaC-f59e0b)
![Status](https://img.shields.io/badge/Status-Production--Ready-22c55e)
![License](https://img.shields.io/badge/License-MIT-green)

> Live drift tracker showing the gap between cloud provider GA announcements and Terraform provider support — deterministic, refreshed daily, and nothing leaves your browser.

[Launch Tracker](https://tlt.rack2cloud.com) | [Landing Page](https://www.rack2cloud.com/terraform-feature-lag-tracker/) | [Hub](https://www.rack2cloud.com/engineering-workbench/iac-governance/)
---

## Overview

"Generally Available" is a half-truth for the IaC engineer. AWS, Azure, and GCP ship new services and features fast — the Terraform provider release that gives you a declarative way to manage them lags behind, sometimes by days, sometimes by years. That gap is where migration timelines slip, where teams reach for `AzAPI` or Cloud Control API workarounds, and where "we'll automate that later" becomes permanent ClickOps.

The Terraform Feature Lag Tracker closes the visibility gap. A daily pipeline scrapes AWS, Azure, and GCP release feeds, cross-references each announcement against the corresponding Terraform provider's GitHub releases, and publishes the result as a single dataset. The frontend is a pure data lens on top of it — no calculation happens client-side beyond filtering, sorting, and rendering.

**Not** a Terraform Registry mirror. **Not** a migration tool. **Not** an AI-generated summary — every match is a deterministic keyword/resource-prefix comparison against release-note text. It **is**: automated, daily, and read-only.

---

## How It Works

| Section | Purpose |
|---|---|
| KPI Row | Total features tracked, open provider gaps, and the freshness date of the underlying dataset |
| Filters | Cloud (All / AWS / Azure / GCP) and Status (All / Gap / Supported), plus free-text search |
| Table | Feature / Cloud / Status / Lag (visual bar, 180-day scale) / Terraform version that shipped support |

Sort order is gap-first: open gaps sorted by lag (highest first), followed by supported features sorted by lag (highest first). The two groups are never sorted together — `lag` means "days until Terraform shipped support" for supported features (a fixed historical number) and "days since GA with no support yet" for open gaps (a number that grows daily). Mixing them in one sort would rank an old, already-resolved slow release above an active gap that's actively costing someone time today.

---

## Data Pipeline

```
GitHub Actions (daily, 08:00 UTC cron)
  → tracker_backend.py
    → scrapes AWS / Azure / GCP release feeds (RSS/Atom + archive scan)
    → fetches Terraform provider releases via GitHub API
    → keyword + resource-prefix matching against release note bodies
  → r2c_lag_data.json (committed back to this repo)
  → frontend fetches directly from raw.githubusercontent.com at page load
```

No server, no database. The JSON file in this repo *is* the backend.

---

## Stack

```
index.html    — entire application (HTML + CSS + vanilla JS)
vercel.json   — routing config, security headers
tracker_backend.py            — data pipeline (Python)
.github/workflows/daily_refresh.yml — daily scrape + commit automation
r2c_lag_data.json             — the dataset itself, source of truth for the frontend
```

No framework. No build step. No runtime dependencies beyond Google Fonts on the frontend. Deployable to any static host. Optimized for Vercel.

---

## Local Development

```bash
git clone https://github.com/rack2cloud/Terraform-Feature-Lag-Tracker.git
cd Terraform-Feature-Lag-Tracker
open index.html
```

No install step. No build step. Open the file. (The data pipeline itself requires Python 3.9+ and the dependencies listed in `daily_refresh.yml` if you want to run `tracker_backend.py` locally.)

---

## Deployment

Connects directly to Vercel via GitHub integration.
Custom domain: `tlt.rack2cloud.com` → CNAME `cname.vercel-dns.com`

---

## Roadmap

| Item | Status | Notes |
|---|---|---|
| AWS / Azure / GCP daily scrape + Terraform release matching | ✅ v1 Live | This repo — `tracker_backend.py`, daily GitHub Actions cron |
| Gap-first sort, lag-bar visualization | ✅ v1 Live | 180-day normalized scale, exact day count always shown |
| Feed-unavailable fallback (localStorage cache) | ✅ v1 Live | Never shows an empty table on fetch failure — that would imply zero gaps |
| Impact classification (High/Medium/Low blast radius) | 🔵 Deferred | Needs a new field in the data pipeline — not something the frontend can infer |
| Partial support status | 🔵 Deferred | Backend currently binary (Supported / Not Supported) — would require distinguishing preview/beta provider releases in `tracker_backend.py` |
| Multi-cloud parity view | 🔵 Deferred | Requires cross-vendor feature-equivalence mapping the scraper doesn't currently produce |
| Historical trend / snapshot retention | 🔵 Deferred | Git commit history on `r2c_lag_data.json` was checked and found insufficient (only 2 commits ever touched the file) — a real snapshot mechanism is needed, not git archaeology |

---

## Part of the Rack2Cloud Engineering Workbench

The IaC Governance Toolkit is one of five operational hubs on [Rack2Cloud.com](https://www.rack2cloud.com) — a technical architecture publication for senior enterprise infrastructure architects.

Related reading: [Terraform Feature Lag Tracker: IaC Guide](https://www.rack2cloud.com/terraform-feature-lag-tracker/)

---

## License

MIT License — see [LICENSE](LICENSE)

---

## About

Built by [The Architect](https://rack2cloud.com) — 25+ years of enterprise infrastructure delivery across financial services, healthcare, manufacturing, and public sector.

**Rack2Cloud.com** | [Modern Infrastructure & IaC](https://rack2cloud.com/modern-infrastructure-iac-learning-path/) | [Infrastructure Architecture Review](https://rack2cloud.com/work-with-me/) | [Contact](https://rack2cloud.com/contact)

---
