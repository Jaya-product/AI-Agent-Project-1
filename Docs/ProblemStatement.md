# Problem Statement — Groww App Review Intelligence Agent

## Overview

Build an AI agent for the **[Groww platform](https://play.google.com/store/apps/details?id=com.nextbillion.groww&hl=en_IN)** (Stocks, Mutual Funds, IPO) that transforms raw mobile-store user feedback into a **weekly pulse report** — giving your team a scannable, insight-rich summary in minutes.

> **Goal:** Aggregate, theme, summarize, and deliver user review insights through familiar surfaces — Google Docs for the written pulse, and Gmail for a ready-to-send draft — without manually handling credentials or REST wiring.

---

## End-to-End Flow

```
Pull Reviews  →  Cluster into Themes  →  Generate Weekly Pulse  →  Publish to Google Docs  →  Draft Email via Gmail
```

1. **Pull** recent Play Store reviews for the Groww app (public exports only).
2. **Cluster** reviews into a small set of themes and distill a one-page weekly note.
3. **Publish** the note to Google Docs so stakeholders can read it.
4. **Draft** an email to yourself (or an alias) containing or linking to the pulse.

---

## Deliverables

### Weekly One-Page Pulse
The generated document must include:

| Section | Description |
|---|---|
| **Top Themes** | What users are talking about most (up to 3) |
| **User Quotes** | Verbatim snippets from real reviews — no invented wording |
| **Action Ideas** | 3 concrete next steps grounded in the identified themes |

### Draft Email
A Gmail draft containing the weekly note (or a clear link to it), ready for the team to review and send.

---

## Who This Helps

| Audience | Why |
|---|---|
| **Product / Growth** | Prioritize fixes and improvements from real user signals |
| **Support** | Align messaging with what users are actually saying |
| **Leadership** | One-page health check without drowning in raw reviews |

---

## What to Build

### 1. Review Ingestion
- Import reviews from roughly the **last 8–12 weeks**.
- Fields to capture: `rating`, `title`, `text`, `date` (and any others available in the export).

### 2. Theme Clustering
- Group reviews into **at most 5 themes**.
- Example themes: `onboarding`, `KYC`, `payments`, `statements`, `withdrawals` — pick what fits the data.

### 3. Weekly Pulse Generation
The one-page note must contain:
- **Top 3 themes** (subset of the identified themes)
- **3 user quotes** (anonymized, verbatim)
- **3 action ideas** (concrete and grounded in the themes)

### 4. Google Docs Integration (via MCP)
- Create or update the pulse document in Google Docs using an MCP server.

### 5. Gmail Draft (via MCP)
- Create a draft email addressed to yourself or an alias, containing or linking to the weekly pulse, using an MCP server.

---

## Integrations: MCP-First Approach

Use **MCP (Model Context Protocol) servers** for Google Docs and Gmail integrations.

> ⚠️ **Requirement:** MCP-first — do **not** integrate Google APIs directly (no bespoke OAuth client + REST client code as the primary path). MCP servers expose the tools your agent calls; lean on that pattern to keep Docs and Gmail consistent with course tooling and avoid duplicating auth/HTTP plumbing.

---

## Key Constraints

| Constraint | Rule |
|---|---|
| **Reviews** | Use public review exports only — no scraping behind store logins or ToS-violating automation |
| **Themes** | Maximum **5 themes** for clustering; the pulse highlights the **top 3** |
| **Length** | Keep the note scannable — **≤ 250 words** where applicable |
| **Privacy** | No PII — no usernames, emails, device IDs, or other identifiable reviewer data in any artifact; quotes must be anonymous / stripped |

---

## Reference

- **App URL:** [Groww on Google Play](https://play.google.com/store/apps/details?id=com.nextbillion.groww&hl=en_IN)
- **Integration Pattern:** MCP (Model Context Protocol) for Google Docs & Gmail
