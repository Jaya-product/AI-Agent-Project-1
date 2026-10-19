# Groww Weekly Review Pulse — Frontend

A single-page dashboard that turns the weekly Play Store review report into an
interactive view: theme mix, theme → action mapping, gaps, and a filterable
quote explorer.

This folder is a **working reference implementation** (vanilla JS + d3 v7, no
build step). Port it to the target stack (e.g. React/Next.js) or ship it as-is.

```
groww-pulse/
├── index.html          Markup for all sections
├── styles.css          Design tokens (light + dark) and component styles
├── app.js              State store, derived values, render + interaction logic
├── data/
│   ├── pulse.json      Sample data — the API contract (see below)
│   └── pulse.js        Same data as a script, so index.html opens from disk
└── screenshots/        Desktop, dark mode, mobile, open filter menu
```

**Run it:** open `index.html` in a browser, or serve the folder
(`npx serve .`). Needs internet for d3 (cdnjs) and Google Fonts.

---

## 1. Data contract

One JSON document per week. `app.js → loadData()` reads `window.PULSE_DATA`;
replace that with a `fetch()` to your API.

```jsonc
{
  "week": "2026-10-19",
  "themes": [                      // top themes, any count (UI tuned for 3)
    { "rank": 1, "theme": "UI/UX & Ease of Use", "reviews": 27 }
  ],
  "quotes": [
    {
      "id": "q1",
      "theme": "Customer Service & Withdrawals",   // must match a themes[].theme to link
      "sentiment": "Negative",                     // "Positive" | "Negative"
      "quote": "Extremely poor customer service! ..."
    }
  ],
  "actions": [
    {
      "id": "a1",
      "rank": 1,
      "theme": "Brokerage Charges & Fees",        // may be a non-top theme → shown unlinked
      "action": "Deploy pre-trade brokerage breakdown modal ..."
    }
  ]
}
```

> **Important:** the current text report does **not** contain `quotes[].theme`,
> `quotes[].sentiment` or `actions[].theme`. They were tagged by hand for this
> sample. The report pipeline (the LLM step that writes the report) should emit
> these fields as structured JSON instead of markdown.

## 2. Derived values (computed client-side in `app.js`)

