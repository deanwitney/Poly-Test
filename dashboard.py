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
    if "password_correct" not in st.session_state: st.session_state["password_correct"] = False
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
    'sb_init_bet': 10.0, 'sb_streak': 4, 'sb_max_l': 2, 
    'sb_strat': "Anti-Streak (Bet Opp)", 'sb_bet_sizing': "Dynamic Recovery",
    'sb_kelly_prob': 53.0, 'sb_kelly_mult': 0.5,
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

# --- 5. SIMULATION ENGINE (Now with Kelly Math) ---
def run_simulation(dataset, s_bankroll, i_bet, s_trigger, m_loss, strat, share_price, fee_pct, sizing_strat, advance_x, kelly_prob=53.0, kelly_mult=0.5):
    if dataset is None or dataset.empty: return None, 0, 0, 0, 0, 0, 0, None, None

    bankroll, current_bet, history = s_bankroll, float(i_bet), []
    pending, resolve_in, w, l, ml, active_l = None, 0, 0, 0, 0, 0
    accumulated_loss = 0.0
    
    peak_bankroll, max_drawdown = s_bankroll, 0
    mdd_start, mdd_end = dataset.iloc[0]['Time'], dataset.iloc[0]['Time']
    peak_time, outcomes_list = mdd_start, []

    actual_mult = (100 / share_price) * (1 - (fee_pct / 100))
    b_odds = actual_mult - 1 # Net profit multiplier for Kelly

    for _, row in dataset.iterrows():
        actual = row['Outcome']
        outcomes_list.append(actual)
        action, bet_dir = "Waiting", "None"
        
        # 1. Process existing bet
        if pending:
            if resolve_in > 1:
                resolve_in -= 1
                action, bet_dir = f"In Flight (Wait {resolve_in})", pending
            else:
                action, bet_dir = "Resolving", pending
                if pending == actual:
                    bankroll += (current_bet * actual_mult)
                    w += 1; active_l = 0; accumulated_loss = 0.0
                    pending = None
                    
                    if sizing_strat != "Kelly Criterion":
                        current_bet = float(i_bet)
                else:
                    l += 1; active_l += 1; ml = max(ml, active_l)
                    accumulated_loss += current_bet
                    pending = None
                    
                    if strat == "Follow Streak" or active_l >= m_loss:
                        accumulated_loss, active_l = 0.0, 0
                        if sizing_strat != "Kelly Criterion": current_bet = float(i_bet)
                    else:
                        if sizing_strat == "Dynamic Recovery":
                            if actual_mult <= 1.01: current_bet *= 2
                            else: current_bet = (accumulated_loss + i_bet) / (actual_mult - 1)
                        elif sizing_strat == "Standard (x2)": 
                            current_bet *= 2
                        # If Kelly, we do nothing here. The next bet size is calculated dynamically on the trigger.

        # 2. Look for new triggers
        if pending is None:
            last_n = outcomes_list[-int(s_trigger):]
            if len(last_n) == s_trigger and len(set(last_n)) == 1:
                pending = last_n[-1] if strat == "Follow Streak" else ("Down" if last_n[-1] == "Up" else "Up")
                resolve_in = advance_x + 1
                
                # --- KELLY CRITERION DYNAMIC SIZING ---
                if sizing_strat == "Kelly Criterion":
                    p_win = kelly_prob / 100.0
                    q_loss = 1.0 - p_win
                    
                    if b_odds > 0:
                        kelly_f = p_win - (q_loss / b_odds)
                    else:
                        kelly_f = 0
                        
                    # Apply multiplier (Half Kelly, Quarter Kelly) and enforce a 0.5% minimum bet if edge is low
                    kelly_f = max(0.005, kelly_f * kelly_mult) 
                    
                    # Size bet based on CURRENT bankroll
                    current_bet = bankroll * kelly_f

                if bankroll < current_bet: 
                    history.append({"Time": row['Time'], "BTC Result": actual, "Bankroll": round(bankroll, 2), "Action": "💥 BUSTED", "Bet On": pending})
                    return pd.DataFrame(history), w, l, ml, bankroll, peak_bankroll, max_drawdown, mdd_start, mdd_end
                
                bankroll -= current_bet
                action, bet_dir = "Bet Placed", pending
        
        if bankroll > peak_bankroll: peak_bankroll, peak_time = bankroll, row['Time']
        if (peak_bankroll - bankroll) > max_drawdown:
            max_drawdown = peak_bankroll - bankroll
            mdd_start, mdd_end = peak_time, row['Time']

        history.append({"Time": row['Time'], "BTC Result": actual, "Bankroll": round(bankroll, 2), "Action": action, "Bet On": bet_dir})
        
    return pd.DataFrame(history), w, l, ml, bankroll, peak_bankroll, max_drawdown, mdd_start, mdd_end

