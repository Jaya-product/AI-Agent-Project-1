# Groww App Review Intelligence Agent

> **An automated intelligence pipeline that ingests Google Play Store reviews, scrubs PII, semantically clusters customer feedback using LLMs, synthesizes an executive Weekly Pulse note (≤ 250 words), updates a living Google Doc journal, and dispatches weekly email summaries via the Model Context Protocol (MCP).**

---

## Architecture & Pipeline Flow

```
┌────────────────────────────────────────────────────────────────────────┐
│                        Play Store Reviews                              │
│                   (com.nextbillion.groww)                              │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ [Phase 2] Ingestion & Parsing (google-play-scraper)                    │
│ • Fetch lookback window (default: 10 weeks)                           │
│ • Canonical Review schema (rating, date, text, thumbs_up upvotes)      │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ [Phase 3] Preprocessing & PII Scrubbing                                │
│ • Strip emails, phone numbers, UPI IDs, support ticket numbers         │
│ • Filter non-substantive/short reviews (<20 chars, ~67% filtered)      │
│ • Configurable regional script policy (Hindi, Tamil, Malayalam)        │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ [Phase 4] Semantic Theme Clustering (Gemini / OpenAI)                  │
│ • Clustered into ≤ 5 dominant themes ranked by volume                  │
│ • Prioritize community-upvoted quotes (thumbs_up > 0)                  │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ [Phase 5] Pulse Synthesis & Frontend Export                            │
│ • Top 3 themes · 3 verbatim quotes · 3 high-impact actions              │
│ • Strict ≤ 250-word cap enforced post-generation                       │
│ • Synchronizes interactive dashboard (pulse.json & pulse.js)           │
└───────────────────┬────────────────────────────────┬───────────────────┘
                    │                                │
                    ▼                                ▼
┌─────────────────────────────────────┐  ┌───────────────────────────────┐
│ [Phase 6A] Google Docs MCP          │  │ [Phase 6B] Gmail MCP          │
│ • Appends pulse note to Google Doc  │  │ • Dispatches executive email  │
│ • Non-destructive (end of segment)  │  │ • Inline pulse + Doc journal  │
└─────────────────────────────────────┘  └───────────────────────────────┘
```

---

## Key Features

1. **Production-Grade PII Scrubbing**: Strips personal email addresses, Indian 10-digit / +91 phone numbers, UPI virtual payment addresses (`user@upi`), and support ticket IDs (`Ticket ID 28133385`). Zero PII logging guaranteed.
2. **Real-World Play Store Data Handling**: Grounded in real Groww review data (~400 reviews/3 days):
   - Gracefully discards short emoji/one-word noise (`67.2%` of raw reviews).
   - Multilingual support natively handles Hindi (Devanagari), Tamil, Malayalam, and Punjabi scripts.
   - Leverages `thumbs_up` community validation to prioritize the most resonant user quotes.
3. **Strict Executive Format**: Guaranteed ≤ 250-word ceiling with 3 themes, 3 verbatim quotes (never truncated or fabricated), and 3 concrete action ideas.
4. **Model Context Protocol (MCP) Standard**: Connects via Streamable HTTP (SSE) to the Google Workspace MCP Server. Zero direct Google OAuth or private API tokens in application code.
5. **Interactive Web Dashboard**: Built-in executive frontend (`Designs/groww-pulse/`) with theme breakdown, quote explorer, and action mapping.

---

## Quickstart & Setup

### 1. Prerequisites
- Python 3.10+ (tested on Python 3.12)
- Node.js 18+ (optional, only needed if running local MCP server)

### 2. Installation
```bash
# Clone the repository
git clone https://github.com/Jaya-product/AI-Agent.git
cd "AI Agent"

# Create and activate virtual environment
python -m venv venv

# Windows (PowerShell)
.\venv\Scripts\Activate.ps1

# Linux / macOS
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Environment Configuration
Copy the template configuration file:
```bash
cp .env.example .env
```

Edit `.env` with your credentials:
```ini
# LLM Provider ("gemini" or "openai")
LLM_PROVIDER=gemini
GEMINI_API_KEY=your_gemini_api_key_here

# MCP Server Endpoint
MCP_SERVER_URL=https://mcp-server-production-3be3.up.railway.app
MCP_API_KEY=

