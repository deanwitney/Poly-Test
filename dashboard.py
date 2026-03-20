import streamlit as st
import pandas as pd
import plotly.express as px
import requests
import time
import zoneinfo
import hashlib
import random
from datetime import datetime

# --- 1. MANDATORY CONFIG ---
st.set_page_config(page_title="BTC Master Strategy Lab", layout="wide")
ET_TIMEZONE = zoneinfo.ZoneInfo("America/New_York")

# --- 2. SECURITY LAYER ---
def check_password():
    CORRECT_HASH = "7123d367e354baefc7131376b2e3bbab1055dd45ba920b9f1ee2047cb1b72efc"
    if "password_correct" not in st.session_state:
        st.session_state["password_correct"] = False
    if st.session_state["password_correct"]: return True
    st.title("🛡️ BTC Strategy Lab Login")
    password_input = st.text_input("Enter Dashboard Password", type="password")
    if st.button("Unlock Dashboard"):
        if hashlib.sha256(password_input.strip().encode()).hexdigest() == CORRECT_HASH:
            st.session_state["password_correct"] = True
            st.rerun()
        else: st.error("😕 Password incorrect.")
    return False

if not check_password(): st.stop()

# --- 3. INITIALIZE SESSION STATE ---
for key, val in {
    'sb_init_bet': 10, 'sb_streak': 2, 'sb_max_l': 6, 
    'sb_strat': "Follow Streak", 'sb_dd_limit': 100,
    'stored_df': pd.DataFrame(), 'live_active': False, 
    'live_bankroll': 1000.0, 'live_history': [],
    'last_processed_time': None, 'live_pending_bet': None,
    'live_current_bet': 0.0, 'live_loss_count': 0
}.items():
    if key not in st.session_state: st.session_state[key] = val

# --- 4. THE ROBUST FETCHER ---
def fetch_binance_history(limit=500):
    mirrors = ["https://api.binance.com", "https://api1.binance.com", 
               "https://api2.binance.com", "https://api3.binance.com"]
    all_data, remaining, end_time = [], limit, None
    headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'}

    status_area = st.empty()
    
    while remaining > 0:
        f_limit = min(remaining, 1000)
        params = {"symbol": "BTCUSDT", "interval": "5m", "limit": f_limit}
        if end_time: params["endTime"] = end_time
        
        success = False
        random.shuffle(mirrors) # Try mirrors in random order to find an open one
        
        for base in mirrors:
            try:
                status_area.info(f"📡 Trying mirror: {base}...")
                res = requests.get(f"{base}/api/v3/klines", params=params, headers=headers, timeout=12)
                
                if res.status_code == 200:
                    data = res.json()
                    if data:
                        all_data = data + all_data
                        end_time = data[0][0] - 1
                        remaining -= len(data)
                        success = True
                        break
                elif res.status_code in [403, 429]:
                    status_area.warning(f"⚠️ Mirror {base} is throttled. Cooling down...")
                    time.sleep(2) # Small pause
            except: continue
        
        if not success:
            status_area.error("🛑 All Binance mirrors are currently blocking this Cloud IP. Reducing 'Fetch Count' may help.")
            break
            
    status_area.empty()
    if not all_data: return pd.DataFrame()

    df = pd.DataFrame(all_data, columns=['ot','o','h','l','c','v','ct','qv','nt','tbb','tbq','i'])
    df['Outcome'] = df.apply(lambda x: "Up" if float(x['c']) >= float(x['o']) else "Down", axis=1)
    df['Time'] = pd.to_datetime(df['ot'], unit='ms').dt.tz_localize('UTC').dt.tz_convert(ET_TIMEZONE)
    return df[['Time', 'Outcome', 'o', 'c']]

# --- 5. SIMULATION ENGINE ---
def run_simulation(dataset, s_bankroll, i_bet, s_trigger, m_loss, strat):
    if dataset.empty: return None, 0, 0, 0, 0, 0, 0, None, None
    bankroll, current_bet, history = s_bankroll, i_bet, []
    pending, w, l, ml, active_l = None, 0, 0, 0, 0
    peak_br, max_dd = s_bankroll, 0
    mdd_start, mdd_end = dataset.iloc[0]['Time'], dataset.iloc[0]['Time']
    peak_t, outcomes = mdd_start, []

    for _, row in dataset.iterrows():
        actual = row['Outcome']
        outcomes.append(actual)
        action, bet_dir = "Waiting", "None"
        if pending:
            action, bet_dir = "Betting", pending
            if bankroll < current_bet: return None, 0, 0, 0, 0, 0, 0, None, None
            bankroll -= current_bet
            if pending == actual:
                bankroll += (current_bet * 2); w += 1; active_l = 0; current_bet = i_bet
                last_n = outcomes[-int(s_trigger):]
                pending = actual if (len(set(last_n)) == 1 and strat == "Follow Streak") else None
            else:
                l += 1; active_l += 1; ml = max(ml, active_l)
                if strat == "Follow Streak": pending, current_bet, active_l = None, i_bet, 0
                else:
                    if active_l >= m_loss: pending, current_bet, active_l = None, i_bet, 0
                    else: current_bet *= 2
        if bankroll > peak_br: peak_br, peak_t = bankroll, row['Time']
        if (peak_br - bankroll) > max_dd:
            max_dd = peak_br - bankroll
            mdd_start, mdd_end = peak_t, row['Time']
        if pending is None:
            last_n = outcomes[-int(s_trigger):]
            if len(last_n) == s_trigger and len(set(last_n)) == 1:
                pending = last_n[-1] if strat == "Follow Streak" else ("Down" if last_n[-1] == "Up" else "Up")
        history.append({"Time": row['Time'], "Bankroll": round(bankroll, 2), "Action": action, "Bet On": bet_dir})
    return pd.DataFrame(history), w, l, ml, bankroll, bankroll, max_dd, mdd_start, mdd_end

