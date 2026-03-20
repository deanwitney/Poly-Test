import streamlit as st
import pandas as pd
import plotly.express as px
import time
import zoneinfo
import hashlib
import os

# --- 1. MANDATORY CONFIG ---
st.set_page_config(page_title="BTC Strategy Terminal", layout="wide", initial_sidebar_state="expanded")
ET_TIMEZONE = zoneinfo.ZoneInfo("America/New_York")

# --- 2. SECURITY LAYER ---
def check_password():
    CORRECT_HASH = "7123d367e354baefc7131376b2e3bbab1055dd45ba920b9f1ee2047cb1b72efc"
    if "password_correct" not in st.session_state: st.session_state["password_correct"] = False
    if st.session_state["password_correct"]: return True
    
    st.markdown("## `> SECURE_LOGIN_REQUIRED`")
    password_input = st.text_input("Enter Access Key", type="password")
    if st.button("> AUTHENTICATE"):
        if hashlib.sha256(password_input.strip().encode()).hexdigest() == CORRECT_HASH:
            st.session_state["password_correct"] = True; st.rerun()
        else: st.error("[ERROR] INVALID_KEY_PROVIDED")
    return False

if not check_password(): st.stop()

# --- 3. INITIALIZE SESSION STATE ---
for key, val in {
    'sb_bankroll': 1000.0, 'sb_init_bet': 10.0, 'sb_scale_bet': False,
    'sb_streak': 4, 'sb_max_l': 2, 'sb_strat': "Anti-Streak (Bet Opp)", 
    'sb_bet_sizing': "Dynamic Recovery", 'sb_dd_limit': 100, 
    'sb_share_price': 50, 'sb_fee_pct': 1.5, 'sb_advance_x': 1,
    'best_params_found': None, 'stored_df': pd.DataFrame(),
    'live_active': False, 'live_bankroll': 1000.0, 'live_history': [],
    'last_processed_time': None, 'live_pending_bet': None,
    'live_current_bet': 0.0, 'live_loss_count': 0, 'live_accum_loss': 0.0,
    'live_resolve_in': 0, 'live_base_bet_locked': 0.0
}.items():
    if key not in st.session_state: st.session_state[key] = val

# --- 4. CSV DATA LOADER ---
def load_historical_data(limit=2000):
    filename = "btc_historical_data.csv"
    if not os.path.exists(filename):
        st.error(f"[FATAL] FILE_NOT_FOUND: '{filename}'")
        return pd.DataFrame()
    with st.spinner(f"> EXTRACTING {limit} RECORDS..."):
        try:
            df = pd.read_csv(filename)
            df['o'] = pd.to_numeric(df['o']); df['c'] = pd.to_numeric(df['c'])
            df['Outcome'] = df.apply(lambda x: "Up" if x['c'] >= x['o'] else "Down", axis=1)
            df['Time'] = pd.to_datetime(df['ot'], unit='ms').dt.tz_localize('UTC').dt.tz_convert(ET_TIMEZONE)
            return df.tail(limit).reset_index(drop=True)
        except Exception as e:
            st.error(f"[FATAL] PARSE_ERROR: {e}"); return pd.DataFrame()

