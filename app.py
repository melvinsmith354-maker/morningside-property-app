import streamlit as st
import sqlite3
import pandas as pd
from scraper import init_db, run_scraper, PRESET_SEARCHES

st.set_page_config(page_title="P24 Rate Hunter", page_icon="🏡")

# Initialize Database
init_db()

st.title("🏡 P24 Rate-per-m² Hunter")
st.caption("Automated Property24 Bargain Monitor")

conn = sqlite3.connect("properties.db")

# Sidebar showing your hardcoded config
st.sidebar.header("⚙️ Active Preset Rules")
st.sidebar.write("**Junk Floor:** R 7,500 / m²")
st.sidebar.divider()
st.sidebar.write("**Monitored Searches:**")
for item in PRESET_SEARCHES:
    st.sidebar.markdown(f"- **{item['name']}** (Max: R{item['max_rate']:,.0f}/m²)")

# --- MAIN TRIGGER BUTTON ---
if st.button("🚀 Run Scraper Now", type="primary"):
    with st.spinner("Scraping Property24 pages across all preset searches..."):
        run_scraper()
    st.success("Scrape complete!")
    st.rerun()

st.divider()

# --- RESULTS TABLE ---
st.subheader("🔥 Bargain Deals Found")
max_threshold = st.slider("Filter Display Max R / m²", min_value=7500, max_value=30000, value=15000, step=500)

df = pd.read_sql_query("SELECT title, price, sqm, rate_sqm, url FROM listings", conn)

if not df.empty:
    # Filter between junk threshold (R7,500) and slider maximum
    filtered_df = df[(df['rate_sqm'] >= 7500) & (df['rate_sqm'] <= max_threshold)].sort_values(by="rate_sqm")
    
    if not filtered_df.empty:
        for _, row in filtered_df.iterrows():
            st.markdown(f"### 📍 [{row['title']}]({row['url']})")
            col1, col2, col3 = st.columns(3)
            col1.metric("Price", f"R {row['price']:,.0f}")
            col2.metric("Size", f"{row['sqm']:.0f} m²")
            col3.metric("Rate / m²", f"R {row['rate_sqm']:,.2f}")
            st.divider()
    else:
        st.warning("No listings match your selected threshold.")
else:
    st.info("No data available yet. Click 'Run Scraper Now' above or wait for GitHub Actions.")

conn.close()
