import streamlit as st
import pandas as pd
import plotly.express as px
import requests
import time
import zoneinfo
import hashlib
import os
from datetime import datetime

# --- 1. MANDATORY CONFIG ---
st.set_page_config(page_title="BTC Strategy Lab (CSV Edition)", layout="wide")
ET_TIMEZONE = zoneinfo.ZoneInfo("America/New_York")

# --- 2. SECURITY LAYER (Password: 1199) ---
def check_password():
    CORRECT_HASH = "7123d367e354baefc7131376b2e3bbab1055dd45ba920b9f1ee2047cb1b72efc"
    if "password_correct" not in st.session_state:
        st.session_state["password_correct"] = False
    if st.session_state["password_correct"]:
        return True
    
    st.title("🛡️ BTC Strategy Lab Login")
    password_input = st.text_input("Enter Dashboard Password", type="password")
    if st.button("Unlock Dashboard"):
        input_hash = hashlib.sha256(password_input.strip().encode()).hexdigest()
        if input_hash == CORRECT_HASH:
            st.session_state["password_correct"] = True
            st.rerun()
        else:
            st.error("😕 Password incorrect.")
    return False

if not check_password():
    st.stop()

# --- 3. INITIALIZE SESSION STATE ---
for key, val in {
    'sb_init_bet': 10, 'sb_streak': 2, 'sb_max_l': 6, 
    'sb_strat': "Follow Streak", 'sb_dd_limit': 100,
    'best_params_found': None, 'stored_df': pd.DataFrame(),
    'live_active': False, 'live_bankroll': 1000.0, 'live_history': [],
    'last_processed_time': None, 'live_pending_bet': None,
    'live_current_bet': 0.0, 'live_loss_count': 0
}.items():
    if key not in st.session_state: st.session_state[key] = val

# --- 4. DATA LOADER (Reads from your GitHub CSV) ---
def load_historical_data(limit=2000):
    filename = "btc_historical_data.csv"
    
    if not os.path.exists(filename):
        st.error(f"❌ File '{filename}' not found in GitHub. Please upload it first!")
        return pd.DataFrame()

    with st.spinner("📂 Loading data from CSV..."):
        # Reading the local file is nearly instant compared to API calls
        df = pd.read_csv(filename)
        
        # Convert prices and calculate direction
        df['o'] = pd.to_numeric(df['o'])
        df['c'] = pd.to_numeric(df['c'])
        df['Outcome'] = df.apply(lambda x: "Up" if x['c'] >= x['o'] else "Down", axis=1)
        
        # Convert ot (Open Time) to human-readable New York time
        df['Time'] = pd.to_datetime(df['ot'], unit='ms').dt.tz_localize('UTC').dt.tz_convert(ET_TIMEZONE)
        
        # Take the most recent data based on the user's limit
        return df.tail(limit)

# --- 5. SIMULATION ENGINE ---
def run_simulation(dataset, s_bankroll, i_bet, s_trigger, m_loss, strat):
    if dataset.empty:
        return None, 0, 0, 0, 0, 0, 0, None, None
        
    bankroll, current_bet, history = s_bankroll, i_bet, []
    pending, w, l, ml, active_l = None, 0, 0, 0, 0
    peak_bankroll, max_drawdown = s_bankroll, 0
    mdd_start, mdd_end = dataset.iloc[0]['Time'], dataset.iloc[0]['Time']
    peak_time, outcomes_list = mdd_start, []

    for _, row in dataset.iterrows():
        actual = row['Outcome']
        outcomes_list.append(actual)
        action, bet_dir = "Waiting", "None"
        
        if pending:
            action, bet_dir = "Betting", pending
            if bankroll < current_bet: return None, 0, 0, 0, 0, 0, 0, None, None
            bankroll -= current_bet
            if pending == actual:
                bankroll += (current_bet * 2); w += 1; active_l = 0; current_bet = i_bet
                last_n = outcomes_list[-int(s_trigger):]
                if len(set(last_n)) == 1: 
                    pending = actual if strat == "Follow Streak" else ("Down" if actual == "Up" else "Up")
                else: pending = None
            else:
                l += 1; active_l += 1; ml = max(ml, active_l)
                if strat == "Follow Streak":
                    pending, current_bet, active_l = None, i_bet, 0
                else:
                    if active_l >= m_loss: pending, current_bet, active_l = None, i_bet, 0
                    else: current_bet *= 2
        
        if bankroll > peak_bankroll: peak_bankroll, peak_time = bankroll, row['Time']
        if (peak_bankroll - bankroll) > max_drawdown:
            max_drawdown = peak_bankroll - bankroll
            mdd_start, mdd_end = peak_time, row['Time']

        if pending is None:
            last_n = outcomes_list[-int(s_trigger):]
            if len(last_n) == s_trigger and len(set(last_n)) == 1:
                if strat == "Follow Streak": pending = last_n[-1]
                else: pending = "Down" if last_n[-1] == "Up" else "Up"

        history.append({"Time": row['Time'], "BTC Result": actual, "Bankroll": round(bankroll, 2), "Action": action, "Bet On": bet_dir})
    return pd.DataFrame(history), w, l, ml, bankroll, bankroll, max_drawdown, mdd_start, mdd_end