# --- 5. SIMULATION ENGINE ---
def run_simulation(dataset, s_bankroll, i_bet, scale_bet, s_trigger, m_loss, strat, share_price, fee_pct, sizing_strat, advance_x):
    if dataset is None or dataset.empty: return None, 0, 0, 0, 0, 0, 0, None, None

    def get_base_bet(current_br):
        return current_br * (i_bet / 100.0) if scale_bet else float(i_bet)

    bankroll = s_bankroll
    base_bet = get_base_bet(bankroll)
    current_bet = base_bet
    history = []
    
    pending, resolve_in, w, l, ml, active_l = None, 0, 0, 0, 0, 0
    accumulated_loss = 0.0
    
    peak_bankroll, max_drawdown = s_bankroll, 0
    mdd_start, mdd_end = dataset.iloc[0]['Time'], dataset.iloc[0]['Time']
    peak_time, outcomes_list = mdd_start, []

    actual_mult = (100 / share_price) * (1 - (fee_pct / 100))

    for _, row in dataset.iterrows():
        actual = row['Outcome']
        outcomes_list.append(actual)
        action, bet_dir = "STANDBY", "NONE"
        
        # 1. Process existing bet
        if pending:
            if resolve_in > 1:
                resolve_in -= 1
                action, bet_dir = f"IN_FLIGHT (T-{resolve_in})", pending
            else:
                action, bet_dir = "RESOLVING", pending
                if pending == actual:
                    # WIN
                    bankroll += (current_bet * actual_mult)
                    w += 1; active_l = 0; accumulated_loss = 0.0
                    pending = None
                    
                    base_bet = get_base_bet(bankroll)
                    current_bet = base_bet
                else:
                    # LOSS
                    l += 1; active_l += 1; ml = max(ml, active_l)
                    accumulated_loss += current_bet
                    pending = None
                    
                    if strat == "Follow Streak" or active_l >= m_loss:
                        accumulated_loss, active_l = 0.0, 0
                        base_bet = get_base_bet(bankroll)
                        current_bet = base_bet
                    else:
                        if sizing_strat == "Dynamic Recovery":
                            if actual_mult <= 1.01: current_bet *= 2
                            else: current_bet = (accumulated_loss + base_bet) / (actual_mult - 1)
                        else: 
                            current_bet *= 2

        # 2. Look for new triggers
        if pending is None:
            last_n = outcomes_list[-int(s_trigger):]
            if len(last_n) == s_trigger and len(set(last_n)) == 1:
                pending = last_n[-1] if strat == "Follow Streak" else ("Down" if last_n[-1] == "Up" else "Up")
                resolve_in = advance_x + 1
                
                if bankroll < current_bet: 
                    history.append({"Time": row['Time'], "BTC Result": actual, "Bankroll": round(bankroll, 2), "Action": "[BUSTED]", "Bet On": pending})
                    return pd.DataFrame(history), w, l, ml, bankroll, peak_bankroll, max_drawdown, mdd_start, mdd_end
                
                bankroll -= current_bet
                action, bet_dir = "ORDER_EXECUTED", pending
        
        if bankroll > peak_bankroll: peak_bankroll, peak_time = bankroll, row['Time']
        if (peak_bankroll - bankroll) > max_drawdown:
            max_drawdown = peak_bankroll - bankroll
            mdd_start, mdd_end = peak_time, row['Time']

        history.append({"Time": row['Time'], "BTC Result": actual, "Bankroll": round(bankroll, 2), "Action": action, "Bet On": bet_dir})
        
    return pd.DataFrame(history), w, l, ml, bankroll, peak_bankroll, max_drawdown, mdd_start, mdd_end

# --- 6. UI SIDEBAR (TERMINAL REORG) ---
st.sidebar.markdown("## `> ROOT_TERMINAL`")
mode = st.sidebar.radio("RUNTIME_ENVIRONMENT", ["`[01]` Optimizer Module", "`[02]` Live Simulator"])
st.sidebar.divider()

st.sidebar.markdown("### `> MOD_01: CAPITAL`")
sb_bankroll = st.sidebar.number_input("Starting Ledger ($)", value=float(st.session_state.sb_bankroll))
sb_scale_bet = st.sidebar.checkbox("Enable Compounding (Scale Base %)", value=st.session_state.sb_scale_bet)

if sb_scale_bet:
    current_val = float(st.session_state.sb_init_bet) if float(st.session_state.sb_init_bet) <= 100 else 1.0
    sb_init_bet = st.sidebar.number_input("Base Bet (% of Ledger)", value=current_val, step=0.1)
else:
    sb_init_bet = st.sidebar.number_input("Base Bet (Flat $)", value=float(st.session_state.sb_init_bet), step=1.0)
st.sidebar.divider()

