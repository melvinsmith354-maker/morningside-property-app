import streamlit as st
import sqlite3
import pandas as pd
from scraper import init_db, run_scraper

st.set_page_config(page_title="P24 Rate Hunter", page_icon="🏡")

# Initialize Database
init_db()

st.title("🏡 P24 Rate-per-m² Hunter")
st.write("Calculate and filter Property24 listings by R/m²")

tab1, tab2 = st.tabs(["🔥 Active Deals", "⚙️ Add P24 Link"])

conn = sqlite3.connect("properties.db")

with tab2:
    st.subheader("Monitored Property24 Searches")
    st.info("1. Go to Property24 on your phone/browser.\n2. Filter by area, beds, price, etc.\n3. Paste the URL below.")
    
    with st.form("add_search_form"):
        search_name = st.text_input("Search Name", placeholder="Morningside 2 Bed Flats")
        p24_url = st.text_input("Property24 Link", placeholder="https://www.property24.com/apartments-for-sale/morningside/sandton/gauteng/4258")
        target_max = st.number_input("Target Max R / m²", value=10000, step=500)
        submit = st.form_submit_button("💾 Save Search Alert")
        
        if submit:
            if search_name and p24_url:
                c = conn.cursor()
                c.execute("INSERT INTO user_searches (search_name, p24_url, max_price_sqm) VALUES (?, ?, ?)",
                          (search_name, p24_url, target_max))
                conn.commit()
                st.success("Search saved! Triggering initial scrape...")
                run_scraper()
                st.rerun()
            else:
                st.error("Please fill in all fields.")

with tab1:
    max_threshold = st.slider("Max R / m² Threshold", min_value=5000, max_value=30000, value=10000, step=500)
    
    df = pd.read_sql_query("SELECT title, price, sqm, rate_sqm, url FROM listings", conn)
    
    if not df.empty:
        filtered_df = df[df['rate_sqm'] <= max_threshold].sort_values(by="rate_sqm")
        if not filtered_df.empty:
            for _, row in filtered_df.iterrows():
                st.markdown(f"### 📍 [{row['title']}]({row['url']})")
                col1, col2, col3 = st.columns(3)
                col1.metric("Price", f"R {row['price']:,.0f}")
                col2.metric("Size", f"{row['sqm']:.0f} m²")
                col3.metric("Rate / m²", f"R {row['rate_sqm']:,.2f}")
                st.divider()
        else:
            st.warning("No listings currently match your selected threshold.")
    else:
        st.warning("No listings currently match or no data scraped yet.")

conn.close()
