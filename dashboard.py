import streamlit as st
import pandas as pd
import plotly.express as px
import time
import zoneinfo
import hashlib
import os

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
            st.session_state["password_correct"] = True; st.rerun()
        else: st.error("😕 Password incorrect.")
    return False

if not check_password(): st.stop()

# --- 3. INITIALIZE SESSION STATE ---
for key, val in {
    'sb_init_bet': 10.0, 'sb_streak': 2, 'sb_max_l': 6, 
    'sb_strat': "Follow Streak", 'sb_bet_sizing': "Dynamic Recovery",
    'sb_dd_limit': 100, 'sb_share_price': 50, 'sb_fee_pct': 1.5, 'sb_advance_x': 1,
    'best_params_found': None, 'stored_df': pd.DataFrame(),
    'live_active': False, 'live_bankroll': 1000.0, 'live_history': [],
    'last_processed_time': None, 'live_pending_bet': None,
    'live_current_bet': 0.0, 'live_loss_count': 0, 'live_accum_loss': 0.0,
    'live_resolve_in': 0
}.items():
    if key not in st.session_state: st.session_state[key] = val

# --- 4. CSV DATA LOADER ---
def load_historical_data(limit=2000):
    filename = "btc_historical_data.csv"
    if not os.path.exists(filename):
        st.error(f"❌ '{filename}' not found. Please ensure it is in the same folder on GitHub.")
        return pd.DataFrame()
    with st.spinner(f"📂 Loading {limit} data points..."):
        try:
            df = pd.read_csv(filename)
            df['o'] = pd.to_numeric(df['o']); df['c'] = pd.to_numeric(df['c'])
            df['Outcome'] = df.apply(lambda x: "Up" if x['c'] >= x['o'] else "Down", axis=1)
            df['Time'] = pd.to_datetime(df['ot'], unit='ms').dt.tz_localize('UTC').dt.tz_convert(ET_TIMEZONE)
            return df.tail(limit).reset_index(drop=True)
        except Exception as e:
            st.error(f"⚠️ Error reading CSV: {e}"); return pd.DataFrame()

# --- 5. SIMULATION ENGINE (Now with Advance Betting) ---
def run_simulation(dataset, s_bankroll, i_bet, s_trigger, m_loss, strat, share_price, fee_pct, sizing_strat, advance_x):
    if dataset is None or dataset.empty: return None, 0, 0, 0, 0, 0, 0, None, None

    bankroll, current_bet, history = s_bankroll, float(i_bet), []
    pending, resolve_in, w, l, ml, active_l = None, 0, 0, 0, 0, 0
    accumulated_loss = 0.0
    
    peak_bankroll, max_drawdown = s_bankroll, 0
    mdd_start, mdd_end = dataset.iloc[0]['Time'], dataset.iloc[0]['Time']
    peak_time, outcomes_list = mdd_start, []

    actual_mult = (100 / share_price) * (1 - (fee_pct / 100))

    for _, row in dataset.iterrows():
        actual = row['Outcome']
        outcomes_list.append(actual)
        action, bet_dir = "Waiting", "None"
        
        # 1. Process an existing bet
        if pending:
            if resolve_in > 1:
                resolve_in -= 1
                action, bet_dir = f"In Flight (Wait {resolve_in})", pending
            else:
                # RESOLVE THE BET
                action, bet_dir = "Resolving", pending
                if pending == actual:
                    # WIN
                    bankroll += (current_bet * actual_mult)
                    w += 1; active_l = 0; current_bet = float(i_bet); accumulated_loss = 0.0
                    pending = None
                else:
                    # LOSS
                    l += 1; active_l += 1; ml = max(ml, active_l)
                    accumulated_loss += current_bet
                    pending = None
                    
                    if strat == "Follow Streak":
                        current_bet, active_l, accumulated_loss = float(i_bet), 0, 0.0
                    else:
                        if active_l >= m_loss:
                            current_bet, active_l, accumulated_loss = float(i_bet), 0, 0.0
                        else:
                            if sizing_strat == "Dynamic Recovery":
                                if actual_mult <= 1.01: current_bet *= 2
                                else: current_bet = (accumulated_loss + i_bet) / (actual_mult - 1)
                            else: current_bet *= 2

        # 2. Look for new triggers if we aren't currently waiting for a bet to resolve
        if pending is None:
            last_n = outcomes_list[-int(s_trigger):]
            if len(last_n) == s_trigger and len(set(last_n)) == 1:
                # PLACE NEW BET
                pending = last_n[-1] if strat == "Follow Streak" else ("Down" if last_n[-1] == "Up" else "Up")
                resolve_in = advance_x + 1
                
                # Deduct funds when bet is placed
                if bankroll < current_bet: return None, 0, 0, 0, 0, 0, 0, None, None
                bankroll -= current_bet
                action, bet_dir = "Bet Placed", pending
        
        # Track Drawdowns
        if bankroll > peak_bankroll: peak_bankroll, peak_time = bankroll, row['Time']
        if (peak_bankroll - bankroll) > max_drawdown:
            max_drawdown = peak_bankroll - bankroll
            mdd_start, mdd_end = peak_time, row['Time']

        history.append({"Time": row['Time'], "BTC Result": actual, "Bankroll": round(bankroll, 2), "Action": action, "Bet On": bet_dir})
        
    return pd.DataFrame(history), w, l, ml, bankroll, bankroll, max_drawdown, mdd_start, mdd_end

