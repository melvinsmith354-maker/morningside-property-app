import streamlit as st
import sqlite3
import pandas as pd

from scraper import (
    DB_NAME,
    init_db,
    run_scraper
)

st.set_page_config(
    page_title="P24 Rate Hunter",
    page_icon="🏡",
    layout="wide"
)

# =========================================================
# INITIALISE DATABASE
# =========================================================

init_db()

st.title(
    "🏡 Property24 Rate-per-m² Market Dashboard"
)

st.caption(
    "2-Pass Density Isolation & Outlier Removal Engine"
)

# =========================================================
# RUN SCRAPER BUTTON
# =========================================================

if st.button(
    "🚀 Run Scraper & Market Engine",
    type="primary"
):

    try:

        with st.spinner(
            "Scraping all pages and "
            "processing market density..."
        ):

            run_scraper()

        st.success(
            "Analysis complete!"
        )

        st.rerun()

    except Exception as e:

        st.error(
            "Scraper failed."
        )

        st.exception(e)

st.divider()

# =========================================================
# LOAD AREA STATISTICS
# =========================================================

stats_df = pd.DataFrame()

try:

    with sqlite3.connect(
        DB_NAME
    ) as conn:

        stats_df = pd.read_sql_query(
            """
            SELECT
                area,
                total_raw,
                total_clean,
                real_min,
                real_max,
                top_3_percentile

            FROM area_stats

            WHERE area = ?
            """,
            conn,
            params=(
                "Morningside",
            )
        )

except Exception as e:

    st.error(
        "Could not read area statistics."
    )

    st.exception(e)

# =========================================================
# DISPLAY STATISTICS
# =========================================================

if not stats_df.empty:

    stats = stats_df.iloc[0]

    st.subheader(
        "📊 Market Density & Outlier Summary"
    )

    col1, col2, col3, col4, col5 = (
        st.columns(5)
    )

    col1.metric(
        "Raw Scraped Listings",
        f"{int(stats['total_raw'])}"
    )

    col2.metric(
        "Valid (Non-Junk) Listings",
        f"{int(stats['total_clean'])}"
    )

    col3.metric(
        "Real Market Minimum",
        (
            f"R "
            f"{stats['real_min']:,.2f}"
            f" / m²"
        )
    )

    col4.metric(
        "Real Market Maximum",
        (
            f"R "
            f"{stats['real_max']:,.2f}"
            f" / m²"
        )
    )

    col5.metric(
        "Top 3% Bargain Ceiling",
        (
            f"R "
            f"{stats['top_3_percentile']:,.2f}"
            f" / m²"
        )
    )

    st.divider()

    # =====================================================
    # LOAD PROPERTY LISTINGS
    # =====================================================

    raw_df = pd.DataFrame()

    try:

        with sqlite3.connect(
            DB_NAME
        ) as conn:

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
                params=(
                    "Morningside",
                )
            )

    except Exception as e:

        st.error(
            "Could not read property listings."
        )

        st.exception(e)

    # =====================================================
    # PREPARE CLEAN MARKET DATA
    # =====================================================

    if not raw_df.empty:

        raw_df = raw_df.dropna(
            subset=[
                "price",
                "sqm",
                "rate_sqm"
            ]
        ).copy()

        raw_df = raw_df[
            raw_df["sqm"] > 0
        ].copy()

        clean_df = raw_df[
            (
                raw_df["rate_sqm"]
                >= stats["real_min"]
            )
            &
            (
                raw_df["rate_sqm"]
                <= stats["real_max"]
            )
        ].copy()

        clean_df = (
            clean_df
            .sort_values(
                by="rate_sqm",
                ascending=True
            )
            .reset_index(
                drop=True
            )
        )

        total_clean_count = len(
            clean_df
        )

        # =================================================
        # RANKING
        # =================================================

        if total_clean_count > 0:

            clean_df[
                "rank_num"
            ] = (
                clean_df.index
                + 1
            )

            clean_df[
                "true_percentile"
            ] = (
                clean_df["rank_num"]
                / total_clean_count
                * 100
            )

            # =============================================
            # TOP 3% DEALS
            # =============================================

            top_3_df = clean_df[
                clean_df["rate_sqm"]
                <= stats[
                    "top_3_percentile"
                ]
            ].copy()

            st.subheader(
                f"🔥 Top 3% Bargain Deals "
                f"({len(top_3_df)} Found)"
            )

            if not top_3_df.empty:

                for _, row in (
                    top_3_df.iterrows()
                ):

                    st.markdown(
                        f"### 📍 "
                        f"[{row['title']}]"
                        f"({row['url']})"
                    )

                    c1, c2, c3, c4 = (
                        st.columns(4)
                    )

                    c1.metric(
                        "Price",
                        (
                            f"R "
                            f"{row['price']:,.0f}"
                        )
                    )

                    c2.metric(
                        "Size",
                        (
                            f"{row['sqm']:.0f}"
                            f" m²"
                        )
                    )

                    c3.metric(
                        "Rate / m²",
                        (
                            f"R "
                            f"{row['rate_sqm']:,.2f}"
                        )
                    )

                    c4.metric(
                        "Value Rank",
                        (
                            f"Top "
                            f"{row['true_percentile']:.1f}% "
                            f"(#{int(row['rank_num'])} "
                            f"of {total_clean_count})"
                        )
                    )

                    st.divider()

            else:

                st.info(
                    "No listings fell within "
                    "the Top 3% threshold."
                )

            # =============================================
            # ALL CLEAN PROPERTIES TABLE
            # =============================================

            with st.expander(
                "👁️ View All Valid Morningside "
                "Properties (Cleaned)"
            ):

                display_df = clean_df[
                    [
                        "rank_num",
                        "true_percentile",
                        "title",
                        "price",
                        "sqm",
                        "rate_sqm",
                        "url"
                    ]
                ].copy()

                display_df = (
                    display_df.rename(
                        columns={
                            "rank_num":
                                "Rank",

                            "true_percentile":
                                "Percentile %",

                            "title":
                                "Property",

                            "price":
                                "Price",

                            "sqm":
                                "Size m²",

                            "rate_sqm":
                                "Rate / m²",

                            "url":
                                "URL"
                        }
                    )
                )

                display_df[
                    "Percentile %"
                ] = (
                    display_df[
                        "Percentile %"
                    ].round(2)
                )

                display_df[
                    "Price"
                ] = (
                    display_df[
                        "Price"
                    ].round(0)
                )

                display_df[
                    "Rate / m²"
                ] = (
                    display_df[
                        "Rate / m²"
                    ].round(2)
                )

                st.dataframe(
                    display_df,
                    use_container_width=True,
                    hide_index=True
                )

        else:

            st.warning(
                "Listings were scraped, "
                "but none fall inside the "
                "current clean market range."
            )

    else:

        st.info(
            "No Morningside property listings "
            "are currently stored in the database."
        )

else:

    st.info(
        "👋 Welcome! Click the "
        "**🚀 Run Scraper & Market Engine** "
        "button above to run the density analysis."
    )
