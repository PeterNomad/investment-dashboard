"""
Investment Dashboard - Streamlit Application
Run locally:  streamlit run streamlit_app.py
Deploy:       push to GitHub, connect to share.streamlit.io
Data files:   place PDFs and CSVs in a data/ subfolder next to this file
"""

import os
import sys
from pathlib import Path
from datetime import date

import streamlit as st
import plotly.graph_objects as go
import pandas as pd

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Investment Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    .metric-card {
        background: #1e2235;
        border-radius: 10px;
        padding: 16px 20px;
        border: 1px solid #2d3152;
        text-align: center;
    }
    .metric-label {
        font-size: 11px;
        color: #8892a4;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        margin-bottom: 6px;
    }
    .metric-value {
        font-size: 22px;
        font-weight: 700;
        color: #e2e8f0;
    }
    .section-header {
        font-size: 16px;
        font-weight: 600;
        color: #e2e8f0;
        margin-bottom: 12px;
        padding-bottom: 6px;
        border-bottom: 1px solid #2d3152;
    }
</style>
""", unsafe_allow_html=True)

sys.path.insert(0, str(Path(__file__).parent))
from data_engine import InvestmentData, PORTFOLIOS, aus_fy

# ── Colours ───────────────────────────────────────────────────────────────────
AC_COLOURS = {
    "Cash":                    "#94a3b8",
    "Government Bonds":        "#60a5fa",
    "Credit":                  "#818cf8",
    "Real Assets":             "#f59e0b",
    "Equity - Domestic":       "#34d399",
    "Equity - International":  "#4f8ef7",
    "Uncorrelated Strategies": "#e879f9",
}
PLOT_BG  = "rgba(0,0,0,0)"
GRID_CLR = "#2d3152"
TEXT_CLR = "#e2e8f0"
GREEN    = "#34d399"
RED      = "#ef4444"
ACCENT   = "#4f8ef7"

def plot_layout(fig, height=320, **kwargs):
    fig.update_layout(
        paper_bgcolor=PLOT_BG, plot_bgcolor=PLOT_BG,
        font=dict(color=TEXT_CLR, size=11),
        height=height,
        margin=dict(t=20, b=10, l=50, r=10),
        legend=dict(bgcolor="rgba(0,0,0,0)"),
        **kwargs,
    )
    fig.update_xaxes(gridcolor=GRID_CLR, linecolor=GRID_CLR)
    fig.update_yaxes(gridcolor=GRID_CLR, linecolor=GRID_CLR)
    return fig

def fmt_dollar(v, show_sign=False):
    if v is None: return "—"
    sign = "+" if (show_sign and v >= 0) else ""
    s = f"${abs(v):,.0f}"
    return f"({s})" if v < 0 else f"{sign}{s}"

def fmt_pct(v):
    if v is None: return "—"
    return f"{'+'if v>=0 else''}{v:.1f}%"

def metric_card(label, value_html):
    return f"""<div class="metric-card">
        <div class="metric-label">{label}</div>
        <div class="metric-value">{value_html}</div>
    </div>"""


# ── Load data (cached so it only runs once per deploy) ────────────────────────
@st.cache_resource(show_spinner="Loading portfolio data…")
def load_data() -> InvestmentData:
    data_dir = Path(__file__).parent / "data"
    return InvestmentData(data_dir)

data = load_data()

# Debug: show exactly what files were found
data_dir = Path(__file__).parent / "data"
st.write("**Debug — data dir:**", str(data_dir))
st.write("**Exists:**", data_dir.exists())
if data_dir.exists():
    all_files = list(data_dir.iterdir())
    st.write("**Files found:**", [f.name for f in all_files])
st.write("**Valuations loaded:**", len(data.valuations))
st.write("**Transactions loaded:**", len(data.transactions))
if data.load_errors:
    st.write("**Errors:**", data.load_errors)

if not data.valuations:
    st.error("No valuation files found in the `data/` folder. "
             "Add your PDFs and CSVs there and redeploy.")
    st.stop()


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("📈 Investment Dashboard")
    st.caption("Ramsay Family Investment Portfolio")
    st.divider()

    # Portfolio selector — only show portfolios that have valuation data
    port_options = {"ALL": "All Portfolios (Consolidated)"}
    for k, cfg in PORTFOLIOS.items():
        port_options[k] = cfg["name"]
    available = ["ALL"] + data.available_portfolios_with_valuations()
    port_options = {k: v for k, v in port_options.items() if k in available}

    selected_portfolio = st.selectbox(
        "Portfolio",
        options=list(port_options.keys()),
        format_func=lambda k: port_options[k],
        index=1 if len(port_options) > 1 else 0,
    )

    st.divider()
    period_type = st.radio("Period View", ["Quarterly", "Annual"], horizontal=True)

    p_key = None if selected_portfolio == "ALL" else selected_portfolio
    periods = data.all_periods(p_key)
    type_key = "quarter" if period_type == "Quarterly" else "fy"
    period_opts = {k: v for k, v in periods.items() if v["period_type"] == type_key}

    if not period_opts:
        st.warning("No periods with data available.")
        st.stop()

    period_keys = sorted(period_opts.keys())
    selected_idx = st.selectbox(
        "Period",
        options=range(len(period_keys)),
        format_func=lambda i: period_opts[period_keys[i]]["label"],
        index=len(period_keys) - 1,
    )
    perf = period_opts[period_keys[selected_idx]]

    st.divider()

    # Data files summary
    pdfs = sorted(p.name for p in (Path(__file__).parent / "data").glob("*Valuation*.pdf"))
    csvs = sorted(p.name for p in (Path(__file__).parent / "data").glob("*.csv"))
    with st.expander(f"📁 Data files ({len(pdfs)} valuations, {len(csvs)} CSVs)"):
        for n in pdfs: st.caption(f"📄 {n}")
        for n in csvs: st.caption(f"📊 {n}")

    st.divider()
    st.caption(f"**FX:** 1 USD = {data.fx_rate:.4f} AUD")
    st.caption(f"*{data.fx_source[:55]}*")

    if data.load_errors:
        with st.expander(f"⚠ {len(data.load_errors)} warning(s)"):
            for e in data.load_errors:
                st.caption(e)


# ── Main dashboard ────────────────────────────────────────────────────────────
port_name = port_options[selected_portfolio]
st.markdown(f"## {port_name}")
st.caption(perf.get("label", ""))

opening   = perf.get("opening_value", 0)
closing   = perf.get("closing_value", 0)
net_ret_d = perf.get("net_return_$", 0)
net_ret_p = perf.get("net_return_pct", 0)
ann_p     = perf.get("annualised_return_pct", 0)
income    = perf.get("income", 0)
fees      = perf.get("mgmt_fees", 0)
tax       = perf.get("tax", 0)
contribs  = perf.get("contributions", 0)
withdrwls = perf.get("withdrawals", 0)

# ── KPI row 1 ─────────────────────────────────────────────────────────────────
cols = st.columns(5)
for col, (label, val, coloured) in zip(cols, [
    ("Opening Value",  fmt_dollar(opening),         False),
    ("Closing Value",  fmt_dollar(closing),         False),
    ("Net Return ($)", fmt_dollar(net_ret_d, True), True),
    ("Net Return (%)", fmt_pct(net_ret_p),          True),
    ("Annualised (%)", fmt_pct(ann_p),              True),
]):
    with col:
        if coloured:
            num = net_ret_d if "($)" in label else (net_ret_p if "(%)" in label else ann_p)
            c = GREEN if num >= 0 else RED
            html = f'<span style="color:{c}">{val}</span>'
        else:
            html = val
        st.markdown(metric_card(label, html), unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)

# ── KPI row 2 ─────────────────────────────────────────────────────────────────
cols2 = st.columns(5)
for col, (label, val) in zip(cols2, [
    ("Income Received", fmt_dollar(income)),
    ("Mgmt Fees",       fmt_dollar(fees)),
    ("Tax Paid",        fmt_dollar(tax)),
    ("Contributions",   fmt_dollar(contribs)),
    ("Withdrawals",     fmt_dollar(withdrwls)),
]):
    with col:
        st.markdown(metric_card(label, val), unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)

# ── Asset allocation + returns bar ────────────────────────────────────────────
col_l, col_r = st.columns(2)

with col_l:
    st.markdown('<div class="section-header">Asset Allocation — Closing Valuation</div>',
                unsafe_allow_html=True)
    breakdown = perf.get("asset_class_breakdown", {})
    if breakdown:
        labels  = list(breakdown.keys())
        values  = [breakdown[ac]["market_value"] for ac in labels]
        colours = [AC_COLOURS.get(ac, "#888") for ac in labels]
        fig = go.Figure(go.Pie(
            labels=labels, values=values, hole=0.45,
            marker=dict(colors=colours, line=dict(color="#0f1117", width=2)),
            textinfo="label+percent", textfont=dict(size=10),
            hovertemplate="<b>%{label}</b><br>$%{value:,.0f}<br>%{percent}<extra></extra>",
        ))
        fig.add_annotation(text=f"<b>{fmt_dollar(closing)}</b>", x=0.5, y=0.5,
                           font=dict(size=13, color=TEXT_CLR), showarrow=False)
        plot_layout(fig, height=300, showlegend=False, margin=dict(t=10,b=10,l=10,r=10))
        st.plotly_chart(fig, use_container_width=True)

with col_r:
    label_type = "Quarterly" if period_type == "Quarterly" else "Annual"
    st.markdown(f'<div class="section-header">{label_type} Returns</div>',
                unsafe_allow_html=True)
    all_same = {k: v for k, v in periods.items() if v["period_type"] == type_key}
    if all_same:
        bk = sorted(all_same.keys())
        bl = [all_same[k]["label"].split("(")[0].strip() for k in bk]
        br = [all_same[k].get("net_return_pct", 0) for k in bk]
        fig2 = go.Figure(go.Bar(
            x=bl, y=br,
            marker_color=[GREEN if r >= 0 else RED for r in br],
            text=[fmt_pct(r) for r in br], textposition="outside",
            textfont=dict(size=9),
            hovertemplate="<b>%{x}</b><br>Return: %{y:.1f}%<extra></extra>",
        ))
        fig2.add_hline(y=0, line_color=GRID_CLR, line_width=1)
        plot_layout(fig2, height=300)
        fig2.update_yaxes(ticksuffix="%")
        st.plotly_chart(fig2, use_container_width=True)

# ── Portfolio value over time ─────────────────────────────────────────────────
st.markdown('<div class="section-header">Portfolio Value Over Time</div>',
            unsafe_allow_html=True)
fig3 = go.Figure()
port_keys_show = ([selected_portfolio] if selected_portfolio != "ALL"
                  else data.available_portfolios_with_valuations())
for pk in port_keys_show:
    pvs = data.valuations_for(pk)
    if pvs:
        fig3.add_trace(go.Scatter(
            x=[v["date"] for v in pvs],
            y=[v.get("total_value", 0) for v in pvs],
            mode="lines+markers", name=PORTFOLIOS.get(pk, {}).get("name", pk),
            line=dict(width=2), marker=dict(size=8),
            hovertemplate=f"<b>{pk}</b><br>%{{x|%d %b %Y}}<br>$%{{y:,.0f}}<extra></extra>",
        ))
all_dates = [v["date"] for v in data.valuations]
if all_dates:
    min_d, max_d = min(all_dates), max(all_dates)
    for fy_yr in range(aus_fy(min_d), aus_fy(max_d) + 2):
        fy_end = date(fy_yr, 6, 30)
        if min_d <= fy_end <= max_d:
            fig3.add_vline(x=str(fy_end), line_dash="dot", line_color=GRID_CLR,
                           annotation_text=f"FY{fy_yr}", annotation_font_size=9,
                           annotation_font_color="#8892a4")
plot_layout(fig3, height=260, hovermode="x unified")
fig3.update_yaxes(tickprefix="$", tickformat=",.0f")
st.plotly_chart(fig3, use_container_width=True)

# ── Waterfall ─────────────────────────────────────────────────────────────────
st.markdown('<div class="section-header">Cash Flow Waterfall</div>',
            unsafe_allow_html=True)
fig4 = go.Figure(go.Waterfall(
    orientation="v",
    measure=["absolute","relative","relative","relative","relative","relative","absolute"],
    x=["Opening Value","Income","Mgmt Fees","Tax","Contributions","Withdrawals","Closing Value"],
    y=[opening, income, -fees, -tax, contribs, -withdrwls, closing],
    connector=dict(line=dict(color=GRID_CLR, width=1)),
    increasing=dict(marker=dict(color=GREEN)),
    decreasing=dict(marker=dict(color=RED)),
    totals=dict(marker=dict(color=ACCENT)),
    texttemplate="%{y:$,.0f}", textposition="outside",
    textfont=dict(size=9, color=TEXT_CLR),
))
plot_layout(fig4, height=300)
fig4.update_yaxes(tickprefix="$", tickformat=",.0f")
st.plotly_chart(fig4, use_container_width=True)

# ── Transaction table ─────────────────────────────────────────────────────────
st.markdown('<div class="section-header">Transaction Detail</div>',
            unsafe_allow_html=True)
start_d  = date.fromisoformat(str(perf.get("start")))
end_d    = date.fromisoformat(str(perf.get("end")))
p_filter = None if selected_portfolio == "ALL" else selected_portfolio
txs      = data.transaction_summary(p_filter, start_d, end_d)

cat_options = ["All"] + sorted({t["category"] for t in txs})
cat_filter  = st.selectbox("Filter by category", cat_options)
if cat_filter != "All":
    txs = [t for t in txs if t["category"] == cat_filter]

if txs:
    df = pd.DataFrame([{
        "Date":         t["date"].strftime("%d %b %Y"),
        "Portfolio":    t["portfolio"],
        "Category":     t["category"].replace("_", " ").title(),
        "Description":  t["description"][:80],
        "Credit (AUD)": t.get("credit_aud", t["credit"]),
        "Debit (AUD)":  t.get("debit_aud",  t["debit"]),
    } for t in sorted(txs, key=lambda x: x["date"], reverse=True)[:300]])
    st.dataframe(df, use_container_width=True, hide_index=True,
                 column_config={
                     "Credit (AUD)": st.column_config.NumberColumn(format="$%.2f"),
                     "Debit (AUD)":  st.column_config.NumberColumn(format="$%.2f"),
                 })
    st.caption(f"{len(df)} transactions")
else:
    st.info("No transactions for this filter.")

# ── Holdings table ────────────────────────────────────────────────────────────
st.markdown('<div class="section-header">Holdings at Closing Valuation</div>',
            unsafe_allow_html=True)
holdings = perf.get("holdings_detail", {})
if holdings:
    df_h = pd.DataFrame([{
        "Code":         code,
        "Description":  h.get("description", "")[:50],
        "Asset Class":  h.get("asset_class", ""),
        "Quantity":     h.get("quantity", 0),
        "Price":        h.get("price", 0),
        "Market Value": h.get("market_value", 0),
    } for code, h in sorted(holdings.items(), key=lambda x: -x[1].get("market_value", 0))])
    st.dataframe(df_h, use_container_width=True, hide_index=True,
                 column_config={
                     "Quantity":     st.column_config.NumberColumn(format="%.2f"),
                     "Price":        st.column_config.NumberColumn(format="$%.4f"),
                     "Market Value": st.column_config.NumberColumn(format="$%.0f"),
                 })
else:
    st.info("No holdings data for this period.")