# --- 6. UI SIDEBAR ---
st.sidebar.title("🎮 Control Panel")
mode = st.sidebar.radio("Mode", ["Backtest & Optimize", "Live Mode (Simulator)"])
st.sidebar.markdown("---")

sb_bankroll = st.sidebar.number_input("Starting Bankroll ($)", value=1000)

st.sidebar.markdown("### 💱 Market & Sizing")
sb_share_price = st.sidebar.number_input("Avg Share Price (¢)", value=int(st.session_state.sb_share_price), min_value=1, max_value=99)
sb_fee_pct = st.sidebar.number_input("Platform Fee (%)", value=float(st.session_state.sb_fee_pct), step=0.1, format="%.2f")

# NEW: Kelly added to dropdown
sb_bet_sizing = st.sidebar.selectbox("Bet Sizing Logic", ["Dynamic Recovery", "Standard (x2)", "Kelly Criterion"], index=["Dynamic Recovery", "Standard (x2)", "Kelly Criterion"].index(st.session_state.sb_bet_sizing))

# Hide Base Bet if Kelly is selected, show Kelly controls instead
if sb_bet_sizing == "Kelly Criterion":
    st.sidebar.markdown("📈 **Kelly Parameters**")
    sb_kelly_prob = st.sidebar.slider("Est. Win Rate (%)", min_value=50.0, max_value=80.0, value=float(st.session_state.sb_kelly_prob), step=0.5, help="Based on your backtest stats, what is the % chance of winning this bet? Kelly needs an edge to work.")
    sb_kelly_mult = st.sidebar.selectbox("Kelly Multiplier", options=[1.0, 0.5, 0.25], format_func=lambda x: "Full Kelly (Max Growth/High Risk)" if x==1.0 else ("Half Kelly (Balanced)" if x==0.5 else "Quarter Kelly (Safe)"), index=[1.0, 0.5, 0.25].index(st.session_state.sb_kelly_mult))
    sb_init_bet = 10.0 # Dummy value, overridden by Kelly math
else:
    sb_init_bet = st.sidebar.number_input("Initial Base Bet ($)", value=float(st.session_state.sb_init_bet))
    sb_kelly_prob, sb_kelly_mult = 53.0, 0.5

sb_advance_x = st.sidebar.number_input("Advance Bet (Periods)", value=int(st.session_state.sb_advance_x), min_value=0)

st.sidebar.markdown("### ⚙️ Constraints")
sb_streak = st.sidebar.number_input("Streak Trigger", value=int(st.session_state.sb_streak), min_value=1)
sb_max_l = st.sidebar.number_input("Max Sequence Steps", value=int(st.session_state.sb_max_l), min_value=1, help="Acts as a Circuit Breaker for Kelly. E.g., Stop betting if you lose 2 in a row.")
sb_strat = st.sidebar.selectbox("Strategy Type", ["Follow Streak", "Anti-Streak (Bet Opp)"], index=0 if st.session_state.sb_strat == "Follow Streak" else 1)

# Save states
st.session_state.sb_init_bet, st.session_state.sb_streak, st.session_state.sb_max_l, st.session_state.sb_strat = sb_init_bet, sb_streak, sb_max_l, sb_strat
st.session_state.sb_share_price, st.session_state.sb_fee_pct, st.session_state.sb_bet_sizing = sb_share_price, sb_fee_pct, sb_bet_sizing
st.session_state.sb_advance_x, st.session_state.sb_kelly_prob, st.session_state.sb_kelly_mult = sb_advance_x, sb_kelly_prob, sb_kelly_mult

dd_limit_pct = st.sidebar.number_input("Max DD Limit (%)", value=int(st.session_state.sb_dd_limit))
st.session_state.sb_dd_limit = dd_limit_pct
safety_floor_pct = st.sidebar.slider("Safety Floor (%)", 0, 100, 20)

