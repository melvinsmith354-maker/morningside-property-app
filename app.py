import math
import sqlite3

import pandas as pd
import streamlit as st

from scraper import DB_NAME, DEAL_PERCENT, init_db, run_scraper

AREA = "Morningside"

st.set_page_config(
    page_title="P24 Rate Hunter",
    page_icon="🏡",
    layout="wide",
)

init_db()

st.title("🏡 Property24 Rate-per-m² Market Dashboard")
st.caption("Full pagination • real-market filtering • lowest 5% deal monitor")

if st.button("🚀 Run Scraper & Market Engine", type="primary"):
    try:
        with st.spinner("Scraping every Property24 result page until no new listings remain..."):
            summaries = run_scraper()

        if summaries:
            result = summaries[0]
            st.success(
                f"Finished: {result['total_raw']:,} unique listings captured across "
                f"{result['pages_scraped']:,} pages; {result['total_clean']:,} valid listings."
            )
        else:
            st.success("Analysis complete.")

        st.rerun()
    except Exception as exc:
        st.error("Scraper failed.")
        st.exception(exc)

st.divider()

stats_df = pd.DataFrame()
raw_df = pd.DataFrame()

try:
    with sqlite3.connect(DB_NAME) as conn:
        stats_df = pd.read_sql_query(
            """
            SELECT
                area,
                total_raw,
                total_clean,
                reported_total,
                pages_scraped,
                real_min,
                real_max,
                real_median,
                bottom_5_cutoff,
                last_scraped_at
            FROM area_stats
            WHERE area = ?
            """,
            conn,
            params=(AREA,),
        )

        raw_df = pd.read_sql_query(
            """
            SELECT
                id,
                area,
                title,
                price,
                sqm,
                rate_sqm,
                url
            FROM raw_listings
            WHERE area = ?
            """,
            conn,
            params=(AREA,),
        )
except Exception as exc:
    st.error("Could not read dashboard data.")
    st.exception(exc)

if stats_df.empty:
    st.info("👋 Click **🚀 Run Scraper & Market Engine** to build the market snapshot.")
    st.stop()

stats = stats_df.iloc[0]

total_raw = int(stats["total_raw"] or 0)
total_clean = int(stats["total_clean"] or 0)
reported_total = None if pd.isna(stats["reported_total"]) else int(stats["reported_total"])
pages_scraped = int(stats["pages_scraped"] or 0)

st.subheader("📊 Morningside Market Summary")

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Total Listings", f"{total_raw:,}")
c2.metric("Valid Listings", f"{total_clean:,}")
c3.metric("Minimum Real Listing", f"R {stats['real_min']:,.2f} / m²")
c4.metric("Maximum Real Listing", f"R {stats['real_max']:,.2f} / m²")
c5.metric("Median Real Listing", f"R {stats['real_median']:,.2f} / m²")

if reported_total is not None:
    if total_raw < reported_total:
        st.warning(
            f"⚠️ Property24 reports {reported_total:,} results, but this scrape captured only "
            f"{total_raw:,} unique listing IDs across {pages_scraped:,} pages. "
            "Do not trust the market statistics until those counts are close."
        )
    else:
        st.caption(
            f"Property24 reported {reported_total:,} results. "
            f"The scraper captured {total_raw:,} unique listings across {pages_scraped:,} pages."
        )
else:
    st.caption(f"Scraped {total_raw:,} unique listings across {pages_scraped:,} pages.")

st.divider()

if raw_df.empty or total_clean == 0:
    st.info("No valid market listings are available yet.")
    st.stop()

clean_df = raw_df.dropna(subset=["price", "sqm", "rate_sqm", "url"]).copy()
clean_df = clean_df[
    (clean_df["sqm"] > 0)
    & (clean_df["rate_sqm"] >= stats["real_min"])
    & (clean_df["rate_sqm"] <= stats["real_max"])
].copy()
clean_df = clean_df.sort_values("rate_sqm", ascending=True).reset_index(drop=True)

# Recalculate dashboard ranking from the exact current clean snapshot.
clean_count = len(clean_df)
clean_df["rank_num"] = clean_df.index + 1
clean_df["percentile"] = clean_df["rank_num"] / clean_count * 100
clean_df["below_median_pct"] = (
    (float(stats["real_median"]) - clean_df["rate_sqm"]) / float(stats["real_median"]) * 100
)

deal_count = max(1, math.ceil(clean_count * DEAL_PERCENT / 100))
deals_df = clean_df.head(deal_count).copy()

st.subheader(f"🔥 Lowest {DEAL_PERCENT}% by R/m² ({len(deals_df):,} Listings)")
st.caption(
    f"Current lowest-{DEAL_PERCENT}% ceiling: "
    f"R {float(stats['bottom_5_cutoff']):,.2f} / m²"
    if not pd.isna(stats["bottom_5_cutoff"])
    else ""
)

for _, row in deals_df.iterrows():
    st.markdown(f"### 📍 [{row['title']}]({row['url']})")

    d1, d2, d3, d4, d5 = st.columns(5)
    d1.metric("Price", f"R {row['price']:,.0f}")
    d2.metric("Size", f"{row['sqm']:,.0f} m²")
    d3.metric("Rate / m²", f"R {row['rate_sqm']:,.2f}")
    d4.metric("Below Median", f"{row['below_median_pct']:.1f}%")
    d5.metric(
        "Percentile",
        f"{row['percentile']:.2f}%",
        help=f"Rank #{int(row['rank_num'])} of {clean_count:,} valid listings",
    )
    st.caption(f"Rank #{int(row['rank_num'])} of {clean_count:,} valid listings")
    st.divider()

with st.expander(f"👁️ View All {clean_count:,} Valid {AREA} Listings"):
    display_df = clean_df[
        [
            "rank_num",
            "percentile",
            "below_median_pct",
            "title",
            "price",
            "sqm",
            "rate_sqm",
            "url",
        ]
    ].copy()

    display_df = display_df.rename(
        columns={
            "rank_num": "Rank",
            "percentile": "Percentile %",
            "below_median_pct": "% Below Median",
            "title": "Property",
            "price": "Price",
            "sqm": "Size m²",
            "rate_sqm": "Rate / m²",
            "url": "Property24 Link",
        }
    )

    display_df["Percentile %"] = display_df["Percentile %"].round(2)
    display_df["% Below Median"] = display_df["% Below Median"].round(1)
    display_df["Price"] = display_df["Price"].round(0)
    display_df["Size m²"] = display_df["Size m²"].round(0)
    display_df["Rate / m²"] = display_df["Rate / m²"].round(2)

    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Property24 Link": st.column_config.LinkColumn("Property24 Link", display_text="Open listing"),
        },
    )

st.caption(
    "Telegram rule: a listing is eligible for one alert only if it was in the lowest 5% "
    "when the system first discovered that Property24 listing ID. Successful alerts are marked "
    "and are not sent again."
)
