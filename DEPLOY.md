# Deployment notes

The dashboard is a static website. The scheduled Python updater changes only `data/latest.json` and `data/history.json`; the browser then renders those values automatically.

The workflow requires these default repository permissions:

- Contents: write
- Pages: write
- ID token: write

If an organization policy disables GitHub Pages or scheduled Actions, an organization administrator may need to enable those features once.
