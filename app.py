import streamlit as st
import yfinance as yf
import pandas as pd
import math

# 1. 스마트폰 화면 최적화
st.set_page_config(page_title="스노우볼 파이프라인 백테스터", page_icon="❄️", layout="centered")
st.markdown("""
    <style>
    .block-container { padding-top: 1.2rem; padding-bottom: 2rem; }
    div[data-testid="stMetricValue"] { font-size: 1.2rem; }
    </style>
""", unsafe_allow_html=True)

st.title("❄️ TQQQ → UPRO 스노우볼 앱")
st.caption("TQQQ(U23 / 50%) 수확 → UPRO(U10 / 30%) 전이 통합 시뮬레이터")

# 2. 파라미터 설정 패널 (TQQQ 50%, UPRO 30% 분리 세팅)
with st.expander("⚙️ 전략 파라미터 설정 (터치하여 열기/닫기)", expanded=False):
    c_top1, c_top2, c_top3 = st.columns(3)
    with c_top1:
        start_year = st.number_input("시작 연도", value=2016, min_value=2010, max_value=2026, step=1)
    with c_top2:
        exchange_rate = st.number_input("적용 환율(원)", value=1350, min_value=500, max_value=3000, step=10)
    with c_top3:
        sgov_rate = st.number_input("SGOV 이자(%)", value=1.0, min_value=0.0, max_value=20.0, step=0.1) / 100.0

    c_mid1, c_mid2 = st.columns(2)
    with c_mid1:
        rebal_months = st.number_input("리밸런싱 주기(개월)", value=12, min_value=0, max_value=60, step=1)
    with c_mid2:
        ma200_filter = st.selectbox("200일선 추세필터", [1, 0], format_func=lambda x: "사용(1)" if x == 1 else "미사용(0)")

    st.markdown("---")
    st.markdown("**① 메인 엔진: TQQQ (기본 U23: -15%, +15%, 3분할, 4분할 / 비중 50%)**")
    t1, t2 = st.columns(2)
    with t1:
        t_init_stock = st.number_input("TQQQ 초기 주식(원)", value=5000000, min_value=0, step=1000000)
        t_init_cash = st.number_input("TQQQ 초기 예수금(원)", value=5000000, min_value=0, step=1000000)
        t_bband = st.number_input("TQQQ 매수 밴드(%)", value=-15.0, min_value=-50.0, max_value=-1.0, step=1.0) / 100.0
        t_bsplit = st.number_input("TQQQ 분할 매수", value=3, min_value=1, max_value=20, step=1)
        t_crisis_amt = st.number_input("TQQQ 위기추가금", value=5000000, min_value=0, step=1000000)
        t_thresh = st.number_input("인출 임계자산(원)", value=50000000, min_value=0, step=5000000)
    with t2:
        t_m_add = st.number_input("TQQQ 월 적립금(원)", value=400000, min_value=0, step=100000)
        t_sband = st.number_input("TQQQ 매도 밴드(%)", value=15.0, min_value=1.0, max_value=50.0, step=1.0) / 100.0
        t_ssplit = st.number_input("TQQQ 분할 매도", value=4, min_value=1, max_value=20, step=1)
        t_crisis_step = st.number_input("TQQQ 추가투입 회차", value=3, min_value=0, max_value=20, step=1)
        t_withdraw = st.number_input("월 인출→UPRO 이체", value=500000, min_value=0, step=100000)
        t_stock_ratio_pct = st.number_input("TQQQ 주식 비중(%)", value=50, min_value=10, max_value=90, step=5)

    st.markdown("---")
    st.markdown("**② 보조 엔진: UPRO (기본 U10: -5%, +5%, 5분할, 4분할 / 비중 30%)**")
    u1, u2 = st.columns(2)
    with u1:
        u_init_stock = st.number_input("UPRO 초기 주식(원)", value=500000, min_value=0, step=500000)
        u_init_cash = st.number_input("UPRO 초기 예수금(원)", value=500000, min_value=0, step=500000)
        u_bband = st.number_input("UPRO 매수 밴드(%)", value=-5.0, min_value=-50.0, max_value=-1.0, step=1.0) / 100.0
        u_bsplit = st.number_input("UPRO 분할 매수", value=5, min_value=1, max_value=20, step=1)
        u_crisis_amt = st.number_input("UPRO 위기추가금", value=1000000, min_value=0, step=500000)
    with u2:
        u_m_add = st.number_input("UPRO 기본 월 적립", value=100000, min_value=0, step=50000)
        u_sband = st.number_input("UPRO 매도 밴드(%)", value=5.0, min_value=1.0, max_value=50.0, step=1.0) / 100.0
        u_ssplit = st.number_input("UPRO 분할 매도", value=4, min_value=1, max_value=20, step=1)
        u_crisis_step = st.number_input("UPRO 추가투입 회차", value=5, min_value=0, max_value=20, step=1)
        u_stock_ratio_pct = st.number_input("UPRO 주식 비중(%)", value=30, min_value=10, max_value=90, step=5)

