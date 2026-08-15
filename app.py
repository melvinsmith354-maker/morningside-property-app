import os
import sqlite3
import numpy as np
import pandas as pd
import streamlit as st
from scraper import init_db, run_scraper

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(BASE_DIR, "properties.db")

st.set_page_config(page_title="Morningside Apartments", page_icon="🏢", layout="wide")

st.title("🏢 Morningside Apartment Dashboard")
st.caption("Valid Property Filtration & Top 5% Value Tracker")

# 1. Run Engine Logic
if st.button("🚀 Fetch Latest Data (~ 1 minute)", type="primary"):
    with st.spinner("Scraping all pages and crunching data... please wait."):
        run_scraper(max_pages=50)
    st.success("Analysis complete!")
    st.rerun()

st.divider()

# 2. Extract Data Safely
if not os.path.exists(DB_NAME):
    st.info("Database is empty. Click the button above to start.")
    st.stop()

try:
    conn = sqlite3.connect(DB_NAME)
    stats_df = pd.read_sql_query("SELECT * FROM area_stats ORDER BY id DESC LIMIT 1", conn)
    raw_df = pd.read_sql_query("SELECT * FROM raw_listings", conn)
    conn.close()
except Exception:
    stats_df = pd.DataFrame()
    raw_df = pd.DataFrame()

if not stats_df.empty and not raw_df.empty:
    sub_stats = stats_df.iloc[0]

    st.subheader("📊 Primary Market Metrics")
    col1, col2, col3 = st.columns(3)
    col1.metric("Total Properties Scraped", f"{int(sub_stats['total_raw'])}")
    col2.metric("Valid Properties (Cleaned)", f"{int(sub_stats['total_clean'])}")
    col3.metric("Median Property Value (R/m²)", f"R {sub_stats['median_rate']:,.2f}")

    st.divider()

    # 3. Process Valid DataFrame
    clean_df = raw_df[(raw_df['rate_sqm'] >= sub_stats['real_min']) & (raw_df['rate_sqm'] <= sub_stats['real_max'])].copy()
    clean_df = clean_df.sort_values(by="rate_sqm").reset_index(drop=True)
    
    total_valid_count = int(sub_stats['total_clean'])
    
    # Mathematical computations for rendering
    clean_df['rank_num'] = clean_df.index + 1
    clean_df['true_percentile'] = (clean_df['rank_num'] / total_valid_count) * 100
    clean_df['pct_below_median'] = ((sub_stats['median_rate'] - clean_df['rate_sqm']) / sub_stats['median_rate']) * 100

    # Extract strictly the top 5% limit
    top_5_limit = int(np.ceil(total_valid_count * 0.05))
    top_5_df = clean_df.head(top_5_limit)

    st.subheader(f"🔥 Lowest 5% Valid Properties ({len(top_5_df)} Deals out of {total_valid_count})")
    
    if not top_5_df.empty:
        for _, row in top_5_df.iterrows():
            # Format UI elements for readability
            beds_txt = f"{int(row['bedrooms'])}" if not pd.isna(row['bedrooms']) else "N/A"
            baths_txt = f"{float(row['bathrooms'])}" if not pd.isna(row['bathrooms']) else "N/A"
            pct_txt = f"Top {row['true_percentile']:.2f}%"
            below_med_txt = f"{row['pct_below_median']:.1f}% below median"

            st.markdown(f"### 📍 [{row['title']}]({row['url']})")
            
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Price", f"R {row['price']:,.0f}")
            c2.metric("Size", f"{row['sqm']:.0f} m²")
            c3.metric("Bedrooms", beds_txt)
            c4.metric("Bathrooms", baths_txt)
            
            c1b, c2b, c3b, c4b = st.columns(4)
            c1b.metric("Rate / m²", f"R {row['rate_sqm']:,.2f}")
            c2b.metric("Percentile Rank", pct_txt)
            c3b.metric("Discount Tracker", below_med_txt)
            st.divider()
    else:
        st.info("No listings fell within the Top 5% threshold.")

else:
    st.info("Database loaded, but no valid property data was found. Try re-running the scraper.")