# --- 6. UI & SIDEBAR ---
st.sidebar.title("🎮 Control Panel")
mode = st.sidebar.radio("Mode", ["Backtest & Optimize", "Live Mode"])
sb_bankroll = st.sidebar.number_input("Bankroll ($)", value=1000)
sb_init_bet = st.sidebar.number_input("Initial Bet ($)", value=st.session_state.sb_init_bet)
sb_streak = st.sidebar.number_input("Streak Trigger", value=st.session_state.sb_streak, min_value=1)
sb_max_l = st.sidebar.number_input("Max Doubles", value=st.session_state.sb_max_l, min_value=1)
sb_strat = st.sidebar.selectbox("Strategy", ["Follow Streak", "Anti-Streak (Bet Opp)"], index=0 if st.session_state.sb_strat == "Follow Streak" else 1)
st.session_state.sb_init_bet, st.session_state.sb_streak, st.session_state.sb_max_l, st.session_state.sb_strat = sb_init_bet, sb_streak, sb_max_l, sb_strat
st.sidebar.markdown("---")
dd_limit_pct = st.sidebar.number_input("Max DD Limit (%)", value=st.session_state.sb_dd_limit)
st.session_state.sb_dd_limit = dd_limit_pct

if mode == "Backtest & Optimize":
    st.title("📊 Backtest & Optimization")
    col_f, col_o = st.columns([1,1])
    with col_f:
        num_f = st.number_input("Fetch Count", 100, 5000, 500)
        if st.button("📡 Fetch Data", use_container_width=True):
            st.session_state.stored_df = fetch_binance_history(num_f)
    
    if not st.session_state.stored_df.empty:
        res_df, w, l, ms, final, m_bank, mdd, mdd_start, mdd_end = run_simulation(st.session_state.stored_df, sb_bankroll, sb_init_bet, sb_streak, sb_max_l, sb_strat)
        if res_df is not None:
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Final Balance", f"${final:,.2f}")
            m2.metric("Profit", f"${final-sb_bankroll:,.2f}")
            m3.metric("W/L", f"{w}/{l}")
            m4.metric("Max DD", f"${mdd:,.2f}")
            fig = px.area(res_df, x="Time", y="Bankroll")
            if mdd > 0: fig.add_vrect(x0=mdd_start, x1=mdd_end, fillcolor="red", opacity=0.2)
            fig.update_layout(template="plotly_dark")
            st.plotly_chart(fig, use_container_width=True)
    else: st.info("💡 Fetch data to start. If blocked, wait 5 mins or try a lower 'Fetch Count' like 200.")

else:
    st.title("⚡ Live Strategy Bot")
    if not st.session_state.live_active:
        if st.button("🚀 ACTIVATE LIVE BOT"):
            st.session_state.live_active, st.session_state.live_bankroll = True, sb_bankroll
            st.session_state.live_current_bet, st.session_state.live_pending_bet = sb_init_bet, None
            st.rerun()
    else:
        if st.button("🛑 DEACTIVATE"): st.session_state.live_active = False; st.rerun()
    
    if st.session_state.live_active:
        st.info("Live Monitoring... Syncing with Binance mirrors.")
        live_data = fetch_binance_history(20)
        if not live_data.empty:
            latest = live_data.iloc[-1]
            if st.session_state.last_processed_time != latest['Time']:
                actual = latest['Outcome']
                if st.session_state.live_pending_bet:
                    st.session_state.live_bankroll -= st.session_state.live_current_bet
                    if st.session_state.live_pending_bet == actual:
                        st.session_state.live_bankroll += (st.session_state.live_current_bet * 2)
                        st.session_state.live_current_bet = sb_init_bet
                        last_n = live_data['Outcome'].tail(int(sb_streak)).tolist()
                        st.session_state.live_pending_bet = actual if (len(set(last_n)) == 1 and sb_strat == "Follow Streak") else None
                    else:
                        if sb_strat == "Follow Streak": st.session_state.live_pending_bet, st.session_state.live_current_bet = None, sb_init_bet
                        else: st.session_state.live_current_bet *= 2
                if not st.session_state.live_pending_bet:
                    last_n = live_data['Outcome'].tail(int(sb_streak)).tolist()
                    if len(set(last_n)) == 1:
                        st.session_state.live_pending_bet = last_n[-1] if sb_strat == "Follow Streak" else ("Down" if last_n[-1] == "Up" else "Up")
                st.session_state.last_processed_time = latest['Time']
                st.session_state.live_history.append({"Time": latest['Time'], "Bankroll": st.session_state.live_bankroll})
        
        c1, c2 = st.columns(2)
        c1.metric("Bankroll", f"${st.session_state.live_bankroll:,.2f}")
        c2.metric("Next Bet", f"${st.session_state.live_current_bet:,.2f}" if st.session_state.live_pending_bet else "$0.00")
        if st.session_state.live_history: st.plotly_chart(px.line(pd.DataFrame(st.session_state.live_history), x="Time", y="Bankroll"))
        time.sleep(60); st.rerun()
