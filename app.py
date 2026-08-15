import os
import sqlite3
import numpy as np
import pandas as pd
import streamlit as st
from scraper import init_db, run_scraper

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(BASE_DIR, "properties.db")

st.set_page_config(page_title="Apartment Market Dashboard", page_icon="🏢", layout="wide")

init_db()

st.title("🏢 Multi-Suburb Apartment Dashboard")
st.caption("Valid Property Filtration & Top 5% Value Tracker")

# Since GitHub Cron handles the heavy lifting, this button is just for emergencies
if st.button("🚀 Fetch Latest Data (Warning: Takes 15+ minutes for all suburbs)", type="primary"):
    with st.spinner("Scraping all pages for all 16 suburbs... please wait."):
        run_scraper(max_pages=50)
    st.success("Analysis complete!")
    st.rerun()

st.divider()

if not os.path.exists(DB_NAME):
    st.info("Database is empty. Please wait for your GitHub Cron to run, or click the button above.")
    st.stop()

try:
    conn = sqlite3.connect(DB_NAME)
    stats_df = pd.read_sql_query("SELECT * FROM area_stats ORDER BY suburb ASC", conn)
    conn.close()
except Exception:
    stats_df = pd.DataFrame()

if not stats_df.empty:
    
    # --- SIDEBAR NAVIGATION ---
    suburbs = list(stats_df['suburb'].unique())
    st.sidebar.header("📍 Suburb Navigator")
    selected_suburb = st.sidebar.selectbox("Select Suburb:", suburbs)
    
    st.sidebar.divider()
    st.sidebar.info("💡 Data is auto-updated via GitHub Actions in the background.")

    # --- MAIN DASHBOARD (Filtered by Suburb) ---
    sub_stats = stats_df[stats_df['suburb'] == selected_suburb].iloc[0]

    st.subheader(f"📊 Primary Market Metrics: {selected_suburb}")
    col1, col2, col3 = st.columns(3)
    col1.metric("Total Properties Scraped", f"{int(sub_stats['total_raw'])}")
    col2.metric("Valid Properties (Cleaned)", f"{int(sub_stats['total_clean'])}")
    col3.metric("Median Property Value (R/m²)", f"R {sub_stats['median_rate']:,.2f}")

    st.divider()

    # Load raw data safely
    try:
        conn = sqlite3.connect(DB_NAME)
        raw_df = pd.read_sql_query("SELECT * FROM raw_listings WHERE suburb=?", conn, params=(selected_suburb,))
        conn.close()
    except Exception:
        raw_df = pd.DataFrame()

    if not raw_df.empty:
        clean_df = raw_df[(raw_df['rate_sqm'] >= sub_stats['real_min']) & (raw_df['rate_sqm'] <= sub_stats['real_max'])].copy()
        clean_df = clean_df.sort_values(by="rate_sqm").reset_index(drop=True)
        
        # Render Histogram
        st.subheader(f"📈 Valid Market Distribution for {selected_suburb} (R/m²)")
        hist_values, bin_edges = np.histogram(clean_df['rate_sqm'], bins=20)
        bin_labels = [f"R {int(bin_edges[i]):,} - R {int(bin_edges[i+1]):,}" for i in range(len(bin_edges)-1)]
        hist_df = pd.DataFrame({'Properties': hist_values}, index=bin_labels)
        st.bar_chart(hist_df)
        
        st.divider()

        total_valid_count = int(sub_stats['total_clean'])
        
        clean_df['rank_num'] = clean_df.index + 1
        clean_df['true_percentile'] = (clean_df['rank_num'] / total_valid_count) * 100
        clean_df['pct_below_median'] = ((sub_stats['median_rate'] - clean_df['rate_sqm']) / sub_stats['median_rate']) * 100

        top_5_limit = int(np.ceil(total_valid_count * 0.05))
        top_5_df = clean_df.head(top_5_limit)

        st.subheader(f"🔥 Lowest 5% Valid Properties ({len(top_5_df)} Deals out of {total_valid_count})")
        
        if not top_5_df.empty:
            for _, row in top_5_df.iterrows():
                
                # Clean formatting for beds and baths
                beds = row['bedrooms']
                baths = row['bathrooms']
                beds_txt = f"{int(beds)}" if not pd.isna(beds) and float(beds).is_integer() else f"{beds}" if not pd.isna(beds) else "N/A"
                baths_txt = f"{int(baths)}" if not pd.isna(baths) and float(baths).is_integer() else f"{baths}" if not pd.isna(baths) else "N/A"
                
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
            st.info(f"No listings fell within the Top 5% threshold in {selected_suburb}.")

else:
    st.info("Database loaded, but no valid property data was found. Try re-running the scraper.")
