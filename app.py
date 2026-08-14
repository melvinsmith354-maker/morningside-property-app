import streamlit as st
import sqlite3
import pandas as pd
from scraper import init_db, run_scraper

st.set_page_config(page_title="P24 Rate Hunter", page_icon="🏡", layout="wide")

init_db()

st.title("🏡 Property24 Rate-per-m² Market Dashboard")
st.caption("2-Pass Density Isolation & Outlier Removal Engine")

conn = sqlite3.connect("properties.db")

# Run Scraper Button
if st.button("🚀 Run Scraper & Market Engine", type="primary"):
    with st.spinner("Scraping all pages and processing market density..."):
        run_scraper()
    st.success("Analysis complete!")
    st.rerun()

st.divider()

try:
    stats_df = pd.read_sql_query("SELECT * FROM area_stats WHERE area='Morningside'", conn)
except Exception:
    stats_df = pd.DataFrame()

if not stats_df.empty:
    stats = stats_df.iloc[0]
    
    st.subheader("📊 Market Density & Outlier Summary")
    col1, col2, col3, col4, col5 = st.columns(5)
    
    col1.metric("Raw Scraped Listings", f"{int(stats['total_raw'])}")
    col2.metric("Valid (Non-Junk) Listings", f"{int(stats['total_clean'])}")
    col3.metric("Real Market Minimum", f"R {stats['real_min']:,.2f} / m²")
    col4.metric("Real Market Maximum", f"R {stats['real_max']:,.2f} / m²")
    col5.metric("Top 5% Bargain Ceiling", f"R {stats['top_5_percentile']:,.2f} / m²")

    st.divider()

    try:
        raw_df = pd.read_sql_query("SELECT * FROM raw_listings WHERE area='Morningside'", conn)
    except Exception:
        raw_df = pd.DataFrame()

    if not raw_df.empty:
        # Filter out junk listings using real market min and max bounds
        clean_df = raw_df[(raw_df['rate_sqm'] >= stats['real_min']) & (raw_df['rate_sqm'] <= stats['real_max'])].sort_values(by="rate_sqm").reset_index(drop=True)
        
        total_clean_count = len(clean_df)
        
        # Calculate True Ordinal Percentile Rank (1 to 348)
        clean_df['rank_num'] = clean_df.index + 1
        clean_df['true_percentile'] = (clean_df['rank_num'] / total_clean_count) * 100

        # Filter properties that sit inside the true Top 5% threshold
        top_5_df = clean_df[clean_df['rate_sqm'] <= stats['top_5_percentile']]

        st.subheader(f"🔥 Top 5% Bargain Deals ({len(top_5_df)} Found)")
        
        if not top_5_df.empty:
            for _, row in top_5_df.iterrows():
                st.markdown(f"### 📍 [{row['title']}]({row['url']})")
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Price", f"R {row['price']:,.0f}")
                c2.metric("Size", f"{row['sqm']:.0f} m²")
                c3.metric("Rate / m²", f"R {row['rate_sqm']:,.2f}")
                c4.metric("Value Rank", f"Top {row['true_percentile']:.1f}% (#{int(row['rank_num'])} of {total_clean_count})")
                st.divider()
        else:
            st.info("No listings fell within the Top 5% threshold.")

        with st.expander("👁️ View All Valid Morningside Properties (Cleaned)"):
            st.dataframe(clean_df[['rank_num', 'true_percentile', 'title', 'price', 'sqm', 'rate_sqm', 'url']], use_container_width=True)

else:
    st.info("👋 Welcome! Click the **🚀 Run Scraper & Market Engine** button above to run the density analysis.")

conn.close()