run_btn = st.button("🚀 스노우볼 시뮬레이션 실행", type="primary")

# 3. 주가 데이터 수집
@st.cache_data(ttl=3600)
def get_market_data(year: int):
    start_dt = f"{year - 2}-01-01"
    tqqq = yf.download("TQQQ", start=start_dt, progress=False)
    upro = yf.download("UPRO", start=start_dt, progress=False)

    if tqqq is None or upro is None or tqqq.empty or upro.empty:
        return pd.DataFrame(), pd.DataFrame()

    for df in (tqqq, upro):
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df["MA200"] = df["Close"].rolling(200).mean()

    tqqq = tqqq[tqqq.index.year >= year].copy()
    upro = upro[upro.index.year >= year].copy()
    common_idx = tqqq.index.intersection(upro.index)
    return tqqq.loc[common_idx], upro.loc[common_idx]

def get_iso_week_id(dt: pd.Timestamp) -> tuple:
    iso = dt.isocalendar()
    return (int(iso.year), int(iso.week))

# 4. Apps Script 원본 엔진 1:1 이식 함수 (개별 주식 비중 파라미터 적용)
def simulate_asset(df: pd.DataFrame, init_stock_krw: float, init_cash_krw: float,
                   in_buy_band: float, in_sell_band: float, tot_buy_steps: int, tot_sell_steps: int,
                   extra_amt: float, extra_step: int, monthly_in_series: list,
                   thresh_asset: float, monthly_out: float, asset_stock_ratio_pct: int):
    
    stock_ratio = asset_stock_ratio_pct / 100.0
    daily_interest = sgov_rate / 252.0
    buy_band = 1.0 + in_buy_band
    sell_band = 1.0 + in_sell_band

    dates = df.index.tolist()
    highs = df["High"].astype(float).tolist()
    lows = df["Low"].astype(float).tolist()
    closes = df["Close"].astype(float).tolist()
    ma200s = df["MA200"].tolist()

    first_price = closes[0]
    cash = float(init_cash_krw)
    shares = 0
    init_share_price_krw = first_price * exchange_rate
    if init_stock_krw > 0 and init_share_price_krw > 0:
        shares = math.floor(init_stock_krw / init_share_price_krw)
        cash += init_stock_krw - (shares * init_share_price_krw)

    buy_step = 1
    sell_step = 1
    last_sold_week_high = 0.0
    last_traded_week = None

    total_extra_injected = 0.0
    total_monthly_injected = 0.0
    total_withdrawal = 0.0
    has_triggered_ma200 = False

    cur_week_high = first_price
    cur_week_low = first_price
    cur_week = get_iso_week_id(dates[0])
    exact_prev_week_low = first_price
    exact_prev_week_high = first_price

    last_rebal_date = dates[0]
    prev_month = dates[0].month

    daily_records = []
    monthly_withdrawals_out = []

    for i, dt in enumerate(dates):
        high, low, close = highs[i], lows[i], closes[i]
        cur_ma200 = None if pd.isna(ma200s[i]) else float(ma200s[i])
        week_num = get_iso_week_id(dt)

        if week_num != cur_week:
            exact_prev_week_high = cur_week_high
            exact_prev_week_low = cur_week_low
            cur_week_high = high
            cur_week_low = low
            cur_week = week_num
        else:
            if high > cur_week_high:
                cur_week_high = high
            if low < cur_week_low:
                cur_week_low = low

        cash += cash * daily_interest

        target_buy_price = exact_prev_week_low * buy_band
        target_sell_price = exact_prev_week_high * sell_band
        trade_note = ""

        # 200일선 상향 돌파 조건
        is_crossing_up_ma200 = False
        if ma200_filter == 1 and i > 0 and not has_triggered_ma200 and buy_step == 1:
            prev_close = closes[i - 1]
            prev_ma200 = None if pd.isna(ma200s[i - 1]) else float(ma200s[i - 1])
            if prev_ma200 is not None and cur_ma200 is not None:
                if prev_close <= prev_ma200 and close > cur_ma200:
                    is_crossing_up_ma200 = True

        # 월초 적립 및 임계자산 초과 시 인출
        withdrawn_today = 0.0
        if dt.month != prev_month:
            cur_tot_asset = cash + (shares * close * exchange_rate)
            m_dep = monthly_in_series[i]
            if cur_tot_asset < thresh_asset:
                cash += m_dep
                total_monthly_injected += m_dep
            else:
                if monthly_out > 0:
                    if cash >= monthly_out:
                        cash -= monthly_out
                    else:
                        needed = monthly_out - cash
                        cash = 0.0
                        shares -= math.ceil(needed / (close * exchange_rate))
                        if shares < 0:
                            shares = 0
                    total_withdrawal += monthly_out
                    withdrawn_today = monthly_out
        prev_month = dt.month
        monthly_withdrawals_out.append(withdrawn_today)

        # 정기 리밸런싱 (종목별 설정 비중 적용)
        if rebal_months > 0:
            months_passed = (dt.year - last_rebal_date.year) * 12 + (dt.month - last_rebal_date.month)
            if months_passed >= rebal_months:
                tot_asset = cash + (shares * close * exchange_rate)
                target_stock_asset = tot_asset * stock_ratio
                shares = math.floor(target_stock_asset / (close * exchange_rate))
                cash = tot_asset - (shares * close * exchange_rate)

                buy_step = 1
                sell_step = 1
                has_triggered_ma200 = False
                last_rebal_date = dt
                trade_note = f"⚖️ 정기 리밸런싱({asset_stock_ratio_pct}:{100 - asset_stock_ratio_pct})"
                last_traded_week = week_num

        # 그리드 매수 / 매도 실행
        if trade_note == "":
            if week_num == last_traded_week and not is_crossing_up_ma200:
                pass
            else:
                is_grid_buy = (low <= target_buy_price)
                can_buy_cond = (cash > 0 or (extra_step > 0 and buy_step == extra_step and extra_amt > 0))
                if buy_step <= tot_buy_steps and (is_grid_buy or is_crossing_up_ma200) and can_buy_cond:
                    if extra_step > 0 and buy_step == extra_step and extra_amt > 0:
                        cash += extra_amt
                        total_extra_injected += extra_amt

                    fraction = 1.0 / (tot_buy_steps - buy_step + 1)
                    buy_amount = cash * fraction
                    buy_price_usd = close if is_crossing_up_ma200 else target_buy_price

                    bought_shares = math.floor(buy_amount / (buy_price_usd * exchange_rate))
                    actual_buy_amount = bought_shares * buy_price_usd * exchange_rate

                    shares += bought_shares
                    cash -= actual_buy_amount
                    if is_crossing_up_ma200:
                        has_triggered_ma200 = True

                    trade_note = f"🟢 매수 {buy_step}단계"
                    buy_step += 1
                    sell_step = 1
                    last_traded_week = week_num

                elif high >= target_sell_price and shares > 0:
                    if exact_prev_week_high != last_sold_week_high:
                        fraction = 1.0 if (sell_step == tot_sell_steps or tot_sell_steps == 1) else (1.0 / (tot_sell_steps - sell_step + 1))
                        sell_shares = math.floor(shares * fraction)
                        sell_amount = sell_shares * target_sell_price * exchange_rate

                        shares -= sell_shares
                        cash += sell_amount

                        trade_note = f"🔴 매도 {sell_step}회차"
                        last_sold_week_high = exact_prev_week_high
                        last_traded_week = week_num

                        sell_step = 1 if sell_step >= tot_sell_steps else (sell_step + 1)
                        buy_step = 1
                        has_triggered_ma200 = False

        tot_val = cash + (shares * close * exchange_rate)
        real_tot_val = tot_val + total_withdrawal
        daily_records.append({
            "Date": dt,
            "Close": close,
            "Shares": shares,
            "Cash": cash,
            "Balance": tot_val,
            "Withdrawal": total_withdrawal,
            "RealAsset": real_tot_val,
            "Note": trade_note
        })

    res_df = pd.DataFrame(daily_records).set_index("Date")
    next_buy_p = exact_prev_week_low * buy_band
    next_sell_p = exact_prev_week_high * sell_band
    next_buy_f = 1.0 / (tot_buy_steps - buy_step + 1)
    next_buy_sh = max(0, math.floor((cash * next_buy_f) / (next_buy_p * exchange_rate)))
    next_sell_f = 1.0 if (sell_step == tot_sell_steps or tot_sell_steps == 1) else (1.0 / (tot_sell_steps - sell_step + 1))
    next_sell_sh = max(0, math.floor(shares * next_sell_f))

    return res_df, monthly_withdrawals_out, {
        "extra_in": total_extra_injected,
        "monthly_in": total_monthly_injected,
        "withdrawn": total_withdrawal,
        "final_bal": float(res_df["Balance"].iloc[-1]),
        "final_real": float(res_df["RealAsset"].iloc[-1]),
        "last_close": closes[-1],
        "prev_low": exact_prev_week_low,
        "prev_high": exact_prev_week_high,
        "next_buy_p": next_buy_p,
        "next_sell_p": next_sell_p,
        "next_buy_sh": next_buy_sh,
        "next_sell_sh": next_sell_sh,
        "last_note": daily_records[-1]["Note"] or "🔍 관망 (그리드 밴드 대기 중)"
    }

