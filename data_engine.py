"""
Investment Dashboard - Data Engine
Parses valuation PDFs and cash transaction CSVs, classifies transactions,
and computes performance metrics net of contributions and withdrawals.
"""

import os
import re
import zipfile
import csv
import json
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from decimal import Decimal, ROUND_HALF_UP


# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

DATA_DIR = Path(os.environ.get("INVESTMENT_DATA_DIR", Path(__file__).parent / "data"))

# Australian financial year: 1 Jul – 30 Jun
def aus_fy(d: date) -> int:
    """Return the Australian financial year number ending in that year."""
    return d.year + 1 if d.month >= 7 else d.year  # fy2025 = 1 Jul 2024 – 30 Jun 2025

def aus_fy_quarter(d: date) -> tuple[int, int]:
    """Return (fy, quarter) for a date.  Q1=Jul-Sep, Q2=Oct-Dec, Q3=Jan-Mar, Q4=Apr-Jun."""
    fy = aus_fy(d)
    if d.month in (7, 8, 9):
        q = 1
    elif d.month in (10, 11, 12):
        q = 2
    elif d.month in (1, 2, 3):
        q = 3
    else:
        q = 4
    return fy, q

def fy_quarter_dates(fy: int, q: int) -> tuple[date, date]:
    """Start and end dates for an AUS FY quarter."""
    starts = {1: date(fy - 1, 7, 1), 2: date(fy - 1, 10, 1),
              3: date(fy, 1, 1),      4: date(fy, 4, 1)}
    ends   = {1: date(fy - 1, 9, 30), 2: date(fy - 1, 12, 31),
              3: date(fy, 3, 31),      4: date(fy, 6, 30)}
    return starts[q], ends[q]

def fy_dates(fy: int) -> tuple[date, date]:
    return date(fy - 1, 7, 1), date(fy, 6, 30)


# ─────────────────────────────────────────────────────────────────────────────
# PORTFOLIO DEFINITIONS
# ─────────────────────────────────────────────────────────────────────────────