st.sidebar.markdown("### `> MOD_02: ALGORITHM`")
sb_strat = st.sidebar.selectbox("Logic Core", ["Follow Streak", "Anti-Streak (Bet Opp)"], index=0 if st.session_state.sb_strat == "Follow Streak" else 1)
sb_streak = st.sidebar.number_input("Trigger (Consecutive Candles)", value=int(st.session_state.sb_streak), min_value=1)
sb_max_l = st.sidebar.number_input("Max Sequence Steps (Circuit Breaker)", value=int(st.session_state.sb_max_l), min_value=1)
sb_advance_x = st.sidebar.number_input("Execution Offset (Periods)", value=int(st.session_state.sb_advance_x), min_value=0)
st.sidebar.divider()

with st.sidebar.expander("`> ADVANCED: MARKET & RISK`", expanded=False):
    st.markdown("**Market Dynamics**")
    sb_share_price = st.number_input("Est. Share Price (Cents)", value=int(st.session_state.sb_share_price), min_value=1, max_value=99)
    sb_fee_pct = st.number_input("Exchange Fee (%)", value=float(st.session_state.sb_fee_pct), step=0.1, format="%.2f")
    sb_bet_sizing = st.selectbox("Recovery Architecture", ["Dynamic Recovery", "Standard (x2)"], index=0 if st.session_state.sb_bet_sizing == "Dynamic Recovery" else 1)
    
    st.markdown("**Optimizer Constraints**")
    dd_limit_pct = st.number_input("Max Drawdown Tolerance (%)", value=int(st.session_state.sb_dd_limit))
    safety_floor_pct = st.slider("Absolute Minimum Floor (%)", 0, 100, 20)

# Save states
st.session_state.sb_bankroll, st.session_state.sb_init_bet, st.session_state.sb_scale_bet = sb_bankroll, sb_init_bet, sb_scale_bet
st.session_state.sb_streak, st.session_state.sb_max_l, st.session_state.sb_strat = sb_streak, sb_max_l, sb_strat
st.session_state.sb_share_price, st.session_state.sb_fee_pct, st.session_state.sb_bet_sizing = sb_share_price, sb_fee_pct, sb_bet_sizing
st.session_state.sb_advance_x, st.session_state.sb_dd_limit = sb_advance_x, dd_limit_pct