# 5. 실행 및 대시보드 출력
if run_btn or "booted_v3" not in st.session_state:
    st.session_state["booted_v3"] = True
    with st.spinner("Apps Script 원본 로직으로 정밀 시뮬레이션 중..."):
        t_df, u_df = get_market_data(int(start_year))
        if t_df.empty or u_df.empty:
            st.error("데이터를 불러오지 못했습니다. 잠시 후 다시 실행해 주세요.")
        else:
            n_days = len(t_df)
            # 1) TQQQ 실행 (주식 비중 50%)
            t_monthly_in = [float(t_m_add)] * n_days
            t_res, t_w_out, t_stat = simulate_asset(
                t_df, float(t_init_stock), float(t_init_cash),
                t_bband, t_sband, int(t_bsplit), int(t_ssplit),
                float(t_crisis_amt), int(t_crisis_step), t_monthly_in,
                float(t_thresh), float(t_withdraw), int(t_stock_ratio_pct)
            )

            # 2) UPRO 실행 (주식 비중 30% + TQQQ 인출금 유입)
            u_monthly_in = [float(u_m_add) + w for w in t_w_out]
            u_res, _, u_stat = simulate_asset(
                u_df, float(u_init_stock), float(u_init_cash),
                u_bband, u_sband, int(u_bsplit), int(u_ssplit),
                float(u_crisis_amt), int(u_crisis_step), u_monthly_in,
                1e15, 0.0, int(u_stock_ratio_pct)
            )

            # 통합 지표 계산
            tot_df = pd.DataFrame({
                "TQQQ 잔고": t_res["Balance"],
                "UPRO 잔고": u_res["Balance"],
                "통합 순자산": t_res["Balance"] + u_res["Balance"]
            })
            tot_df["MDD"] = tot_df["통합 순자산"] / tot_df["통합 순자산"].cummax() - 1.0

            yrs = max(0.1, (t_df.index[-1] - t_df.index[0]).days / 365.25)
            ext_principal = (
                float(t_init_stock + t_init_cash + u_init_stock + u_init_cash)
                + t_stat["extra_in"] + t_stat["monthly_in"]
                + u_stat["extra_in"] + (u_stat["monthly_in"] - t_stat["withdrawn"])
            )
            final_combined = float(tot_df["통합 순자산"].iloc[-1])
            combined_cagr = (final_combined / ext_principal) ** (1.0 / yrs) - 1.0

            transfer_dates = [d.strftime("%Y-%m") for d, w in zip(t_df.index, t_w_out) if w > 0]
            first_transfer = transfer_dates[0] if transfer_dates else "미도달"

            # 실전 매매 주문서
            st.subheader("📋 오늘자 실전 매매 주문서")
            c1, c2 = st.columns(2)
            with c1:
                st.info(
                    f"**TQQQ (${t_stat['last_close']:.2f} / 비중 {t_stock_ratio_pct}%)**\n\n"
                    f"상태: **{t_stat['last_note']}**\n\n"
                    f"🛒 매수: `${t_stat['next_buy_p']:.2f}` ({t_stat['next_buy_sh']}주)\n\n"
                    f"🎯 매도: `${t_stat['next_sell_p']:.2f}` ({t_stat['next_sell_sh']}주)"
                )
            with c2:
                st.success(
                    f"**UPRO (${u_stat['last_close']:.2f} / 비중 {u_stock_ratio_pct}%)**\n\n"
                    f"상태: **{u_stat['last_note']}**\n\n"
                    f"🛒 매수: `${u_stat['next_buy_p']:.2f}` ({u_stat['next_buy_sh']}주)\n\n"
                    f"🎯 매도: `${u_stat['next_sell_p']:.2f}` ({u_stat['next_sell_sh']}주)"
                )

            # 통합 성과 요약
            st.subheader("📊 통합 포트폴리오 성과 요약")
            m1, m2 = st.columns(2)
            m1.metric("최종 통합 자산", f"{final_combined:,.0f}원", f"순수 원금 {ext_principal:,.0f}원")
            m2.metric("통합 연복리 (CAGR)", f"{combined_cagr*100:.2f}%", f"통합 MDD {tot_df['MDD'].min()*100:.2f}%")

            m3, m4 = st.columns(2)
            m3.metric("파이프라인 개시", first_transfer, f"T→U 누적 이체 {t_stat['withdrawn']:,.0f}원")
            m4.metric("계좌별 최종 잔고", f"T {t_stat['final_bal']/10000:,.0f}만 / U {u_stat['final_bal']/10000:,.0f}만", f"비중 {t_stat['final_bal']/final_combined*100:.0f}% : {u_stat['final_bal']/final_combined*100:.0f}%")

            st.subheader("📈 스노우볼 자산 성장 곡선")
            st.area_chart(tot_df[["TQQQ 잔고", "UPRO 잔고"]])