| id          | From                     | Output                                                                 |
|-------------|--------------------------|------------------------------------------------------------------------|
| `summary`   | themes                   | `total`, `top_theme`, `top_reviews`, `top_share`, `lead_margin` (#1 − #2) |
| `shares`    | themes                   | per theme: `reviews`, `share` of top-N total                           |
| `coverage`  | themes, quotes, actions  | per theme: number of linked `quotes` and `actions`                    |
| `sentiment` | quotes                   | per sentiment: `n`, `share`                                            |
| `keywords`  | quotes                   | fixed term list (withdrawals, fees, charges, support, service, updates, interface, experience, P/L, returns, funds, money, smooth, useful) → quotes mentioning each; terms with 0 hits dropped |

These can move to the backend unchanged.

## 3. Page sections

1. **Hero** — gradient band from the first three series colours.
   - Eyebrow (product · source · week), serif title.
   - Takeaway sentence built from `summary`. If `lead_margin ≤ 2` it calls out
     a near tie and lists all theme counts.
   - 4 KPI tiles, each with a coloured top border. Numbers count up once on load.
     Tiles: reviews in top 3 · lead of #1 over #2 · leading theme's share ·
     negative quote share.
2. **Theme mix** — donut (d3.pie) with the total in the centre; the centre
   switches to the selected theme's count. Below it, a legend row per theme
   showing a dot, name, count, share, share bar, and "n quotes · n actions".
3. **From complaint to fix** — themes (left) linked to actions (right) by
   curved SVG lines in the theme colour. Actions whose theme isn't a top theme
   show "not a top-3 theme" and get no line. The **gaps** box lists themes
   with 0 actions or 0 quotes.
4. **What users are saying**
   - Header: title, plus "x of y quotes" and Reset on the right.
   - **Sentence filter:** *Show [all ▾] quotes about [any theme ▾]
     mentioning [anything ▾]*. Each blank is a pill that opens a menu.
     - Sentiment menu: All / Positive / Negative.
     - Theme menu: options with colour dot and quote count.
     - "Mentioning" menu: free-text search plus keyword chips.
     - A chosen value fills the pill with its colour.
   - **Sentiment bar:** red/green split sized by count, beside the sentence.
     Click a side to filter to it; click again to clear.
   - **Quote cards:** equal-height grid (`auto-fill, minmax(18rem, 1fr)`).
     - Coloured top border and opening quote mark.
     - Text clamped to 5 lines, with "Read more" shown only if truncated.
     - Footer pinned to the bottom: theme on the left, sentiment tag on the right.
     - Search and keyword matches are highlighted with `<mark>`; cards with a
       match open fully.

## 4. Filter state & interactions

Global params: `{ theme, sentiment, kw, q }` (default `{null, 'All', null, null}`).

| Interaction                                | Effect                                                |
|--------------------------------------------|-------------------------------------------------------|
| Click donut slice / legend row / map node / theme option | toggles `theme`; everything else on the page dims |
| Hover the same elements                    | temporary highlight only (no state change)            |
| Sentiment option or sentiment-bar side     | sets `sentiment` (click again → `All`)                |
| Type in search                             | sets `q`, clears `kw`                                  |
| Click keyword chip                         | sets `kw` (toggle), clears `q`                         |
| Active-filter bar (above the grid)         | one × pill per active filter + "Clear all"             |
| Esc                                        | closes the open menu and returns focus to its pill     |
| Enter in search                            | closes the menu                                        |

Filtering is **AND** across theme, sentiment, keyword and search. Text matching
is case-insensitive substring. The empty state offers "Reset the sentence".

## 5. Design system

All values are CSS custom properties at the top of `styles.css`.

- **Fonts:** Inter (UI) and Fraunces (title, KPI numbers, donut centre, quote marks).
- **Theme colours:** `--series-1…8`, assigned by theme `rank`. The same colour
  is used for the theme everywhere: donut, legend, map, menus, quote cards.
- **Status colours:** `--color-ok` (positive), `--color-bad` (negative),
  `--color-warn` (highlights, gap markers).
- **Dark mode:** follows `prefers-color-scheme`. Force a mode with
  `<html data-theme="dark|light">`.
- **Motion:** donut pop, bar grow, link draw, card rise, KPI count-up. All of
  it is disabled under `prefers-reduced-motion`.

## 6. Responsive

- Two-column card grid collapses to one column under ~50rem.
- The voice section uses a container query at 44rem: sentence and sentiment bar
  stack, and the bar goes full width.
- Menus reposition so they stay inside the card.
- The map keeps its curves down to 400px wide.

## 7. Accessibility

- All controls are `<button>`s with `aria-pressed`, `aria-expanded` or
  `aria-checked` as appropriate, and visible focus rings.
- Menus use `role="menu"` / `menuitemradio`.
- The donut has an `aria-label`.
- Colour is never the only signal: themes are also named, and sentiment is
  shown with ▲/▼ and text.

## 8. Acceptance checklist

- [ ] Loads the week's JSON from the API; handles loading and error states
      (`.is-loading` skeleton, "Failed to load" line).
- [ ] Theme selection syncs across donut, legend, map, sentence pill and quotes.
- [ ] Sentence pills reflect the current state; Reset and "Clear all" restore defaults.
- [ ] Quote cards stay equal height; "Read more" appears only on truncated quotes.
- [ ] Works at 400px, 768px and 1280px+, in light and dark mode.
- [ ] Motion respects reduced-motion.

## Gotchas found while building

- **`all: unset` and `hidden`:** `all: unset` on a button overrides the browser's
  `[hidden]` rule. Keep the explicit `[hidden] { display: none }` rules at the
  end of `styles.css`.
- **SVG colours:** set colours that use CSS variables with
  `style.fill` / `style.stroke`, not the `fill` / `stroke` attributes.
  Attributes don't resolve `var()`.
