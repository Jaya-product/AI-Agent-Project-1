# Production Deployment Plan for Vercel

> **The Single Best Deployment Architecture for the Groww Review Intelligence Agent.**
>
> Combines **Vercel’s Global Edge CDN** for lightning-fast dashboard delivery with **GitHub Actions** for the weekly automated Python intelligence pipeline (eliminating all serverless timeout risks).

---

## How It Works

```
┌─────────────────────────────────────────────────────────────┐
│  Every Monday at 09:00 UTC (Automated Scheduled Runner)     │
│  GitHub Actions executes Python Pipeline:                   │
│    1. Ingests ~400 Play Store reviews                       │
│    2. Masks all PII (emails, phones, UPI, tickets)          │
│    3. Clusters themes via Gemini / OpenAI                   │
│    4. Appends Weekly Pulse to Google Doc via MCP            │
│    5. Dispatches executive summary email via Gmail MCP      │
│    6. Commits fresh pulse.json to the repository            │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               │ git push (automated)
                               ▼
┌─────────────────────────────────────────────────────────────┐
│  Vercel Global Edge Network (Zero-Downtime Auto-Deploy)     │
│    • Instantly detects the updated pulse data               │
│    • Rebuilds and deploys static dashboard in < 15 seconds  │
│    • Serves interactive UI to executives at 100% uptime     │
│    • Sub-100ms load times worldwide with zero cold-starts   │
└─────────────────────────────────────────────────────────────┘
```

---

## Step-by-Step Deployment (Total Time: ~5 Minutes)

### Step 1: Push Repository to GitHub
Make sure your latest code is committed and pushed to your GitHub repository:
```bash
# If git is not yet initialized:
# git init
# git remote add origin https://github.com/<your-user>/<your-repo>.git

git add .
git commit -m "feat: vercel deployment and automated weekly pulse workflow"
git push -u origin main
```

---

### Step 2: Deploy Dashboard to Vercel

1. Go to [vercel.com/new](https://vercel.com/new) and log in with your GitHub account.
2. Under **Import Git Repository**, find your `AI-Agent` repository and click **Import**.
3. In the project setup screen:
   - **Project Name**: `groww-review-pulse` (or your choice).
   - **Framework Preset**: Leave as **Other**.
   - **Root Directory**: `./` (Default - configured automatically by [`vercel.json`](file:///c:/Users/jaya.manocha/Documents/myProjects/AI%20Agent/vercel.json)).
   - **Build and Output Settings**: Leave default.
4. Click **Deploy**.
5. In under 60 seconds, your interactive dashboard will be live at:
   ```
   https://groww-review-pulse.vercel.app
   ```

---

### Step 3: Add Pipeline Secrets to GitHub

Your automated weekly agent runs in GitHub Actions. Add your credentials so it can access Gemini and Google Workspace via MCP:

1. In your GitHub repository, navigate to **Settings > Secrets and variables > Actions**.
2. Click **New repository secret** and add the following secrets:

| Secret Name | Value | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | `AIzaSy...` | Google AI Studio API key for theme clustering & pulse synthesis |
| `LLM_PROVIDER` | `gemini` | LLM provider to use (`gemini` or `openai`) |
| `MCP_SERVER_URL` | `https://mcp-server-production-3be3.up.railway.app` | Google Workspace MCP Server endpoint |
| `PULSE_DOC_ID` | `1escKRi5Z9d6H_gZSOxAvhEragRm52d0I_a6JfGP5Mdk` | Target Google Doc ID or URL for journal logging |
| `PULSE_EMAIL_RECIPIENT` | `executives@groww.in` | Email address receiving the weekly executive summary |
| `OPENAI_API_KEY` | *(optional)* | Only needed if `LLM_PROVIDER` is set to `openai` |

3. Enable GitHub Actions write permissions so it can commit updated dashboard data:
   - Go to **Settings > Actions > General**.
   - Scroll down to **Workflow permissions**.
   - Select **Read and write permissions**.
   - Click **Save**.

---

### Step 4: Verify with a Manual Pipeline Run

You don't have to wait for Monday to verify the system:

1. In GitHub, click on the **Actions** tab.
2. In the left sidebar, click **Weekly Review Intelligence Pulse**.
3. Click the **Run workflow** dropdown button:
   - Select `dry_run: false` (for full Google Docs & Gmail publishing) or `dry_run: true` (for safe test run).
   - Click **Run workflow**.
4. The workflow will:
   - Fetch real reviews from Google Play Store.
   - Run PII scrubbing and Gemini clustering.
   - Publish to Google Docs & Gmail.
   - Commit the updated `data/pulse.json`.
5. **Watch Vercel automatically redeploy:** Open your Vercel dashboard. Within 15 seconds of the GitHub Action completing, Vercel will automatically redeploy the dashboard with the latest pulse data!

---

## Automated Schedule

Once deployed, the agent runs completely hands-free:

- **Schedule**: Every **Monday at 09:00 UTC** (14:30 IST).
- **Workflow File**: [`.github/workflows/weekly_pulse.yml`](file:///c:/Users/jaya.manocha/Documents/myProjects/AI%20Agent/.github/workflows/weekly_pulse.yml)
- **Output**:
  - Live Google Doc journal updated.
  - Executive email summary sent.
  - Vercel Web Dashboard updated with zero downtime.

---

## Troubleshooting Quick Reference

| Issue | Cause | Fix |
|---|---|---|
| **GitHub Action fails on git push** | Workflow lacks write permissions | Go to GitHub **Settings > Actions > General > Workflow permissions** and select **Read and write permissions**. |
| **Vercel dashboard shows 404** | Rewrite paths altered | Ensure [`vercel.json`](file:///c:/Users/jaya.manocha/Documents/myProjects/AI%20Agent/vercel.json) exists in the repository root. |
| **MCP server connection error** | Railway instance asleep or invalid URL | Check that `MCP_SERVER_URL` matches your active Railway deployment without trailing slashes. |
| **Play Store review fetch fails** | Temporary Google Play throttling | Pipeline automatically falls back to cached sample data so the dashboard never breaks. |
