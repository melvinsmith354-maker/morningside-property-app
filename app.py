import os
import sqlite3
import pandas as pd
import streamlit as st
from scraper import init_db, run_scraper

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(BASE_DIR, "properties.db")

st.set_page_config(page_title="Morningside Rate Hunter", page_icon="🏡", layout="wide")

init_db()

st.title("🏡 Morningside Property24 Market Dashboard")
st.caption("Density Outlier Engine & Top 2% Value Tracker")

conn = sqlite3.connect(DB_NAME)

# Action button
if st.button("🚀 Run Scraper & Market Engine", type="primary"):
    with st.spinner("Scraping Morningside and processing market metrics..."):
        run_scraper()
    st.success("Analysis complete!")
    st.rerun()

st.divider()

# Check DB contents
try:
    stats_df = pd.read_sql_query("SELECT * FROM area_stats WHERE suburb='Morningside'", conn)
except Exception:
    stats_df = pd.DataFrame()

# AUTO-SCRAPE: If database is completely empty on initial page load, trigger initial fetch
if stats_df.empty:
    with st.spinner("⚡ Database is empty. Running initial Morningside scrape..."):
        run_scraper()
        try:
            stats_df = pd.read_sql_query("SELECT * FROM area_stats WHERE suburb='Morningside'", conn)
        except Exception:
            stats_df = pd.DataFrame()

if not stats_df.empty:
    sub_stats = stats_df.iloc[0]

    st.subheader("📊 Market Summary: Morningside")
    col1, col2, col3, col4, col5 = st.columns(5)
    
    col1.metric("Total Listings Scraped", f"{int(sub_stats['total_raw'])}")
    col2.metric("Total Valid Listings", f"{int(sub_stats['total_clean'])}")
    col3.metric("Minimum Valid Price / m²", f"R {sub_stats['real_min']:,.2f}")
    col4.metric("Median Price / m²", f"R {sub_stats['median_rate']:,.2f}")
    col5.metric("Top 2% Bargain Ceiling", f"R {sub_stats['top_2_percentile']:,.2f}")

    st.divider()

    try:
        raw_df = pd.read_sql_query("SELECT * FROM raw_listings WHERE suburb='Morningside'", conn)
    except Exception:
        raw_df = pd.DataFrame()

    if not raw_df.empty:
        clean_df = raw_df[(raw_df['rate_sqm'] >= sub_stats['real_min']) & (raw_df['rate_sqm'] <= sub_stats['real_max'])].sort_values(by="rate_sqm").reset_index(drop=True)
        
        total_clean_count = len(clean_df)
        clean_df['rank_num'] = clean_df.index + 1
        clean_df['true_percentile'] = (clean_df['rank_num'] / total_clean_count) * 100
        clean_df['pct_below_median'] = ((sub_stats['median_rate'] - clean_df['rate_sqm']) / sub_stats['median_rate']) * 100

        top_2_df = clean_df[clean_df['rate_sqm'] <= sub_stats['top_2_percentile']]

        st.subheader(f"🔥 Top 2% Lowest Valid Properties ({len(top_2_df)} Found)")
        
        if not top_2_df.empty:
            for _, row in top_2_df.iterrows():
                st.markdown(f"### 📍 [{row['title']}]({row['url']})")
                c1, c2, c3, c4, c5 = st.columns(5)
                c1.metric("Price", f"R {row['price']:,.0f}")
                c2.metric("Size", f"{row['sqm']:.0f} m²")
                c3.metric("Rate / m²", f"R {row['rate_sqm']:,.2f}")
                c4.metric("Value Rank", f"Top {row['true_percentile']:.1f}% (#{int(row['rank_num'])} of {total_clean_count})")
                c5.metric("% Below Median", f"{row['pct_below_median']:.1f}% OFF", delta=f"-{row['pct_below_median']:.1f}%")
                st.divider()
        else:
            st.info("No listings fell within the Top 2% threshold.")

        with st.expander(f"👁️ View All {total_clean_count} Valid Morningside Properties"):
            display_df = clean_df[['rank_num', 'true_percentile', 'pct_below_median', 'title', 'price', 'sqm', 'rate_sqm', 'url']].copy()
            display_df['pct_below_median'] = display_df['pct_below_median'].map(lambda x: f"{x:.1f}%")
            st.dataframe(display_df, use_container_width=True)

else:
    st.error("⚠️ Unable to load Morningside listings. Click the red button above to retry.")

conn.close()
