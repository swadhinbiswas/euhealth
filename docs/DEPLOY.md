# Deploying to Cloudflare Pages

The `site/` directory is fully static: HTML, one CSS file, one JS file, and a
generated data payload. No build step, no runtime data fetch, no external CDN.
That makes deployment a file copy.

## Recommended: GitHub Pages via Actions (zero configuration)

`.github/workflows/deploy.yml` builds the warehouse, exports the dashboard, and
publishes `site/` to GitHub Pages. Enable it in **Settings → Pages → Source:
GitHub Actions** and push to `main`.

This needs no Cloudflare account and no secrets, and it keeps the site in
version control alongside the code that generated it.

## Cloudflare Pages

**Option A — direct upload (fastest, no CI).**

```bash
# Install once
npm install -g wrangler

# Authenticate
wrangler login

# site/ is the publish root; there is no build command because there is
# nothing to build
wrangler pages deploy site --project-name eu-health-workforce
```

Wrangler prints a `*.pages.dev` URL. Add a custom domain under
**Pages → project → Custom domains**.

**Option B — connect the Git repository.**

Settings → Pages → Create project → Connect to Git, then:

| Setting | Value |
|---|---|
| Framework preset | None |
| Build command | *(leave empty)* |
| Build output directory | `site` |

Since the site is prebuilt, Pages serves `site/` verbatim. If you would rather
regenerate on every push, use the build command from
`Makefile`: `make site`, with output directory `site`.

**Option C — Workers with static assets.**

```bash
wrangler deploy
```

with a minimal `wrangler.toml`:

```toml
name = "eu-health-workforce"
compatibility_date = "2025-01-01"

[assets]
directory = "./site"
```

## After deploying

1. **Verify the numbers against the warehouse.** The site renders whatever was
   in `data/export/site/data.json` at build time. A successful HTTP 200 says
   nothing about whether the data loaded.
2. **Check the caveat block is present.** It is the part of the page that keeps
   the dashboard honest. If a redesign drops it, the site becomes misleading.
3. **Confirm light and dark mode.** The toggle reads `localStorage` and falls
   back to `prefers-color-scheme`.

## Why static

A dynamic dashboard would need a server, a database connection and a runtime
data source — which means credentials in production and a page that breaks when
Eurostat is unreachable. Generating the payload at build time means the deployed
site is a snapshot with a stated vintage, served from a CDN, and costs nothing
to host.

The trade-off is real: a deployed snapshot goes stale until the next build. For
a portfolio artefact that is the right trade; for an operational tool, wire up
Pages Functions against the warehouse instead and move the export step to a
scheduled build.