# --- 7. APP MODES ---
if mode == "`[01]` Optimizer Module":
    st.markdown("## `> OPTIMIZATION_ENVIRONMENT`")
    
    actual_mult = (100 / sb_share_price) * (1 - (sb_fee_pct / 100))
    sys_status = f"MODE: {'COMPOUNDING' if sb_scale_bet else 'FIXED_BASE'} | MULT: {actual_mult:.3f}x | OFFSET: T+{sb_advance_x}"
    st.info(f"`[SYS_CONFIG] {sys_status}`")

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("#### `[I/O] DATA_LOADER`")
        num_f = st.number_input("Records to Fetch", 500, 100000, 2000, label_visibility="collapsed")
        if st.button("> EXECUTE_LOAD", use_container_width=True):
            st.session_state.stored_df = load_historical_data(num_f)
    
    with c2:
        st.markdown("#### `[CMD] BATCH_TESTER`")
        if st.button("> RUN_OPTIMIZER", use_container_width=True):
            if st.session_state.stored_df is not None and not st.session_state.stored_df.empty:
                
                st.markdown("### `> PROCESSING_THREADS...`")
                progress_bar = st.progress(0)
                
                ui_container = st.container()
                with ui_container:
                    col_table, col_live = st.columns([2, 1])
                    table_placeholder = col_table.empty()
                    live_placeholder = col_live.empty()
                
                results = [] 
                max_dd_allowed = sb_bankroll * (dd_limit_pct / 100) if dd_limit_pct > 0 else 999999
                
                streaks_to_test = range(3, 6) 
                doubles_to_test = range(1, 4)
                
                if sb_scale_bet:
                    bets_to_test = [0.25, 0.5, 1.0, 1.5, 2.0]
                    bet_label = "Base_Bet_(%)"
                else:
                    bets_to_test = [sb_bankroll * 0.0025, sb_bankroll * 0.005, sb_bankroll * 0.010, sb_bankroll * 0.015, sb_bankroll * 0.020]
                    bet_label = "Base_Bet_($)"
                
                total_runs = len(streaks_to_test) * len(bets_to_test) * len(doubles_to_test)
                current_run = 0

                for s in streaks_to_test:
                    for b in bets_to_test:
                        for l in doubles_to_test:
                            current_run += 1
                            display_b = f"{b}%" if sb_scale_bet else f"${b:,.2f}"

                            with live_placeholder.container():
                                st.code(f"THREAD: {current_run}/{total_runs}\nSTRK: {s} | BASE: {display_b}\nSEQS: {l}\nSTATUS: RUNNING...", language="text")

                            _, w, lo, ml, final, m_bank, mdd, _, _ = run_simulation(
                                st.session_state.stored_df, sb_bankroll, b, sb_scale_bet, s, l, 
                                sb_strat, sb_share_price, sb_fee_pct, sb_bet_sizing, sb_advance_x
                            )
                            
                            profit = final - sb_bankroll if final is not None else -sb_bankroll
                            
                            if m_bank is not None and m_bank >= (sb_bankroll * (safety_floor_pct/100)) and mdd <= max_dd_allowed:
                                results.append({
                                    "Streak": s, bet_label: float(b), "Max_Seq": l, 
                                    "Net_Profit": profit, "Max_DD": mdd
                                })
                                
                                display_df = pd.DataFrame(results).sort_values("Net_Profit", ascending=False)
                                
                                if not sb_scale_bet: display_df[bet_label] = display_df[bet_label].apply(lambda x: f"${x:,.2f}")
                                else: display_df[bet_label] = display_df[bet_label].apply(lambda x: f"{x:,.2f}%")
                                    
                                display_df["Net_Profit"] = display_df["Net_Profit"].apply(lambda x: f"${x:,.2f}")
                                display_df["Max_DD"] = display_df["Max_DD"].apply(lambda x: f"${x:,.2f}")
                                
                                table_placeholder.dataframe(display_df, use_container_width=True, hide_index=True)
                                
                                with live_placeholder.container():
                                    st.code(f"THREAD: {current_run}/{total_runs}\nSTRK: {s} | BASE: {display_b}\nSEQS: {l}\nSTATUS: [OK] SURVIVED", language="text")
                            else:
                                with live_placeholder.container():
                                    st.code(f"THREAD: {current_run}/{total_runs}\nSTRK: {s} | BASE: {display_b}\nSEQS: {l}\nSTATUS: [FAIL] BUSTED", language="text")
                            
                            progress_bar.progress(current_run / total_runs)
                            time.sleep(0.01)

                if results:
                    res_df_raw = pd.DataFrame(results)
                    best_row_idx = res_df_raw['Net_Profit'].idxmax()
                    best_row = res_df_raw.iloc[best_row_idx]
                    
                    st.session_state.best_params_found = {
                        "S": int(best_row["Streak"]), "B": float(best_row[bet_label]), 
                        "L": int(best_row["Max_Seq"]), "P": float(best_row["Net_Profit"]), 
                        "DD": float(best_row["Max_DD"])
                    }
                    st.success("[SYS] OPTIMIZATION_COMPLETE")
                else: 
                    st.session_state.best_params_found = "None"
                    st.error("[SYS] ZERO_CONFIGURATIONS_SURVIVED")
            else: st.warning("[WARN] DATA_NOT_LOADED. EXECUTE [DATA_LOADER] FIRST.")

    if st.session_state.best_params_found is not None:
        if not isinstance(st.session_state.best_params_found, str):
            best = st.session_state.best_params_found
            st.success(f"`[OPTIMAL_FOUND] PROFIT: ${best['P']:,.2f} | DRAWDOWN: ${best['DD']:,.2f}`")
            if st.button("> APPLY_OPTIMAL_PARAMETERS", use_container_width=True):
                st.session_state.sb_init_bet = best['B']
                st.session_state.sb_streak = best['S']
                st.session_state.sb_max_l = best['L']
                st.session_state.best_params_found = None
                st.rerun()

    if st.session_state.stored_df is not None and not st.session_state.stored_df.empty:
        st.divider()
        st.markdown("### `> HISTORICAL_TELEMETRY`")
        res_df, w, l, ms, final, m_bank, mdd, mdd_start, mdd_end = run_simulation(
            st.session_state.stored_df, sb_bankroll, sb_init_bet, sb_scale_bet, sb_streak, sb_max_l, 
            sb_strat, sb_share_price, sb_fee_pct, sb_bet_sizing, sb_advance_x
        )
        if res_df is not None and not res_df.empty:
            
            is_busted = "[BUSTED]" in res_df.iloc[-1]['Action']
            if is_busted: st.error("`[CRITICAL_FAILURE] BANKROLL_DEPLETED`")
            
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("SYS_LEDGER_FINAL", f"${final:,.2f}")
            col2.metric("NET_YIELD", f"${final-sb_bankroll:,.2f}")
            col3.metric("WIN_LOSS_RATIO", f"{w} / {l}")
            col4.metric("MAX_DRAWDOWN", f"${mdd:,.2f}")
            
            fig = px.area(res_df, x="Time", y="Bankroll")
            if mdd > 0: fig.add_vrect(x0=mdd_start, x1=mdd_end, fillcolor="red", opacity=0.2, annotation_text="MAX_DD")
            fig.update_layout(template="plotly_dark", margin=dict(l=0, r=0, t=30, b=0))
            st.plotly_chart(fig, width='stretch')
            
            with st.expander("`> VIEW_RAW_EVENT_LOGS`"): st.dataframe(res_df.iloc[::-1], width='stretch')
            with st.expander("`> VIEW_STREAK_DISTRIBUTION_ANALYSIS`"):
                s = st.session_state.stored_df['Outcome']
                streak_groups = (s != s.shift()).cumsum()
                streak_lengths = s.groupby(streak_groups).size()
                streak_counts = streak_lengths.value_counts().sort_index()
                streak_counts = streak_counts[streak_counts.index >= 3]
                
                dist_df = pd.DataFrame({
                    'Sequence_Length': streak_counts.index.astype(str),
                    'Occurrences': streak_counts.values
                })
                
                fig_bar = px.bar(
                    dist_df, x='Sequence_Length', y='Occurrences', 
                    text='Occurrences', color='Occurrences', color_continuous_scale='Reds'
                )
                fig_bar.update_layout(template="plotly_dark", showlegend=False, xaxis_title="Consecutive Candles", yaxis_title="Count")
                st.plotly_chart(fig_bar, use_container_width=True)