# --- 6. UI SIDEBAR ---
st.sidebar.title("🎮 Control Panel")
mode = st.sidebar.radio("Mode", ["Backtest & Optimize", "Live Mode (Simulator)"])
st.sidebar.markdown("---")

sb_bankroll = st.sidebar.number_input("Starting Bankroll ($)", value=1000)
sb_init_bet = st.sidebar.number_input("Initial Bet ($)", value=st.session_state.sb_init_bet)

st.sidebar.markdown("### 💱 Market & Sizing")
sb_share_price = st.sidebar.number_input("Avg Share Price (¢)", value=st.session_state.sb_share_price, min_value=1, max_value=99)
sb_fee_pct = st.sidebar.number_input("Platform Fee (%)", value=st.session_state.sb_fee_pct, step=0.1, format="%.2f")
sb_bet_sizing = st.sidebar.selectbox("Bet Sizing Logic", ["Dynamic Recovery", "Standard (x2)"], index=0 if st.session_state.sb_bet_sizing == "Dynamic Recovery" else 1)

# NEW: Advance Betting
sb_advance_x = st.sidebar.number_input("Advance Bet (Periods)", value=st.session_state.sb_advance_x, min_value=0, help="0 = bet resolves on the immediate next candle. 1 = skip one candle to secure odds, bet resolves on the 2nd candle.")

st.sidebar.markdown("### ⚙️ Constraints")
sb_streak = st.sidebar.number_input("Streak Trigger", value=st.session_state.sb_streak, min_value=1)
sb_max_l = st.sidebar.number_input("Max Doubles / Steps", value=st.session_state.sb_max_l, min_value=1)
sb_strat = st.sidebar.selectbox("Strategy Type", ["Follow Streak", "Anti-Streak (Bet Opp)"], index=0 if st.session_state.sb_strat == "Follow Streak" else 1)

st.session_state.sb_init_bet, st.session_state.sb_streak, st.session_state.sb_max_l, st.session_state.sb_strat = sb_init_bet, sb_streak, sb_max_l, sb_strat
st.session_state.sb_share_price, st.session_state.sb_fee_pct, st.session_state.sb_bet_sizing = sb_share_price, sb_fee_pct, sb_bet_sizing
st.session_state.sb_advance_x = sb_advance_x

dd_limit_pct = st.sidebar.number_input("Max DD Limit (%)", value=st.session_state.sb_dd_limit)
st.session_state.sb_dd_limit = dd_limit_pct
safety_floor_pct = st.sidebar.slider("Safety Floor (%)", 0, 100, 20)