# --- 7. APP MODES ---
if mode == "Backtest & Optimize":
    st.title("📊 CSV Backtest & Optimization")
    
    actual_mult = (100 / sb_share_price) * (1 - (sb_fee_pct / 100))
    
    if sb_bet_sizing == "Kelly Criterion":
        # Calculate exactly what Kelly is doing behind the scenes
        b = actual_mult - 1
        p = sb_kelly_prob / 100
        f = p - ((1 - p) / b) if b > 0 else 0
        current_frac = max(0.005, f * sb_kelly_mult)
        st.info(f"🧠 **Kelly Math Active:** With a {sb_kelly_prob}% win edge and a {actual_mult:.3f}x payout, full Kelly suggests betting **{f*100:.2f}%** of your bankroll. You selected a modifier, so the bot will bet exactly **{current_frac*100:.2f}%** of your current bankroll on every trigger.")
    else:
        st.info(f"⏱️ **Advance Set to {sb_advance_x}:** When a streak triggers, the bot will wait {sb_advance_x * 5} minutes before resolving the bet.")

    c1, c2 = st.columns(2)
    with c1:
        num_f = st.number_input("Data Points to Load", 500, 100000, 2000)
        if st.button("📂 Load CSV Data", use_container_width=True):
            st.session_state.stored_df = load_historical_data(num_f)
    
    with c2:
        if st.button("🚀 Optimize Strategy", use_container_width=True):
            if st.session_state.stored_df is not None and not st.session_state.stored_df.empty:
                
                st.markdown("### ⚙️ Optimizer Running...")
                progress_bar = st.progress(0)
                
                ui_container = st.container()
                with ui_container:
                    col_table, col_live = st.columns([2, 1])
                    table_placeholder = col_table.empty()
                    live_placeholder = col_live.empty()
                
                results = [] 
                max_dd_allowed = sb_bankroll * (dd_limit_pct / 100) if dd_limit_pct > 0 else 999999
                
                streaks_to_test = range(3, 6) # Narrowed for speed
                doubles_to_test = range(1, 4)
                
                # Optimizer behaves differently if Kelly is selected
                if sb_bet_sizing == "Kelly Criterion":
                    bets_to_test = [0.25, 0.5, 1.0] # Test Kelly Multipliers
                    bet_label = "Kelly Size"
                else:
                    bets_to_test = [sb_bankroll * 0.005, sb_bankroll * 0.010, sb_bankroll * 0.020]
                    bet_label = "Base Bet"
                
                total_runs = len(streaks_to_test) * len(bets_to_test) * len(doubles_to_test)
                current_run = 0

                for s in streaks_to_test:
                    for b in bets_to_test:
                        for l in doubles_to_test:
                            current_run += 1
                            
                            # Determine what variables to pass to the engine
                            if sb_bet_sizing == "Kelly Criterion":
                                opt_i_bet = 10 # Ignored by engine
                                opt_k_mult = b
                                display_b = f"{b}x Kelly"
                            else:
                                opt_i_bet = b
                                opt_k_mult = 0.5
                                display_b = f"${b:,.2f}"

                            with live_placeholder.container():
                                st.info(f"**Test {current_run}/{total_runs}**")
                                st.write(f"🔹 **Streak:** {s} | **{bet_label}:** {display_b} | **Max Seq:** {l}")

                            _, w, lo, ml, final, m_bank, mdd, _, _ = run_simulation(
                                st.session_state.stored_df, sb_bankroll, opt_i_bet, s, l, 
                                sb_strat, sb_share_price, sb_fee_pct, sb_bet_sizing, sb_advance_x, sb_kelly_prob, opt_k_mult
                            )
                            
                            profit = final - sb_bankroll if final is not None else -sb_bankroll
                            
                            if m_bank is not None and m_bank >= (sb_bankroll * (safety_floor_pct/100)) and mdd <= max_dd_allowed:
                                results.append({
                                    "Streak": s, bet_label: b, "Max Sequence": l, 
                                    "Profit": profit, "Max DD": mdd
                                })
                                
                                display_df = pd.DataFrame(results).sort_values("Profit", ascending=False)
                                
                                # Format table output based on Kelly vs Standard
                                if sb_bet_sizing != "Kelly Criterion":
                                    display_df[bet_label] = display_df[bet_label].apply(lambda x: f"${x:,.2f}")
                                
                                display_df["Profit"] = display_df["Profit"].apply(lambda x: f"${x:,.2f}")
                                display_df["Max DD"] = display_df["Max DD"].apply(lambda x: f"${x:,.2f}")
                                
                                table_placeholder.dataframe(display_df, use_container_width=True, hide_index=True)
                                
                                with live_placeholder.container():
                                    st.info(f"**Test {current_run}/{total_runs}**")
                                    st.write(f"🔹 **Streak:** {s} | **{bet_label}:** {display_b} | **Max Seq:** {l}")
                                    st.success(f"✅ Survived! Profit: ${profit:,.2f}")
                            else:
                                with live_placeholder.container():
                                    st.info(f"**Test {current_run}/{total_runs}**")
                                    st.write(f"🔹 **Streak:** {s} | **{bet_label}:** {display_b} | **Max Seq:** {l}")
                                    st.error("💥 Result: BUSTED")
                            
                            progress_bar.progress(current_run / total_runs)
                            time.sleep(0.01)

                if results:
                    res_df_raw = pd.DataFrame(results)
                    best_row_idx = res_df_raw['Profit'].idxmax()
                    best_row = res_df_raw.iloc[best_row_idx]
                    
                    st.session_state.best_params_found = {
                        "S": int(best_row["Streak"]), 
                        "B": float(best_row[bet_label]), 
                        "L": int(best_row["Max Sequence"]), 
                        "P": float(best_row["Profit"]), 
                        "DD": float(best_row["Max DD"])
                    }
                    st.success("🎉 Optimization Complete!")
                else: 
                    st.session_state.best_params_found = "None"
                    st.error("No strategies survived the constraints.")
            else: st.warning("⚠️ Please load CSV data first!")

    if st.session_state.best_params_found is not None:
        if not isinstance(st.session_state.best_params_found, str):
            best = st.session_state.best_params_found
            st.success(f"🏆 Best Found: Profit **${best['P']:,.2f}** | Max DD: **${best['DD']:,.2f}**")
            if st.button("✅ USE THESE PARAMETERS", use_container_width=True):
                if st.session_state.sb_bet_sizing == "Kelly Criterion":
                    st.session_state.sb_kelly_mult = best['B']
                else:
                    st.session_state.sb_init_bet = best['B']
                    
                st.session_state.sb_streak = best['S']
                st.session_state.sb_max_l = best['L']
                st.session_state.best_params_found = None
                st.rerun()

    if st.session_state.stored_df is not None and not st.session_state.stored_df.empty:
        res_df, w, l, ms, final, m_bank, mdd, mdd_start, mdd_end = run_simulation(
            st.session_state.stored_df, sb_bankroll, sb_init_bet, sb_streak, sb_max_l, 
            sb_strat, sb_share_price, sb_fee_pct, sb_bet_sizing, sb_advance_x, sb_kelly_prob, sb_kelly_mult
        )
        if res_df is not None and not res_df.empty:
            
            is_busted = "💥 BUSTED" in res_df.iloc[-1]['Action']
            if is_busted: st.error("💥 ACCOUNT BUSTED - Strategy ran out of funds.")
            
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Final Bankroll", f"${final:,.2f}")
            col2.metric("Net Profit", f"${final-sb_bankroll:,.2f}")
            col3.metric("Win/Loss", f"{w} / {l}")
            col4.metric("Max Drawdown", f"${mdd:,.2f}")
            
            fig = px.area(res_df, x="Time", y="Bankroll", title="Historical Performance")
            if mdd > 0: fig.add_vrect(x0=mdd_start, x1=mdd_end, fillcolor="red", opacity=0.2, annotation_text="Max Drawdown Area")
            fig.update_layout(template="plotly_dark")
            st.plotly_chart(fig, width='stretch')
            
            with st.expander("📄 View Raw Data Log"): st.dataframe(res_df.iloc[::-1], width='stretch')
            with st.expander("📊 View Streak Distribution Analysis"):
                s = st.session_state.stored_df['Outcome']
                streak_groups = (s != s.shift()).cumsum()
                streak_lengths = s.groupby(streak_groups).size()
                streak_counts = streak_lengths.value_counts().sort_index()
                streak_counts = streak_counts[streak_counts.index >= 3]
                
                dist_df = pd.DataFrame({
                    'Streak Length': streak_counts.index.astype(str) + " in a row",
                    'Occurrences': streak_counts.values
                })
                
                fig_bar = px.bar(
                    dist_df, x='Streak Length', y='Occurrences', 
                    title="Market Streak Frequency", text='Occurrences', 
                    color='Occurrences', color_continuous_scale='Reds'
                )
                fig_bar.update_layout(template="plotly_dark", showlegend=False)
                st.plotly_chart(fig_bar, use_container_width=True)

# --- LIVE MODE (Simulator) ---
else:
    st.title("⚡ Live Strategy Simulator")
    st.caption("Simulating live trading by stepping through CSV data every 5 seconds.")
    # (Live simulator logic stays exactly the same, but inherits the sizing formulas)