# --- LIVE MODE (Simulator) ---
else:
    st.markdown("## `> LIVE_SIMULATION_ENVIRONMENT`")
    
    def get_live_base_bet(current_br):
        return current_br * (sb_init_bet / 100.0) if sb_scale_bet else float(sb_init_bet)
    
    if 'sim_index' not in st.session_state: st.session_state.sim_index = 0
    
    c1, c2 = st.columns(2)
    if not st.session_state.live_active:
        with c1:
            if st.button("> INITIALIZE_RUNTIME", use_container_width=True):
                st.session_state.live_active, st.session_state.live_bankroll = True, sb_bankroll
                base_bet = get_live_base_bet(sb_bankroll)
                st.session_state.live_base_bet_locked = base_bet
                st.session_state.live_current_bet = base_bet
                st.session_state.live_pending_bet = None
                st.session_state.live_loss_count, st.session_state.live_accum_loss = 0, 0.0
                st.session_state.live_resolve_in = 0
                st.session_state.sim_index = 100; st.rerun()
    else:
        with c1:
            if st.button("> TERMINATE_RUNTIME", use_container_width=True): 
                st.session_state.live_active = False; st.rerun()

    st.divider()

    if st.session_state.live_active:
        st.info("`[SYS] RUNTIME_ACTIVE. Advancing tick sequence (Interval = 5s)...`")
        full_data = load_historical_data(100000)
        
        if full_data.empty: st.error("`[FATAL] I/O_ERROR: DATA_NOT_FOUND`")
        elif st.session_state.sim_index >= len(full_data):
            st.warning("`[SYS] EOF_REACHED. TERMINATING.`"); st.session_state.live_active = False
        else:
            live_data = full_data.iloc[st.session_state.sim_index-20 : st.session_state.sim_index]
            latest = live_data.iloc[-1]
            actual_mult = (100 / sb_share_price) * (1 - (sb_fee_pct / 100))
            
            if st.session_state.last_processed_time != latest['Time']:
                actual = latest['Outcome']
                action = "STANDBY"
                
                if st.session_state.live_pending_bet:
                    if st.session_state.live_resolve_in > 1:
                        st.session_state.live_resolve_in -= 1
                        action = f"IN_FLIGHT (T-{st.session_state.live_resolve_in})"
                    else:
                        action = "RESOLVING"
                        if st.session_state.live_pending_bet == actual:
                            st.session_state.live_bankroll += (st.session_state.live_current_bet * actual_mult)
                            new_base = get_live_base_bet(st.session_state.live_bankroll)
                            st.session_state.live_base_bet_locked, st.session_state.live_current_bet = new_base, new_base
                            st.session_state.live_loss_count, st.session_state.live_accum_loss = 0, 0.0
                            st.session_state.live_pending_bet = None
                        else:
                            st.session_state.live_loss_count += 1
                            st.session_state.live_accum_loss += st.session_state.live_current_bet
                            st.session_state.live_pending_bet = None
                            
                            if sb_strat == "Follow Streak" or st.session_state.live_loss_count >= sb_max_l:
                                new_base = get_live_base_bet(st.session_state.live_bankroll)
                                st.session_state.live_base_bet_locked, st.session_state.live_current_bet = new_base, new_base
                                st.session_state.live_accum_loss, st.session_state.live_loss_count = 0.0, 0
                            else:
                                locked_base = st.session_state.live_base_bet_locked
                                if sb_bet_sizing == "Dynamic Recovery":
                                    if actual_mult <= 1.01: st.session_state.live_current_bet *= 2
                                    else: st.session_state.live_current_bet = (st.session_state.live_accum_loss + locked_base) / (actual_mult - 1)
                                else: st.session_state.live_current_bet *= 2
                
                if not st.session_state.live_pending_bet:
                    last_n = live_data['Outcome'].tail(int(sb_streak)).tolist()
                    if len(set(last_n)) == 1:
                        st.session_state.live_pending_bet = last_n[-1] if sb_strat == "Follow Streak" else ("Down" if last_n[-1] == "Up" else "Up")
                        st.session_state.live_resolve_in = sb_advance_x + 1
                        
                        if st.session_state.live_bankroll < st.session_state.live_current_bet:
                            action = "[BUSTED]"
                            st.session_state.live_active = False 
                        else:
                            st.session_state.live_bankroll -= st.session_state.live_current_bet
                            action = "ORDER_EXECUTED"

                st.session_state.last_processed_time = latest['Time']
                st.session_state.live_history.append({"Time": latest['Time'], "Bankroll": st.session_state.live_bankroll, "Result": actual, "Action": action, "Bet On": st.session_state.live_pending_bet})

            st.markdown("### `> REALTIME_TELEMETRY`")
            m1, m2, m3 = st.columns(3)
            m1.metric("SYS_LEDGER_ACTIVE", f"${st.session_state.live_bankroll:,.2f}")
            m2.metric("QUEUED_ALLOCATION", f"${st.session_state.live_current_bet:,.2f}" if st.session_state.live_pending_bet else "$0.00")
            m3.metric("SYS_STATE", f"{st.session_state.live_history[-1]['Action']} {st.session_state.live_pending_bet}" if st.session_state.live_pending_bet else "STANDBY")
            
            if st.session_state.live_history:
                fig = px.line(pd.DataFrame(st.session_state.live_history), x="Time", y="Bankroll")
                fig.update_layout(template="plotly_dark", margin=dict(l=0, r=0, t=30, b=0))
                st.plotly_chart(fig, use_container_width=True)
                with st.expander("`> VIEW_RUNTIME_LOGS`"): st.dataframe(pd.DataFrame(st.session_state.live_history).iloc[::-1], width='stretch')
            
            if st.session_state.live_active:
                st.session_state.sim_index += 1; time.sleep(5); st.rerun()
