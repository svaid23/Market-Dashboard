# India Market Cap Opportunity Dashboard

Automated HTML dashboard for comparing:

- NIFTY 50 (Large Cap)
- NIFTY MIDCAP 100 (Mid Cap)
- NIFTY SMALLCAP 250 (Small Cap)

The dashboard deliberately avoids forecasting future EPS growth or future P/E. It scores observable evidence:

1. **Valuation (4 points)** - current P/E vs own 5-year median.
2. **Actual earnings momentum (4 points)** - year-on-year change in index trailing earnings, derived as Index Level / P/E.
3. **Relative price momentum (2 points)** - 6-month and 1-year performance vs NIFTY 50.

## Automatic refresh

The GitHub Actions workflow runs on the **1st and 16th of each month at 17:45 IST**. It:

1. Pulls official Nifty Indices historical price and valuation data.
2. Recalculates all dashboard metrics and scores.
3. Preserves the last good data if the live source is temporarily unavailable.
4. Adds a history point for trend tracking.
5. Publishes the updated site to GitHub Pages.

No data entry is required after deployment.

## One-time GitHub Pages setup

1. Create a GitHub repository and put these files in its root.
2. In **Settings > Pages**, set **Source** to **GitHub Actions**.
3. Open the repository's **Actions** tab and run **Refresh dashboard** once using **Run workflow**.
4. After that, the refresh is scheduled automatically.

## Data sources

- https://www.niftyindices.com/reports/historical-data
- https://www.niftyindices.com/resources/index-concepts/price-earnings-ratio

The historical-data page publishes index price data and P/E, P/B and dividend-yield history. Nifty Indices defines index P/E using trailing four-quarter earnings of index constituents.

## Important

This is a research-priority framework, not a buy/sell signal or return forecast. Index constituents can change over time, so changes in derived index earnings can reflect both company earnings and index composition changes.