# --- 7. APP MODES ---
if mode == "Backtest & Optimize":
    st.title("📊 CSV Backtest & Optimization")
    
    actual_mult = (100 / sb_share_price) * (1 - (sb_fee_pct / 100))
    st.info(f"⏱️ **Advance Set to {sb_advance_x}:** When a streak triggers, the bot will wait {sb_advance_x * 5} minutes before resolving the bet, giving you time to buy shares at 50/50 odds.")

    c1, c2 = st.columns(2)
    with c1:
        num_f = st.number_input("Data Points to Load", 500, 100000, 2000)
        if st.button("📂 Load CSV Data", use_container_width=True):
            st.session_state.stored_df = load_historical_data(num_f)
    with c2:
        if st.button("🚀 Optimize Strategy", use_container_width=True):
            if st.session_state.stored_df is not None and not st.session_state.stored_df.empty:
                results = []
                max_dd_allowed = sb_bankroll * (dd_limit_pct / 100) if dd_limit_pct > 0 else 999999
                for s in range(1, 7):
                    for b in [10, 25, 50, 100]:
                        for l in range(1, 10):
                            _, w, lo, ml, final, m_bank, mdd, _, _ = run_simulation(st.session_state.stored_df, sb_bankroll, b, s, l, sb_strat, sb_share_price, sb_fee_pct, sb_bet_sizing, sb_advance_x)
                            if m_bank is not None and m_bank >= (sb_bankroll * (safety_floor_pct/100)) and mdd <= max_dd_allowed:
                                results.append({"S": s, "B": b, "L": l, "P": final-sb_bankroll, "DD": mdd})
                if results: st.session_state.best_params_found = pd.DataFrame(results).sort_values("P", ascending=False).iloc[0]
                else: st.session_state.best_params_found = "None"
            else: st.warning("⚠️ Please load CSV data first!")

    if st.session_state.best_params_found is not None:
        if not isinstance(st.session_state.best_params_found, str):
            best = st.session_state.best_params_found
            st.success(f"🏆 Best: Profit **${best['P']:,.2f}** | Max DD: **${best['DD']:,.2f}**")
            if st.button("✅ USE THESE PARAMETERS", use_container_width=True):
                st.session_state.sb_init_bet, st.session_state.sb_streak, st.session_state.sb_max_l = int(best['B']), int(best['S']), int(best['L'])
                st.session_state.best_params_found = None; st.rerun()

    if st.session_state.stored_df is not None and not st.session_state.stored_df.empty:
        res_df, w, l, ms, final, m_bank, mdd, mdd_start, mdd_end = run_simulation(st.session_state.stored_df, sb_bankroll, sb_init_bet, sb_streak, sb_max_l, sb_strat, sb_share_price, sb_fee_pct, sb_bet_sizing, sb_advance_x)
        if res_df is not None:
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Final Bankroll", f"${final:,.2f}")
            col2.metric("Net Profit", f"${final-sb_bankroll:,.2f}")
            col3.metric("Win/Loss", f"{w} / {l}")
            col4.metric("Max Drawdown", f"${mdd:,.2f}")
            fig = px.area(res_df, x="Time", y="Bankroll", title=f"Historical Performance")
            if mdd > 0: fig.add_vrect(x0=mdd_start, x1=mdd_end, fillcolor="red", opacity=0.2, annotation_text="Max Drawdown Area")
            fig.update_layout(template="plotly_dark")
            st.plotly_chart(fig, width='stretch')
            with st.expander("📄 View Raw Data Log"): st.dataframe(res_df.iloc[::-1], width='stretch')
        else: st.error("💥 ACCOUNT BUSTED")
    else: st.info("💡 Click **'Load CSV Data'** to begin analysis.")

