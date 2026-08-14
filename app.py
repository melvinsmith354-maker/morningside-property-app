import streamlit as st
import sqlite3
import pandas as pd
from scraper import init_db, run_scraper, MIN_SUBURB_VOLUME

st.set_page_config(page_title="Multi-Suburb Property Hunter", page_icon="🏡", layout="wide")

init_db()

st.title("🏡 Multi-Suburb Property24 Market Dashboard")
st.caption("2-Pass Density Isolation, IQR Median Cutoff & Top 2% Engine")

conn = sqlite3.connect("properties.db")

# Run Scraper Engine Button
if st.button("🚀 Run Scraper & Market Engine", type="primary"):
    with st.spinner("Scraping all pages across regions and analyzing density..."):
        run_scraper()
    st.success("Analysis complete!")
    st.rerun()

st.divider()

try:
    stats_df = pd.read_sql_query("SELECT * FROM area_stats WHERE total_clean >= 50 ORDER BY suburb ASC", conn)
except Exception:
    stats_df = pd.DataFrame()

if not stats_df.empty:
    suburbs = list(stats_df['suburb'].unique())
    
    st.sidebar.header("📍 Navigation")
    selected_suburb = st.sidebar.selectbox("Select Suburb (Volume >= 50):", suburbs)

    sub_stats = stats_df[stats_df['suburb'] == selected_suburb].iloc[0]

    st.subheader(f"📊 Market Summary: {selected_suburb}")
    col1, col2, col3, col4, col5 = st.columns(5)
    
    col1.metric("Valid (Non-Junk) Volume", f"{int(sub_stats['total_clean'])}")
    col2.metric("Real Market Minimum", f"R {sub_stats['real_min']:,.2f} / m²")
    col3.metric("Median Rate (Q2)", f"R {sub_stats['median_rate']:,.2f} / m²")
    col4.metric("IQR Cutoff (Median - 1xIQR)", f"R {sub_stats['iqr_cutoff']:,.2f} / m²")
    col5.metric("Top 2% Bargain Ceiling", f"R {sub_stats['top_2_percentile']:,.2f} / m²")

    st.divider()

    try:
        raw_df = pd.read_sql_query("SELECT * FROM raw_listings WHERE suburb=?", conn, params=(selected_suburb,))
    except Exception:
        raw_df = pd.DataFrame()

    if not raw_df.empty:
        # Filter valid listings starting from real market minimum
        clean_df = raw_df[(raw_df['rate_sqm'] >= sub_stats['real_min']) & (raw_df['rate_sqm'] <= sub_stats['real_max'])].sort_values(by="rate_sqm").reset_index(drop=True)
        
        total_clean_count = len(clean_df)
        
        # Calculate True Ordinal Percentile Rank
        clean_df['rank_num'] = clean_df.index + 1
        clean_df['true_percentile'] = (clean_df['rank_num'] / total_clean_count) * 100

        # Dual condition filter: Must be Top 2% AND below IQR Cutoff
        top_2_df = clean_df[(clean_df['rate_sqm'] <= sub_stats['top_2_percentile']) & (clean_df['rate_sqm'] <= sub_stats['iqr_cutoff'])]

        st.subheader(f"🔥 Top 2% Dual-Verified Bargains in {selected_suburb} ({len(top_2_df)} Found)")
        
        if not top_2_df.empty:
            for _, row in top_2_df.iterrows():
                st.markdown(f"### 📍 [{row['title']}]({row['url']})")
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Price", f"R {row['price']:,.0f}")
                c2.metric("Size", f"{row['sqm']:.0f} m²")
                c3.metric("Rate / m²", f"R {row['rate_sqm']:,.2f}")
                c4.metric("Value Rank", f"Top {row['true_percentile']:.1f}% (#{int(row['rank_num'])} of {total_clean_count})")
                st.divider()
        else:
            st.info(f"No listings in {selected_suburb} met both the Top 2% and IQR distance criteria.")

        with st.expander(f"👁️ View All Valid {selected_suburb} Properties (Cleaned)"):
            st.dataframe(clean_df[['rank_num', 'true_percentile', 'title', 'price', 'sqm', 'rate_sqm', 'url']], use_container_width=True)

else:
    st.info("👋 No suburbs with >= 50 valid properties found yet. Click **🚀 Run Scraper & Market Engine** to analyze the region.")

conn.close()