PORTFOLIOS = {
    "RFT": {
        "name": "Ramsay Family Trust",
        "currency": "AUD",
        "cash_csvs": ["RFT_Transactions_*.csv"],
        "valuation_pdfs": ["RFT_Valuation_*.pdf"],
    },
    "Super": {
        "name": "Ramsay Family Superannuation Fund",
        "currency": "AUD",
        "cash_csvs": ["Super_AUD_Transactions_*.csv", "Super_USD_Transactions_*.csv"],
        "valuation_pdfs": [],   # no valuations provided yet
    },
    "Yasmar": {
        "name": "Yasmar Investments Pty Ltd",
        "currency": "AUD",
        "cash_csvs": ["Yasmar_Transactions_*.csv"],
        "valuation_pdfs": [],
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# TRANSACTION CLASSIFICATION
# ─────────────────────────────────────────────────────────────────────────────

# Category meanings for performance:
#   income         – investment return (interest, dividends, distributions)
#   mgmt_fee       – investment cost reducing returns
#   asset_purchase – cash → asset (neutral, value stays in portfolio)
#   asset_sale     – asset → cash (neutral, value stays in portfolio)
#   contribution   – new external capital added
#   withdrawal     – capital removed for personal/external use (fees to govt/tax are also here)
#   internal       – internal transfer between accounts (e.g. credit card repayment from portfolio)
#   fx_purchase    – AUD spent to buy foreign currency for investment
#   tax            – tax payments (ATO, land tax, etc.)
#   unknown        – needs manual review

CATEGORY_RULES = [
    # Type-level rules (applied first)
    ("GROSS_INTEREST",           "income"),
    ("DIRECT_CREDIT_DIVIDEND",   "income"),
    # Description pattern rules (case-insensitive)
    (r"distribution from",       "income"),
    (r"dividend",                "income"),
    (r"interest",                "income"),
    (r"stpk dist",               "income"),
    (r"capital.return",          "income"),          # capital returns from funds
    (r"map private wealth",      "mgmt_fee"),
    (r"map feerefund",           "mgmt_fee"),        # fee refund – treat as negative fee
    (r"smsf admin",              "mgmt_fee"),
    (r"self managed super fund", "mgmt_fee"),
    (r"^bought\b",               "asset_purchase"),
    (r"\bcall\b.*mgdfund",       "asset_purchase"),  # capital call to managed fund
    (r"mgdfund.*contribution",   "asset_purchase"),
    (r"superchoice",             "contribution"),    # super guarantee contributions
    (r"precision ch",            "contribution"),    # employer contributions
    (r"interbank credit from nat wealth",  "contribution"),
    (r"interbank credit from ato",         "income"),         # ATO refund
    (r"interbank credit from mq",          "income"),         # MQG dividend
    (r"interbank credit from super retail","income"),
    (r"\bato\b",                 "tax"),
    (r"land tax",                "tax"),
    (r"asic",                    "mgmt_fee"),
    (r"buy usd",                 "fx_purchase"),
    (r"settlement by cash_mgmt_withdrawal", "asset_purchase"),
    (r"nomad",                   "withdrawal"),      # business / personal
    (r"ramsay super",            "contribution"),    # cross-entity transfer into super
    (r"jbw cash redempt",        "internal"),        # cross-entity cash move
    (r"fix error",               "internal"),
    (r"^sold\b",                 "asset_sale"),
    (r"\bsold\b",                "asset_sale"),
    (r"settlement by cash_mgt",  "asset_sale"),
    (r"stonepeak reversal",      "asset_purchase"),
    (r"stpk dist",               "income"),
    (r"stpk div",                "income"),
    (r"stonepeak dist",          "income"),
    (r"eft to rft corp",         "internal"),
    (r"elena",                   "withdrawal"),
    (r"west\b",                  "withdrawal"),      # "West" family personal
    (r"wes\b",                   "withdrawal"),
    (r"donnington",              "withdrawal"),
    (r"to west",                 "withdrawal"),
    (r"to wes",                  "withdrawal"),
    (r"local t/t to",            "withdrawal"),
    (r"t/t fee",                 "mgmt_fee"),
    (r"interbank credit from jbwere",  "internal"),
]

def classify_transaction(tx_type: str, description: str) -> str:
    desc = description.lower()
    # Type-level match first
    for pattern, category in CATEGORY_RULES:
        if not pattern.startswith("(") and not pattern.startswith("^") and not pattern.startswith(r"\b"):
            if pattern == tx_type:
                return category
    # Description pattern match
    for pattern, category in CATEGORY_RULES:
        if pattern.startswith("(") or pattern.startswith("^") or pattern.startswith(r"\b") or re.search(pattern, desc):
            try:
                if re.search(pattern, desc):
                    return category
            except re.error:
                if pattern.lower() in desc:
                    return category
    return "unknown"


# ─────────────────────────────────────────────────────────────────────────────
# VALUATION PDF PARSER
# ─────────────────────────────────────────────────────────────────────────────

def parse_valuation_pdf(pdf_path: Path) -> dict:
    """
    Extract portfolio summary and holdings from a JBWere valuation PDF.
    These are actually zip files containing jpeg pages + txt extracts.
    Returns a dict with keys: date, portfolio_name, asset_classes, holdings, total_value
    """
    result = {"source_file": pdf_path.name, "asset_classes": {}, "holdings": []}
    
    with zipfile.ZipFile(pdf_path) as zf:
        txt_files = sorted([n for n in zf.namelist() if n.endswith(".txt")],
                           key=lambda x: int(x.replace(".txt", "")))
        pages = {}
        for name in txt_files:
            page_num = int(name.replace(".txt", ""))
            pages[page_num] = zf.read(name).decode("utf-8", errors="replace")

    # Page 1: report date and entity name
    p1 = pages.get(1, "")
    m = re.search(r"Report as at (.+?)\r?\n", p1)
    if m:
        date_str = m.group(1).strip()
        result["date"] = datetime.strptime(date_str, "%d %b %Y").date()
    m = re.search(r"(RAMSAY[^\r\n]+)\r?\n", p1)
    if m:
        result["portfolio_name"] = m.group(1).strip()

    # Page 2: Portfolio Summary table
    p2 = pages.get(2, "")
    asset_class_pattern = re.compile(
        r"(Cash|Government Bonds|Credit|Real Assets|Equity - Domestic|Equity - International|Uncorrelated Strategies)"
        r"\s+\$\s*([\d,]+\.?\d*)\s+\$\s*([\d,]+\.?\d*)\s+([\d.]+)\s*%\s+([\d.]+)\s*%\s+\$\s*([\d,]+\.?\d*)\s+(-?\$?\s*[\d,]+\.?\d*)"
    )
    for m in asset_class_pattern.finditer(p2):
        name = m.group(1)
        result["asset_classes"][name] = {
            "market_value": float(m.group(2).replace(",", "")),
            "estimated_income": float(m.group(3).replace(",", "")),
            "estimated_yield_pct": float(m.group(4)),
            "pct_of_portfolio": float(m.group(5)),
            "cost": float(m.group(6).replace(",", "")),
        }

    # Total Portfolio Value
    m = re.search(r"Net Portfolio Value\s+\$\s*([\d,]+\.?\d*)", p2)
    if m:
        result["total_value"] = float(m.group(1).replace(",", ""))
    else:
        m = re.search(r"Total Portfolio Value\s+\$\s*([\d,]+\.?\d*)", p2)
        if m:
            result["total_value"] = float(m.group(1).replace(",", ""))

    # Pages 4-6: Individual holdings
    holding_pattern = re.compile(
        r"([A-Z0-9]{4,12})\s+(.+?)\s+([\d,]+\.?\d*)\s+\$\s*([\d.]+)\s+\$\s*([\d,]+\.?\d*)"
    )
    current_asset_class = None
    asset_class_headers = {
        "Cash": "Cash",
        "Government Bonds": "Government Bonds",
        "Credit": "Credit",
        "Real Assets": "Real Assets",
        "Equity - Domestic": "Equity - Domestic",
        "Equity - International": "Equity - International",
        "Uncorrelated Strategies": "Uncorrelated Strategies",
    }
    
    for pg in [4, 5, 6]:
        page_text = pages.get(pg, "")
        for line in page_text.splitlines():
            for ac in asset_class_headers:
                if ac in line and "Total" not in line and "%" not in line[:20]:
                    current_asset_class = ac
            m = re.search(
                r"([A-Z0-9]{4,12}AU|JBWCash|GOLD|QUAL|NDQ|LSF)\s+([\w\s\-\–\,\'\.]+?)\s+([\d,]+\.?\d*)\s+\$\s*([\d.]+)\s+\$\s*([\d,]+\.?\d*)",
                line
            )
            if m and current_asset_class:
                try:
                    result["holdings"].append({
                        "asset_class": current_asset_class,
                        "code": m.group(1),
                        "description": m.group(2).strip(),
                        "quantity": float(m.group(3).replace(",", "")),
                        "price": float(m.group(4)),
                        "market_value": float(m.group(5).replace(",", "")),
                    })
                except ValueError:
                    pass

    return result


# ─────────────────────────────────────────────────────────────────────────────
# CASH TRANSACTION CSV PARSER
# ─────────────────────────────────────────────────────────────────────────────

def parse_transaction_csv(csv_path: Path, portfolio_key: str, currency: str = "AUD") -> list[dict]:
    """Parse a JBWere cash account CSV. Returns list of transaction dicts."""
    transactions = []
    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw_date = row.get(" Date", row.get("Date", "")).strip()
            if not raw_date:
                continue
            try:
                tx_date = datetime.strptime(raw_date, "%d/%m/%Y").date()
            except ValueError:
                continue
            tx_type = row.get("Type", "").strip()
            desc = row.get("Description", "").strip()
            credit = float(row.get("Credit", 0) or 0)
            debit = float(row.get("Debit", 0) or 0)
            balance = float(row.get("Balance", 0) or 0)
            amount = credit - debit   # positive = money in, negative = money out
            category = classify_transaction(tx_type, desc)
            transactions.append({
                "date": tx_date,
                "portfolio": portfolio_key,
                "currency": currency,
                "type": tx_type,
                "description": desc,
                "credit": credit,
                "debit": debit,
                "amount": amount,
                "balance": balance,
                "category": category,
                "fy": aus_fy(tx_date),
                "fy_quarter": aus_fy_quarter(tx_date),
            })
    return sorted(transactions, key=lambda x: x["date"])


# ─────────────────────────────────────────────────────────────────────────────
# FX RATE LOOKUP
# ─────────────────────────────────────────────────────────────────────────────

_fx_cache: dict[tuple, float] = {}

def get_usd_aud_rate(for_date: date | None = None) -> tuple[float, str]:
    """
    Fetch USD/AUD spot rate. Tries the Open Exchange Rates (free, no key needed)
    fallback URL, then a hardcoded recent rate. Returns (rate, source_note).
    rate = AUD per 1 USD (e.g. 1.55 means 1 USD = 1.55 AUD)
    """
    cache_key = ("USDAUD", for_date)
    if cache_key in _fx_cache:
        return _fx_cache[cache_key], "cached"
    
    # Try exchangerate-api (free, no key for latest)
    try:
        url = "https://open.er-api.com/v6/latest/USD"
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read())
        rate_aud = data["rates"]["AUD"]
        source = f"Open Exchange Rates (live, as at {date.today()})"
        _fx_cache[cache_key] = rate_aud
        return rate_aud, source
    except Exception:
        pass

    # Fallback: use a recent hardcoded rate
    fallback = 1.565  # approximate AUD per USD as at Jun 2026
    source = f"Fallback estimate: 1 USD = {fallback} AUD (update via settings)"
    _fx_cache[cache_key] = fallback
    return fallback, source


# ─────────────────────────────────────────────────────────────────────────────
# MAIN DATA LOADER
# ─────────────────────────────────────────────────────────────────────────────

class InvestmentData:
    """
    Central data store. Loads all valuations and transactions from DATA_DIR.
    """
    def __init__(self, data_dir: Path | None = None):
        self.data_dir = data_dir or DATA_DIR
        self.valuations: list[dict] = []
        self.transactions: list[dict] = []
        self.fx_rate: float = 1.0
        self.fx_source: str = ""
        self.load_errors: list[str] = []
        self._load()

    def _load(self):
        # FX rate
        self.fx_rate, self.fx_source = get_usd_aud_rate()

        # Valuations
        for pdf_path in sorted(self.data_dir.glob("*Valuation*.pdf")):
            try:
                v = parse_valuation_pdf(pdf_path)
                # Tag with portfolio key from filename
                fname = pdf_path.stem.upper()
                if "RFT" in fname:
                    v["portfolio"] = "RFT"
                elif "SUPER" in fname:
                    v["portfolio"] = "Super"
                elif "YASMAR" in fname:
                    v["portfolio"] = "Yasmar"
                else:
                    v["portfolio"] = "Unknown"
                self.valuations.append(v)
            except Exception as e:
                self.load_errors.append(f"Valuation {pdf_path.name}: {e}")

        self.valuations.sort(key=lambda v: v.get("date", date.min))

        # Transactions
        csv_map = {
            "RFT":    [("RFT*Transactions*.csv", "AUD"),
                       ("RFT Transactions*.csv", "AUD")],
            "Super":  [("Super*AUD*Transactions*.csv", "AUD"),
                       ("Super AUD Transactions*.csv", "AUD"),
                       ("Super*USD*Transactions*.csv", "USD"),
                       ("Super USD Transactions*.csv", "USD")],
            "Yasmar": [("Yasmar*Transactions*.csv", "AUD"),
                       ("Yasmar Transactions*.csv", "AUD")],
        }
        for portfolio_key, patterns in csv_map.items():
            for pattern, currency in patterns:
                for csv_path in sorted(self.data_dir.glob(pattern)):
                    try:
                        txs = parse_transaction_csv(csv_path, portfolio_key, currency)
                        # Convert USD amounts to AUD
                        if currency == "USD":
                            for tx in txs:
                                tx["amount_aud"] = tx["amount"] * self.fx_rate
                                tx["credit_aud"] = tx["credit"] * self.fx_rate
                                tx["debit_aud"]  = tx["debit"]  * self.fx_rate
                        else:
                            for tx in txs:
                                tx["amount_aud"] = tx["amount"]
                                tx["credit_aud"] = tx["credit"]
                                tx["debit_aud"]  = tx["debit"]
                        self.transactions.extend(txs)
                    except Exception as e:
                        self.load_errors.append(f"CSV {csv_path.name}: {e}")

        self.transactions.sort(key=lambda x: x["date"])

    # ─────────────────────── Query helpers ───────────────────────

    def valuations_for(self, portfolio: str | None = None) -> list[dict]:
        if portfolio:
            return [v for v in self.valuations if v.get("portfolio") == portfolio]
        return self.valuations

    def transactions_for(self, portfolio: str | None = None,
                         start: date | None = None,
                         end: date | None = None) -> list[dict]:
        txs = self.transactions
        if portfolio:
            txs = [t for t in txs if t["portfolio"] == portfolio]
        if start:
            txs = [t for t in txs if t["date"] >= start]
        if end:
            txs = [t for t in txs if t["date"] <= end]
        return txs

    def valuation_at_or_before(self, portfolio: str, target_date: date) -> dict | None:
        vals = [v for v in self.valuations_for(portfolio) if v.get("date") <= target_date]
        return vals[-1] if vals else None

    def valuation_at_or_after(self, portfolio: str, target_date: date) -> dict | None:
        vals = [v for v in self.valuations_for(portfolio) if v.get("date") >= target_date]
        return vals[0] if vals else None

    # ─────────────────────── Performance engine ───────────────────────

    def performance(self, portfolio: str | None, start: date, end: date) -> dict:
        """
        Compute true investment performance between two dates, net of
        contributions and withdrawals, using a time-weighted approach.

        Returns:
            opening_value      – portfolio market value at start
            closing_value      – portfolio market value at end
            contributions      – external capital added during period
            withdrawals        – capital removed during period (personal + tax)
            mgmt_fees          – management fees paid
            income             – dividends, interest, distributions received
            net_return_$       – closing - opening - contributions + withdrawals + fees
            net_return_pct     – net_return_$ / adjusted opening capital
            asset_class_breakdown – from closing valuation
        """
        portfolios = [portfolio] if portfolio else list(PORTFOLIOS.keys())
        result = {
            "start": start, "end": end, "portfolios": portfolios,
            "opening_value": 0.0, "closing_value": 0.0,
            "contributions": 0.0, "withdrawals": 0.0,
            "mgmt_fees": 0.0, "income": 0.0, "tax": 0.0,
            "asset_purchase": 0.0, "asset_sale": 0.0,
            "valuation_dates_used": {},
            "asset_class_breakdown": {},
            "holdings_detail": {},
        }

        for p in portfolios:
            # Opening valuation: closest on or before start
            v_open = self.valuation_at_or_before(p, start)
            # Closing valuation: closest on or before end
            v_close = self.valuation_at_or_before(p, end)

            if v_open:
                result["opening_value"] += v_open.get("total_value", 0.0)
                result["valuation_dates_used"][f"{p}_open"] = v_open.get("date")
            if v_close:
                result["closing_value"] += v_close.get("total_value", 0.0)
                result["valuation_dates_used"][f"{p}_close"] = v_close.get("date")
                # Asset class breakdown from closing valuation
                for ac, ac_data in v_close.get("asset_classes", {}).items():
                    if ac not in result["asset_class_breakdown"]:
                        result["asset_class_breakdown"][ac] = {"market_value": 0.0, "pct": 0.0}
                    result["asset_class_breakdown"][ac]["market_value"] += ac_data["market_value"]
                # Holdings from closing valuation
                for h in v_close.get("holdings", []):
                    key = h["code"]
                    if key not in result["holdings_detail"]:
                        result["holdings_detail"][key] = dict(h)
                    else:
                        result["holdings_detail"][key]["market_value"] += h["market_value"]

            # Transactions between the two valuation dates used (not start/end exactly)
            v_open_date = v_open["date"] if v_open else start
            v_close_date = v_close["date"] if v_close else end
            txs = self.transactions_for(p, v_open_date + timedelta(days=1), v_close_date)
            
            for tx in txs:
                amt = tx["amount_aud"]
                cat = tx["category"]
                if cat == "income":
                    result["income"] += tx["credit_aud"]
                elif cat == "mgmt_fee":
                    if tx["credit_aud"] > 0:  # fee refund
                        result["mgmt_fees"] -= tx["credit_aud"]
                    else:
                        result["mgmt_fees"] += tx["debit_aud"]
                elif cat == "contribution":
                    result["contributions"] += tx["credit_aud"]
                elif cat == "withdrawal":
                    result["withdrawals"] += tx["debit_aud"]
                elif cat == "tax":
                    result["tax"] += tx["debit_aud"]
                elif cat == "asset_purchase":
                    result["asset_purchase"] += tx["debit_aud"] - tx["credit_aud"]
                elif cat == "asset_sale":
                    result["asset_sale"] += tx["credit_aud"]
                # internal / fx_purchase / unknown: excluded from performance

        # Recalculate asset class percentages
        total_cv = result["closing_value"]
        if total_cv > 0:
            for ac in result["asset_class_breakdown"]:
                result["asset_class_breakdown"][ac]["pct"] = (
                    result["asset_class_breakdown"][ac]["market_value"] / total_cv * 100
                )

        # Net gain = closing - opening - net new capital
        # Net new capital = contributions - withdrawals - tax - fees
        # (fees reduce returns, withdrawals/tax are capital out)
        net_capital_movement = result["contributions"] - result["withdrawals"] - result["tax"] - result["mgmt_fees"]
        result["net_return_$"] = result["closing_value"] - result["opening_value"] - net_capital_movement
        
        # Denominator: average invested capital (simple approximation)
        avg_capital = (result["opening_value"] + max(result["opening_value"] + net_capital_movement, 0)) / 2
        result["net_return_pct"] = (result["net_return_$"] / avg_capital * 100) if avg_capital > 0 else 0.0
        
        # Annualise if period > 30 days
        days = (result["valuation_dates_used"].get(f"{portfolios[0]}_close", end) -
                result["valuation_dates_used"].get(f"{portfolios[0]}_open", start)).days
        result["period_days"] = days
        if days > 0:
            result["annualised_return_pct"] = (
                ((1 + result["net_return_pct"] / 100) ** (365 / days) - 1) * 100
            )
        else:
            result["annualised_return_pct"] = 0.0

        return result

    def all_periods(self, portfolio: str | None = None) -> dict:
        """Pre-compute performance for all available quarters and FYs."""
        periods = {}
        # Determine available FYs from valuations
        all_vals = self.valuations_for(portfolio)
        if not all_vals:
            return periods
        min_date = all_vals[0]["date"]
        max_date = all_vals[-1]["date"]
        min_fy = aus_fy(min_date)
        max_fy = aus_fy(max_date)
        
        for fy in range(min_fy, max_fy + 1):
            fy_start, fy_end = fy_dates(fy)
            actual_end = min(fy_end, max_date)
            if fy_start > max_date:
                continue
            key = f"FY{fy}"
            periods[key] = self.performance(portfolio, fy_start, actual_end)
            periods[key]["label"] = f"FY{fy} ({fy_start.strftime('%d %b %Y')} – {actual_end.strftime('%d %b %Y')})"
            periods[key]["period_type"] = "fy"
            
            for q in [1, 2, 3, 4]:
                q_start, q_end = fy_quarter_dates(fy, q)
                if q_start > max_date or q_end < min_date:
                    continue
                actual_qend = min(q_end, max_date)
                qkey = f"FY{fy}Q{q}"
                periods[qkey] = self.performance(portfolio, q_start, actual_qend)
                periods[qkey]["label"] = f"FY{fy} Q{q} ({q_start.strftime('%d %b %Y')} – {actual_qend.strftime('%d %b %Y')})"
                periods[qkey]["period_type"] = "quarter"

        return periods

    def transaction_summary(self, portfolio: str | None, start: date, end: date) -> list[dict]:
        """Return categorised transactions for a period, with AUD amounts."""
        return self.transactions_for(portfolio, start, end)

    def available_portfolios_with_valuations(self) -> list[str]:
        return list({v["portfolio"] for v in self.valuations})
