# Investment Dashboard

A Streamlit web app for analysing JBWere investment portfolio performance
across multiple entities (RFT, Super, Yasmar).

## Deploying on Streamlit Community Cloud

1. Push this repo to GitHub (the two `.py` files + `requirements.txt`)
2. Go to [share.streamlit.io](https://share.streamlit.io) and sign in with GitHub
3. Click **New app** → select your repo → set main file to `streamlit_app.py`
4. Under **Advanced settings → Secrets**, add your data files path (see below)
5. Click **Deploy**

> **Important:** Your data files (PDFs and CSVs) must be uploaded to the repo or
> provided via Streamlit secrets / environment variables. See "Data files" below.

## Data files

Place the following files in a `data/` subfolder in this repo, **or** set the
environment variable `INVESTMENT_DATA_DIR` to point to wherever they live:

| File pattern                    | Description                        |
|---------------------------------|------------------------------------|
| `RFT_Valuation_XXXXXX.pdf`      | RFT portfolio valuations           |
| `Super_Valuation_XXXXXX.pdf`    | Super valuations (when available)  |
| `Yasmar_Valuation_XXXXXX.pdf`   | Yasmar valuations (when available) |
| `RFT_Transactions_*.csv`        | RFT cash transactions              |
| `Super_AUD_Transactions_*.csv`  | Super AUD transactions             |
| `Super_USD_Transactions_*.csv`  | Super USD transactions             |
| `Yasmar_Transactions_*.csv`     | Yasmar transactions                |

## Running locally

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

Then open http://localhost:8501 in your browser.

## Adding new portfolios

Drop new valuation PDFs into the `data/` folder using the naming convention
`Super_Valuation_YYMMDD.pdf` or `Yasmar_Valuation_YYMMDD.pdf` and redeploy.
The app auto-discovers all matching files.