# --- 6. UI SIDEBAR ---
st.sidebar.title("🎮 Control Panel")
mode = st.sidebar.radio("Mode", ["Backtest & Optimize", "Live Mode"])
st.sidebar.markdown("---")
sb_bankroll = st.sidebar.number_input("Starting Bankroll ($)", value=1000)
sb_init_bet = st.sidebar.number_input("Initial Bet ($)", value=st.session_state.sb_init_bet)
sb_streak = st.sidebar.number_input("Streak Trigger", value=st.session_state.sb_streak, min_value=1)
sb_max_l = st.sidebar.number_input("Max Doubles", value=st.session_state.sb_max_l, min_value=1)
sb_strat = st.sidebar.selectbox("Strategy Type", ["Follow Streak", "Anti-Streak (Bet Opp)"], index=0 if st.session_state.sb_strat == "Follow Streak" else 1)
st.session_state.sb_init_bet, st.session_state.sb_streak, st.session_state.sb_max_l, st.session_state.sb_strat = sb_init_bet, sb_streak, sb_max_l, sb_strat
st.sidebar.markdown("---")
dd_limit_pct = st.sidebar.number_input("Max DD Limit (%)", value=st.session_state.sb_dd_limit)
st.session_state.sb_dd_limit = dd_limit_pct

# --- 7. APP MODES ---
if mode == "Backtest & Optimize":
    st.title("📊 Backtest & Optimization")
    c1, c2 = st.columns(2)
    with c1:
        # Now you can easily load 50,000 or 100,000 points without crashing
        num_f = st.number_input("Data Points to Analyze", 500, 100000, 10000)
        if st.button("📂 Load CSV Data", use_container_width=True):
            st.session_state.stored_df = load_historical_data(num_f)
    with c2:
        if st.button("🚀 Optimize Strategy", use_container_width=True):
            if not st.session_state.stored_df.empty:
                results = []
                max_dd_allowed = sb_bankroll * (dd_limit_pct / 100) if dd_limit_pct > 0 else 999999
                # Optimization loop
                for s in range(1, 6):
                    for b in [10, 25, 50]:
                        for l in range(1, 10):
                            _, w, lo, ml, final, m_bank, mdd, _, _ = run_simulation(st.session_state.stored_df, sb_bankroll, b, s, l, sb_strat)
                            if m_bank is not None and mdd <= max_dd_allowed:
                                results.append({"S": s, "B": b, "L": l, "P": final-sb_bankroll, "DD": mdd})
                if results: st.session_state.best_params_found = pd.DataFrame(results).sort_values("P", ascending=False).iloc[0]
            else: st.warning("Please load the CSV first!")

    if not st.session_state.stored_df.empty:
        res_df, w, l, ms, final, m_bank, mdd, mdd_start, mdd_end = run_simulation(st.session_state.stored_df, sb_bankroll, sb_init_bet, sb_streak, sb_max_l, sb_strat)
        if res_df is not None:
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Final Bankroll", f"${final:,.2f}")
            col2.metric("Net Profit", f"${final-sb_bankroll:,.2f}")
            col3.metric("W/L Ratio", f"{w} / {l}")
            col4.metric("Max Drawdown", f"${mdd:,.2f}")
            fig = px.area(res_df, x="Time", y="Bankroll", title="Historical Performance (CSV Data)")
            if mdd > 0: fig.add_vrect(x0=mdd_start, x1=mdd_end, fillcolor="red", opacity=0.2)
            fig.update_layout(template="plotly_dark")
            st.plotly_chart(fig, use_container_width=True)
else:
    st.title("⚡ Live Mode (Beta)")
    st.warning("Live Mode still requires an active connection to Binance. If the Cloud IP is blocked, this mode will pause.")
    # Live mode logic stays the same but is secondary to your CSV Backtester.