# Target Google Workspace destinations
PULSE_DOC_ID=https://docs.google.com/document/d/your-google-doc-id/edit
PULSE_EMAIL_RECIPIENT=executives@groww.in
```

---

## Running the Pipeline

### Dry-Run Mode (Recommended for testing)
Runs the entire pipeline (ingestion, scrubbing, clustering, pulse synthesis, frontend export) and prints the formatted pulse to stdout without dispatching to Google Docs or Gmail:
```bash
python -m src --dry-run --sample-data
```

### Full Production Run
Fetches live Play Store reviews, synthesizes the pulse, appends to the Google Doc journal, and dispatches the executive email:
```bash
python -m src --weeks 10
```

### Launch Interactive Web Dashboard
Run the pipeline and instantly start the local dashboard server:
```bash
python -m src --dry-run --sample-data --serve --port 8000
```
Open `http://127.0.0.1:8000` in any web browser.

### CLI Arguments Reference

| Argument | Description | Default |
|---|---|---|
| `--weeks <N>` | Lookback window in weeks for review ingestion | `10` (from `settings.yaml`) |
| `--dry-run` | Prints pulse to terminal, skips MCP Docs & Gmail publishing | `False` |
| `--sample-data` | Uses cached `scripts/groww_reviews.json` instead of live fetch | `False` |
| `--doc-id <ID>` | Target Google Doc ID or URL override | `PULSE_DOC_ID` from `.env` |
| `--recipient <EMAIL>` | Target recipient email address override | `PULSE_EMAIL_RECIPIENT` from `.env` |
| `--serve` | Start the local web dashboard server after run | `False` |
| `--port <PORT>` | Port for dashboard server | `8000` |

---

## Model Context Protocol (MCP) Setup

The agent publishes weekly notes strictly via the **Google Workspace MCP Server** over Streamable HTTP (SSE).

### Option A: Hosted Railway Server (Default)
The agent is pre-configured to connect to the production Railway instance:
```ini
MCP_SERVER_URL=https://mcp-server-production-3be3.up.railway.app
```

### Option B: Local MCP Server
To run a local MCP server with your authorized Google OAuth tokens:
1. Ensure credentials exist at `../MCP/.credentials/tokens.json`.
2. Start the local server:
   ```bash
   python scripts/run_mcp_server.py
   ```
3. Set `MCP_SERVER_URL=http://127.0.0.1:8080` in `.env`.

---

## Testing & Quality Assurance

The codebase includes an exhaustive test suite covering all unit layers, MCP transport, schema validation, and real-data edge cases:

```bash
# Run all tests
venv\Scripts\pytest

# Run tests with code coverage report
venv\Scripts\pytest --cov=src
```

### Test Suite Status
- **231 tests passing** across 8 test suites.
- **87% overall code coverage** (exceeds the 80% project threshold).
- Verified against all 10 real-world edge cases (short reviews, multilingual text, ticket ID masking, 504-upvote viral reviews, single dominant theme, malformed LLM outputs, MCP server failure fallbacks).

---

## Project Structure

```
AI Agent/
├── src/
│   ├── agent/             # Phase 7: Orchestrator & CLI entry points
│   │   ├── core.py        # Pipeline runner, graceful degradation & CLI
│   ├── ingestion/         # Phase 2 & 3: Play Store ingestion & PII scrubber
│   │   ├── fetcher.py     # Paginated public Play Store review fetcher
│   │   ├── parser.py      # Pydantic Review models & normalization
│   │   └── scrubber.py    # Regex PII scrubber & length/language filters
│   ├── intelligence/      # Phase 4 & 5: LLM clustering & Pulse generator
│   │   ├── clusterer.py   # Theme clustering, backoff retries & quote weighting
│   │   └── pulse_generator.py # Pulse synthesis, word limiter & dashboard export
│   ├── mcp/               # Phase 6: Model Context Protocol transport & tools
│   │   ├── client.py      # Streamable HTTP SSE JSON-RPC 2.0 client
│   │   ├── docs_tools.py  # google_docs_append_text adapter
│   │   └── gmail_tools.py # gmail_send_email adapter with confirmation
│   ├── utils/             # Config loader & structured JSON logger
│   └── web/               # Embedded HTTP server for local pulse dashboard
├── tests/                 # Comprehensive test suite (231 tests, 87% coverage)
├── config/                # Runtime configuration settings (settings.yaml)
├── scripts/               # Sample Groww dataset & MCP runner utilities
├── Designs/               # Groww Pulse interactive web dashboard
└── Docs/                  # Architecture, problem statement & implementation plan
```

---

## Scheduling (Future Automation)

Automated weekly scheduling can be activated when needed:
- **GitHub Actions**: Run `.github/workflows/weekly_pulse.yml` on a cron schedule (`0 9 * * 1` — every Monday at 9:00 AM UTC).
- **Windows Task Scheduler / Linux cron**: Execute `python -m src` weekly with environment secrets pre-loaded.

---

## Deployment to Vercel

For complete step-by-step instructions on deploying the agent and its interactive dashboard to Vercel, see [deployment.md](deployment.md).


