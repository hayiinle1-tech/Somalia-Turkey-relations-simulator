# Somalia–Türkiye Relations Simulator

A website that simulates future Somalia–Türkiye relationship scenarios, with data refreshed daily from the web.

## Put it online (GitHub Pages, free)
1. Create a new public repository on github.com and upload everything in this folder, including the hidden `.github` folder.
2. Repository **Settings → Pages**: Source "Deploy from a branch", branch `main`, folder `/ (root)`. Save.
3. Repository **Settings → Actions → General → Workflow permissions**: choose "Read and write permissions". Save.
4. **Actions** tab → "Refresh Somalia–Türkiye data" → **Run workflow** once to create the first `events.json`.
5. Your site is at `https://<your-username>.github.io/<repo-name>/`. It updates itself every day at 06:17 Mogadishu time.

The page loads the bundled dataset first, then merges the newest `events.json` from the same site automatically.

## Run the scraper by hand
    pip install requests
    python somalia_turkey_scraper.py --days 90