# --- LIVE MODE (Simulator) ---
else:
    st.title("⚡ Live Strategy Simulator")
    st.caption("Simulating live trading by stepping through CSV data every 5 seconds.")
    
    if 'sim_index' not in st.session_state: st.session_state.sim_index = 0
    
    if not st.session_state.live_active:
        if st.button("🚀 ACTIVATE LIVE SIMULATOR"):
            st.session_state.live_active, st.session_state.live_bankroll = True, sb_bankroll
            st.session_state.live_current_bet, st.session_state.live_pending_bet = sb_init_bet, None
            st.session_state.live_loss_count, st.session_state.live_accum_loss = 0, 0.0
            st.session_state.live_resolve_in = 0
            st.session_state.sim_index = 100; st.rerun()
    else:
        if st.button("🛑 DEACTIVATE"): st.session_state.live_active = False; st.rerun()

    if st.session_state.live_active:
        st.info("Simulation running... Advancing one candle every 5 seconds.")
        full_data = load_historical_data(100000)
        
        if full_data.empty: st.error("Cannot run simulation. CSV not loaded.")
        elif st.session_state.sim_index >= len(full_data):
            st.warning("End of CSV data reached."); st.session_state.live_active = False
        else:
            live_data = full_data.iloc[st.session_state.sim_index-20 : st.session_state.sim_index]
            latest = live_data.iloc[-1]
            actual_mult = (100 / sb_share_price) * (1 - (sb_fee_pct / 100))
            
            if st.session_state.last_processed_time != latest['Time']:
                actual = latest['Outcome']
                action = "Waiting"
                
                # 1. Process existing bet
                if st.session_state.live_pending_bet:
                    if st.session_state.live_resolve_in > 1:
                        st.session_state.live_resolve_in -= 1
                        action = f"In Flight (Wait {st.session_state.live_resolve_in})"
                    else:
                        action = "Resolving"
                        if st.session_state.live_pending_bet == actual:
                            st.session_state.live_bankroll += (st.session_state.live_current_bet * actual_mult)
                            st.session_state.live_current_bet, st.session_state.live_loss_count = sb_init_bet, 0
                            st.session_state.live_accum_loss = 0.0
                            st.session_state.live_pending_bet = None
                        else:
                            st.session_state.live_loss_count += 1
                            st.session_state.live_accum_loss += st.session_state.live_current_bet
                            st.session_state.live_pending_bet = None
                            
                            if sb_strat == "Follow Streak":
                                st.session_state.live_current_bet = sb_init_bet
                                st.session_state.live_accum_loss = 0.0
                            else: 
                                if st.session_state.live_loss_count >= sb_max_l:
                                    st.session_state.live_current_bet = sb_init_bet
                                    st.session_state.live_accum_loss = 0.0
                                else:
                                    if sb_bet_sizing == "Dynamic Recovery":
                                        if actual_mult <= 1.01: st.session_state.live_current_bet *= 2
                                        else: st.session_state.live_current_bet = (st.session_state.live_accum_loss + sb_init_bet) / (actual_mult - 1)
                                    else: st.session_state.live_current_bet *= 2
                
                # 2. Look for new triggers
                if not st.session_state.live_pending_bet:
                    last_n = live_data['Outcome'].tail(int(sb_streak)).tolist()
                    if len(set(last_n)) == 1:
                        st.session_state.live_pending_bet = last_n[-1] if sb_strat == "Follow Streak" else ("Down" if last_n[-1] == "Up" else "Up")
                        st.session_state.live_resolve_in = sb_advance_x + 1
                        st.session_state.live_bankroll -= st.session_state.live_current_bet
                        action = "Bet Placed"

                st.session_state.last_processed_time = latest['Time']
                st.session_state.live_history.append({"Time": latest['Time'], "Bankroll": st.session_state.live_bankroll, "Result": actual, "Action": action, "Bet On": st.session_state.live_pending_bet})

            c1, c2, c3 = st.columns(3)
            c1.metric("Live Bankroll", f"${st.session_state.live_bankroll:,.2f}")
            c2.metric("Next Bet Size", f"${st.session_state.live_current_bet:,.2f}" if st.session_state.live_pending_bet else "$0.00")
            c3.metric("Action", f"{st.session_state.live_history[-1]['Action']} {st.session_state.live_pending_bet}" if st.session_state.live_pending_bet else "Waiting...")
            
            if st.session_state.live_history:
                st.plotly_chart(px.line(pd.DataFrame(st.session_state.live_history), x="Time", y="Bankroll", title="Live Session Performance"))
                with st.expander("📄 View Live Session Logs"): st.dataframe(pd.DataFrame(st.session_state.live_history).iloc[::-1], width='stretch')
            
            st.session_state.sim_index += 1; time.sleep(5); st.rerun()
