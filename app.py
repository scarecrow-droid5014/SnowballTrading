import base64
import datetime
import hashlib
import json
import math
import re
import numpy as np
import pandas as pd
import requests
import streamlit as st
import yfinance as yf

st.set_page_config(
    page_title="TQQQ→UPRO 스노우볼 매매 앱",
    page_icon="❄️",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# 모바일 친화적 스타일링
st.markdown(
    """
    <style>
    .block-container { padding-top: 1.5rem; padding-bottom: 3rem; max-width: 560px; }
    .order-card { background-color: #f8f9fa; border-radius: 12px; padding: 14px; border: 1px solid #e9ecef; margin-bottom: 12px; }
    .buy-box { background-color: #e8f5e9; border-radius: 10px; padding: 12px; color: #1b5e20; }
    .sell-box { background-color: #ffebee; border-radius: 10px; padding: 12px; color: #b71c1c; }
    .guide-banner { background-color: #fff3cd; color: #856404; padding: 8px 12px; border-radius: 8px; font-weight: 600; font-size: 0.85rem; margin-top: 8px; }
    .user-badge { background-color: #eef2ff; color: #3730a3; padding: 8px 12px; border-radius: 8px; font-size: 0.85rem; font-weight: 600; margin-bottom: 10px; }
    .transfer-badge { background-color: #e0f2fe; color: #0369a1; padding: 10px 14px; border-radius: 8px; font-size: 0.88rem; font-weight: 600; margin-bottom: 12px; }
    </style>
    """,
    unsafe_allow_html=True,
)


# --- GitHub API 영구 저장소 연동 ---
def get_github_config():
    token = st.secrets.get("GITHUB_TOKEN", "")
    repo = st.secrets.get("GITHUB_REPO", "scarecrow-droid5014/SnowballTrading")
    return token, repo


def sanitize_username(username: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9가-힣_-]", "", username.strip().lower())
    return cleaned[:30]


def hash_pin(pin: str) -> str:
    return hashlib.sha256(pin.strip().encode("utf-8")).hexdigest()


def make_auto_token(username: str, pin_hash: str) -> str:
    raw = f"{username}:{pin_hash}:snowball_pipeline_v1".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def load_user_from_github(username: str):
    token, repo = get_github_config()
    if not token:
        return None, None, "Streamlit Secrets에 GITHUB_TOKEN이 설정되지 않았습니다."

    safe_user = sanitize_username(username)
    if not safe_user:
        return None, None, "올바른 닉네임을 입력해 주세요."

    url = f"https://api.github.com/repos/{repo}/contents/user_data/{safe_user}.json"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }
    res = requests.get(url, headers=headers, timeout=10)
    if res.status_code == 200:
        body = res.json()
        raw_content = base64.b64decode(body["content"]).decode("utf-8")
        return json.loads(raw_content), body["sha"], None
    elif res.status_code == 404:
        return None, None, None
    else:
        return None, None, f"GitHub 조회 오류 ({res.status_code}): {res.text}"


def save_user_to_github(username: str, pin_hash: str, cfg: dict, overrides: dict, sha: str = None):
    token, repo = get_github_config()
    if not token:
        return None, "Streamlit Secrets에 GITHUB_TOKEN이 설정되지 않았습니다."

    safe_user = sanitize_username(username)
    url = f"https://api.github.com/repos/{repo}/contents/user_data/{safe_user}.json"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }

    if not sha:
        check_res = requests.get(url, headers=headers, timeout=10)
        if check_res.status_code == 200:
            sha = check_res.json().get("sha")

    payload_obj = {
        "username": safe_user,
        "pin_hash": pin_hash,
        "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "cfg": cfg,
        "overrides": overrides,
    }
    encoded_content = base64.b64encode(
        json.dumps(payload_obj, ensure_ascii=False, indent=2).encode("utf-8")
    ).decode("utf-8")

    body = {
        "message": f"Update user data: {safe_user}",
        "content": encoded_content,
    }
    if sha:
        body["sha"] = sha

    put_res = requests.put(url, headers=headers, json=body, timeout=10)
    if put_res.status_code in (200, 201):
        return put_res.json().get("content", {}).get("sha"), None
    elif put_res.status_code == 409:
        check_res = requests.get(url, headers=headers, timeout=10)
        if check_res.status_code == 200:
            body["sha"] = check_res.json().get("sha")
            retry_res = requests.put(url, headers=headers, json=body, timeout=10)
            if retry_res.status_code in (200, 201):
                return retry_res.json().get("content", {}).get("sha"), None
        return sha, f"GitHub 저장 충돌 오류 ({put_res.status_code}): 다시 시도해 주세요."
    else:
        return sha, f"GitHub 저장 실패 ({put_res.status_code}): {put_res.text}"


# --- 주가 데이터 수집 ---
@st.cache_data(ttl=3600)
def load_market_data():
    tqqq = yf.download("TQQQ", period="5y", interval="1d", progress=False, auto_adjust=False)
    upro = yf.download("UPRO", period="5y", interval="1d", progress=False, auto_adjust=False)

    if tqqq is None or upro is None or tqqq.empty or upro.empty:
        return pd.DataFrame(), pd.DataFrame()

    processed = []
    for df in (tqqq, upro):
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.reset_index()
        df["Date"] = pd.to_datetime(df["Date"]).dt.tz_localize(None)
        df = df.sort_values("Date").reset_index(drop=True)
        df["MA200"] = df["Close"].rolling(window=200).mean()
        processed.append(df)

    t_df, u_df = processed[0], processed[1]
    common_dates = set(t_df["Date"]).intersection(set(u_df["Date"]))
    t_df = t_df[t_df["Date"].isin(common_dates)].sort_values("Date").reset_index(drop=True)
    u_df = u_df[u_df["Date"].isin(common_dates)].sort_values("Date").reset_index(drop=True)
    return t_df, u_df


def get_week_key(dt: pd.Timestamp) -> str:
    iso = dt.isocalendar()
    return f"{iso.year}-{iso.week:02d}"


# --- 단일 자산 시뮬레이션 엔진 (백테스터 원본 로직 + 실전 수동 보정 통합) ---
def simulate_asset_engine(
    raw_df: pd.DataFrame,
    start_date: datetime.date,
    init_shares: int,
    init_cash_krw: float,
    init_cash_usd: float,
    buy_band_pct: float,
    sell_band_pct: float,
    tot_buy_steps: int,
    tot_sell_steps: int,
    extra_amt: float,
    extra_step: int,
    monthly_in_map: dict,
    default_monthly_in: float,
    thresh_asset: float,
    monthly_out: float,
    asset_stock_ratio_pct: int,
    rebal_months: int,
    use_ma200: bool,
    sgov_rate_pct: float,
    fx_rate: float,
    manual_overrides: dict,
):
    raw_df = raw_df.copy()
    raw_df["WeekKey"] = raw_df["Date"].apply(get_week_key)

    weekly_stats = (
        raw_df.groupby("WeekKey")
        .agg(High=("High", "max"), Low=("Low", "min"))
        .to_dict("index")
    )
    all_weeks = sorted(weekly_stats.keys())

    effective_start = start_date
    if init_shares == 0 and init_cash_krw == 0 and init_cash_usd == 0 and manual_overrides:
        earliest_ov = datetime.date.fromisoformat(min(manual_overrides.keys()))
        if earliest_ov < effective_start:
            effective_start = earliest_ov

    start_ts = pd.Timestamp(effective_start)
    prior_df = raw_df[raw_df["Date"] < start_ts]
    df = raw_df[raw_df["Date"] >= start_ts].reset_index(drop=True)
    if df.empty:
        df = raw_df.tail(1).reset_index(drop=True)

    first_price = float(df.loc[0, "Close"])
    first_week = df.loc[0, "WeekKey"]

    exact_prev_week_high = first_price
    exact_prev_week_low = first_price

    if not prior_df.empty:
        prior_last_week = prior_df.iloc[-1]["WeekKey"]
        if prior_last_week != first_week and prior_last_week in weekly_stats:
            exact_prev_week_high = weekly_stats[prior_last_week]["High"]
            exact_prev_week_low = weekly_stats[prior_last_week]["Low"]
    elif first_week in all_weeks:
        idx = all_weeks.index(first_week)
        if idx > 0:
            prev_wk = all_weeks[idx - 1]
            exact_prev_week_high = weekly_stats[prev_wk]["High"]
            exact_prev_week_low = weekly_stats[prev_wk]["Low"]

    shares = int(init_shares)
    cash = float(init_cash_krw) + (float(init_cash_usd) * fx_rate)
    initial_total_cash_krw = cash

    stock_ratio = asset_stock_ratio_pct / 100.0
    daily_interest = (sgov_rate_pct / 100.0) / 252.0
    buy_band = 1.0 + (buy_band_pct / 100.0)
    sell_band = 1.0 + (sell_band_pct / 100.0)

    buy_step = 1
    sell_step = 1
    last_sold_week_high = 0.0
    last_traded_week = None
    has_triggered_ma200 = False

    total_extra_injected = 0.0
    total_monthly_injected = 0.0
    total_withdrawal = 0.0

    cur_week = first_week
    cur_week_high = float(df.loc[0, "High"])
    cur_week_low = float(df.loc[0, "Low"])

    last_rebal_date = df.loc[0, "Date"]
    prev_month = df.loc[0, "Date"].month

    daily_records = []
    withdrawals_by_date = {}

    for i, row in df.iterrows():
        dt = row["Date"]
        date_str = dt.strftime("%Y-%m-%d")
        high = float(row["High"])
        low = float(row["Low"])
        close = float(row["Close"])
        cur_ma200 = None if pd.isna(row["MA200"]) else float(row["MA200"])
        week_num = row["WeekKey"]

        if week_num != cur_week:
            exact_prev_week_high = cur_week_high
            exact_prev_week_low = cur_week_low
            cur_week_high = high
            cur_week_low = low
            cur_week = week_num
        else:
            cur_week_high = max(cur_week_high, high)
            cur_week_low = min(cur_week_low, low)

        cash += cash * daily_interest

        # 월초 적립 및 임계자산 초과 시 인출 (파이프라인 이체)
        withdrawn_today = 0.0
        day_actions = []
        if dt.month != prev_month:
            cur_tot_asset = cash + (shares * close * fx_rate)
            m_dep = monthly_in_map.get(date_str, default_monthly_in)
            if cur_tot_asset < thresh_asset:
                if m_dep > 0:
                    cash += m_dep
                    total_monthly_injected += m_dep
                    day_actions.append(f"💰 적립 +{m_dep:,.0f}원")
            else:
                if monthly_out > 0:
                    if cash >= monthly_out:
                        cash -= monthly_out
                        day_actions.append(f"📤 UPRO이체 -{monthly_out:,.0f}원")
                    else:
                        needed = monthly_out - cash
                        sell_sh = math.ceil(needed / (close * fx_rate))
                        cash = 0.0
                        shares = max(0, shares - sell_sh)
                        day_actions.append(f"📤 UPRO이체 -{monthly_out:,.0f}원({sell_sh}주 매도)")
                    total_withdrawal += monthly_out
                    withdrawn_today = monthly_out
        prev_month = dt.month
        withdrawals_by_date[date_str] = withdrawn_today

        trade_note = ""

        # 실전 수동 체결 잔고 반영
        if date_str in manual_overrides:
            ov = manual_overrides[date_str]
            new_shares = int(ov["shares"])
            ov_krw = float(ov.get("cash_krw", 0))
            ov_usd = float(ov.get("cash_usd", 0.0))
            new_cash = ov_krw + (ov_usd * fx_rate)
            cash_desc = f"{ov_krw:,.0f}원 + ${ov_usd:,.2f}" if ov_usd > 0 else f"{ov_krw:,.0f}원"

            if new_shares > shares:
                disp_step = min(buy_step, tot_buy_steps)
                trade_note = f"✏️ 실전 매수 {disp_step}단계 ({new_shares:,}주 / {cash_desc})"
                buy_step = min(tot_buy_steps + 1, buy_step + 1)
                sell_step = 1
                last_traded_week = week_num
            elif new_shares < shares:
                trade_note = f"✏️ 실전 매도 {sell_step}회차 ({new_shares:,}주 / {cash_desc})"
                if new_shares == 0:
                    sell_step = 1
                else:
                    sell_step = 1 if sell_step >= tot_sell_steps else (sell_step + 1)
                buy_step = 1
                has_triggered_ma200 = False
                last_traded_week = week_num
            else:
                trade_note = f"✏️ 실전 잔고 ({new_shares:,}주 / {cash_desc})"

            shares = new_shares
            cash = new_cash
        else:
            target_buy_price = exact_prev_week_low * buy_band
            target_sell_price = exact_prev_week_high * sell_band

            is_crossing_up_ma200 = False
            if use_ma200 and i > 0 and not has_triggered_ma200 and buy_step == 1:
                prev_row = df.iloc[i - 1]
                prev_ma200 = None if pd.isna(prev_row["MA200"]) else float(prev_row["MA200"])
                if prev_ma200 is not None and cur_ma200 is not None:
                    if float(prev_row["Close"]) <= prev_ma200 and close > cur_ma200:
                        is_crossing_up_ma200 = True

            # 정기 리밸런싱
            if rebal_months > 0 and (shares > 0 or cash > 0):
                months_passed = (dt.year - last_rebal_date.year) * 12 + (dt.month - last_rebal_date.month)
                if months_passed >= rebal_months:
                    tot_asset = cash + (shares * close * fx_rate)
                    shares = math.floor((tot_asset * stock_ratio) / (close * fx_rate))
                    cash = tot_asset - (shares * close * fx_rate)
                    buy_step = 1
                    sell_step = 1
                    has_triggered_ma200 = False
                    last_rebal_date = dt
                    trade_note = f"⚖️ 정기 리밸런싱({asset_stock_ratio_pct}:{100 - asset_stock_ratio_pct})"
                    last_traded_week = week_num

            # 그리드 매수 / 매도
            if trade_note == "":
                if week_num == last_traded_week and not is_crossing_up_ma200:
                    pass
                else:
                    is_grid_buy = low <= target_buy_price
                    can_buy_cond = cash > 0 or (extra_step > 0 and buy_step == extra_step and extra_amt > 0)
                    if buy_step <= tot_buy_steps and (is_grid_buy or is_crossing_up_ma200) and can_buy_cond:
                        if extra_step > 0 and buy_step == extra_step and extra_amt > 0:
                            cash += extra_amt
                            total_extra_injected += extra_amt
                            day_actions.append(f"🆘 위기투입 +{extra_amt:,.0f}원")

                        fraction = 1.0 / (tot_buy_steps - buy_step + 1)
                        buy_price_usd = close if is_crossing_up_ma200 else target_buy_price
                        bought_shares = math.floor((cash * fraction) / (buy_price_usd * fx_rate))
                        if bought_shares > 0:
                            shares += bought_shares
                            cash -= bought_shares * buy_price_usd * fx_rate
                            if is_crossing_up_ma200:
                                has_triggered_ma200 = True
                            reason = "200일선 돌파" if is_crossing_up_ma200 else f"${target_buy_price:.2f}"
                            trade_note = f"🟢 매수 {buy_step}단계 ({bought_shares}주·{reason})"
                            buy_step += 1
                            sell_step = 1
                            last_traded_week = week_num

                    elif high >= target_sell_price and shares > 0:
                        if exact_prev_week_high != last_sold_week_high:
                            fraction = (
                                1.0
                                if (sell_step == tot_sell_steps or tot_sell_steps == 1)
                                else (1.0 / (tot_sell_steps - sell_step + 1))
                            )
                            sell_shares = math.floor(shares * fraction)
                            if sell_shares > 0:
                                shares -= sell_shares
                                cash += sell_shares * target_sell_price * fx_rate
                                trade_note = f"🔴 매도 {sell_step}회차 ({sell_shares}주·${target_sell_price:.2f})"
                                last_sold_week_high = exact_prev_week_high
                                last_traded_week = week_num
                                sell_step = 1 if sell_step >= tot_sell_steps else (sell_step + 1)
                                buy_step = 1
                                has_triggered_ma200 = False

        if day_actions:
            prefix = "[" + " / ".join(day_actions) + "]"
            trade_note = f"{trade_note} {prefix}".strip()

        tot_val = cash + (shares * close * fx_rate)
        real_tot_val = tot_val + total_withdrawal
        daily_records.append(
            {
                "날짜": date_str,
                "종가($)": round(close, 2),
                "보유수량": shares,
                "예수금합계(원)": round(cash),
                "계좌잔고(원)": round(tot_val),
                "누적인출(원)": round(total_withdrawal),
                "실질총자산(원)": round(real_tot_val),
                "매매기록": trade_note,
            }
        )

    res_df = pd.DataFrame(daily_records)

    # 주말 / 장 마감 후 차주 예약 주문가 산출
    now_ny = pd.Timestamp.now(tz="America/New_York").tz_localize(None)
    this_week = get_week_key(now_ny)
    is_weekend_or_new_week = (this_week != cur_week) or (now_ny.weekday() >= 5)

    cur_week_full = weekly_stats.get(cur_week, {"High": cur_week_high, "Low": cur_week_low})
    prev_week_full = {"High": exact_prev_week_high, "Low": exact_prev_week_low}
    if cur_week in all_weeks:
        c_idx = all_weeks.index(cur_week)
        if c_idx > 0:
            prev_week_full = weekly_stats[all_weeks[c_idx - 1]]

    ref_high = cur_week_full["High"] if is_weekend_or_new_week else prev_week_full["High"]
    ref_low = cur_week_full["Low"] if is_weekend_or_new_week else prev_week_full["Low"]

    next_buy_p = ref_low * buy_band
    next_sell_p = ref_high * sell_band

    if buy_step <= tot_buy_steps and next_buy_p > 0:
        next_buy_f = 1.0 / (tot_buy_steps - buy_step + 1)
        next_buy_sh = max(0, math.floor((cash * next_buy_f) / (next_buy_p * fx_rate)))
    else:
        next_buy_sh = 0

    next_sell_f = (
        1.0
        if (sell_step == tot_sell_steps or tot_sell_steps == 1)
        else (1.0 / (tot_sell_steps - sell_step + 1))
    )
    next_sell_sh = max(0, math.floor(shares * next_sell_f))

    principal = (
        (init_shares * first_price * fx_rate)
        + initial_total_cash_krw
        + total_monthly_injected
        + total_extra_injected
    )

    return res_df, withdrawals_by_date, {
        "principal": round(principal),
        "extra_in": total_extra_injected,
        "monthly_in": total_monthly_injected,
        "withdrawn": total_withdrawal,
        "final_bal": float(res_df["계좌잔고(원)"].iloc[-1]),
        "final_real": float(res_df["실질총자산(원)"].iloc[-1]),
        "last_close": float(res_df["종가($)"].iloc[-1]),
        "last_shares": int(res_df["보유수량"].iloc[-1]),
        "last_cash": int(res_df["예수금합계(원)"].iloc[-1]),
        "ref_low": ref_low,
        "ref_high": ref_high,
        "next_buy_p": next_buy_p,
        "next_sell_p": next_sell_p,
        "next_buy_sh": next_buy_sh,
        "next_sell_sh": next_sell_sh,
        "buy_step": buy_step,
        "sell_step": sell_step,
        "last_note": daily_records[-1]["매매기록"] or "🔍 관망 (그리드 밴드 대기 중)",
    }


# --- UI 렌더링 ---
st.title("❄️ TQQQ → UPRO 스노우볼 매매 앱")

t_df_raw, u_df_raw = load_market_data()
if t_df_raw.empty or u_df_raw.empty:
    st.error("야후 파이낸스에서 TQQQ / UPRO 주가 데이터를 불러오지 못했습니다.")
    st.stop()

latest_db_date = t_df_raw.iloc[-1]["Date"].strftime("%Y-%m-%d")
min_db_date = t_df_raw.iloc[0]["Date"].date()
max_db_date = t_df_raw.iloc[-1]["Date"].date()
st.caption(f"TQQQ(U23) 수확 → UPRO(U10) 전이 실전 보조 · 기준일: {latest_db_date}")

DEFAULT_CFG = {
    "sd": max_db_date.isoformat(),
    "fx": 1350,
    "sg": 4.5,
    "rm": 12,
    "ma": True,
    # TQQQ 기본 파라미터 (U23 / 50%)
    "t_sh": 0,
    "t_ca": 0,
    "t_ca_usd": 0.0,
    "t_bb": -15.0,
    "t_sb": 15.0,
    "t_bs": 3,
    "t_ss": 4,
    "t_sr": 50,
    "t_md": 400000,
    "t_th": 50000000,
    "t_mw": 500000,
    "t_ea": 5000000,
    "t_es": 3,
    # UPRO 기본 파라미터 (U10 / 30%)
    "u_sh": 0,
    "u_ca": 0,
    "u_ca_usd": 0.0,
    "u_bb": -5.0,
    "u_sb": 5.0,
    "u_bs": 5,
    "u_ss": 4,
    "u_sr": 30,
    "u_md": 100000,
    "u_ea": 1000000,
    "u_es": 5,
}

if "logged_user" not in st.session_state:
    st.session_state.logged_user = ""
if "pin_hash" not in st.session_state:
    st.session_state.pin_hash = ""
if "file_sha" not in st.session_state:
    st.session_state.file_sha = None
if "saved_cfg" not in st.session_state:
    st.session_state.saved_cfg = DEFAULT_CFG.copy()
if "manual_overrides" not in st.session_state:
    st.session_state.manual_overrides = {"TQQQ": {}, "UPRO": {}}

# --- 자동 로그인 처리 (?u=닉네임&k=인증키) ---
qp_user = st.query_params.get("u", "")
qp_key = st.query_params.get("k", "")
if not st.session_state.logged_user and qp_user and qp_key:
    safe_u = sanitize_username(qp_user)
    if safe_u:
        existing_data, sha, err = load_user_from_github(safe_u)
        if existing_data and not err:
            stored_hash = existing_data.get("pin_hash", "")
            if make_auto_token(safe_u, stored_hash) == qp_key:
                merged = DEFAULT_CFG.copy()
                merged.update(existing_data.get("cfg", {}))
                ov = existing_data.get("overrides", {})
                if "TQQQ" not in ov:
                    ov = {"TQQQ": {}, "UPRO": {}}
                st.session_state.logged_user = safe_u
                st.session_state.pin_hash = stored_hash
                st.session_state.file_sha = sha
                st.session_state.saved_cfg = merged
                st.session_state.manual_overrides = ov

# --- 상단 내 계좌 불러오기 / 로그인 바 ---
if not st.session_state.logged_user:
    with st.expander("🔑 내 계좌 설정·기록 불러오기 (또는 새 사용자 등록)", expanded=True):
        st.caption("닉네임과 비밀번호(PIN)를 입력하면 클라우드에 내 설정과 매매 기록이 영구 저장됩니다.")
        with st.form("user_login_form"):
            lc1, lc2 = st.columns(2)
            in_user = lc1.text_input("닉네임 (영문/한글/숫자)", placeholder="예: lucas")
            in_pin = lc2.text_input("비밀번호 (PIN)", type="password", placeholder="4자리 이상")
            remember_me = st.checkbox("📱 이 기기에서 자동 로그인 유지 (홈 화면 추가/북마크용)", value=True)
            login_btn = st.form_submit_button("📂 불러오기 / 새 계좌 시작", use_container_width=True)

            if login_btn:
                safe_u = sanitize_username(in_user)
                if not safe_u or len(in_pin.strip()) < 4:
                    st.warning("닉네임과 4자리 이상의 비밀번호(PIN)를 입력해 주세요.")
                else:
                    p_hash = hash_pin(in_pin)
                    existing_data, sha, err = load_user_from_github(safe_u)
                    if err:
                        st.error(err)
                    elif existing_data is not None:
                        if existing_data.get("pin_hash") != p_hash:
                            st.error("비밀번호(PIN)가 일치하지 않습니다.")
                        else:
                            merged = DEFAULT_CFG.copy()
                            merged.update(existing_data.get("cfg", {}))
                            ov = existing_data.get("overrides", {})
                            if "TQQQ" not in ov:
                                ov = {"TQQQ": {}, "UPRO": {}}
                            st.session_state.logged_user = safe_u
                            st.session_state.pin_hash = p_hash
                            st.session_state.file_sha = sha
                            st.session_state.saved_cfg = merged
                            st.session_state.manual_overrides = ov
                            if remember_me:
                                st.query_params["u"] = safe_u
                                st.query_params["k"] = make_auto_token(safe_u, p_hash)
                            st.rerun()
                    else:
                        init_ov = {"TQQQ": {}, "UPRO": {}}
                        new_sha, save_err = save_user_to_github(safe_u, p_hash, DEFAULT_CFG.copy(), init_ov, None)
                        if save_err:
                            st.error(save_err)
                        else:
                            st.session_state.logged_user = safe_u
                            st.session_state.pin_hash = p_hash
                            st.session_state.file_sha = new_sha
                            st.session_state.saved_cfg = DEFAULT_CFG.copy()
                            st.session_state.manual_overrides = init_ov
                            if remember_me:
                                st.query_params["u"] = safe_u
                                st.query_params["k"] = make_auto_token(safe_u, p_hash)
                            st.rerun()
else:
    uc1, uc2 = st.columns([3, 1])
    uc1.markdown(
        f'<div class="user-badge">👤 접속 계정: <b>{st.session_state.logged_user}</b> (클라우드 자동 동기화 중)</div>',
        unsafe_allow_html=True,
    )
    if uc2.button("로그아웃", use_container_width=True):
        st.session_state.logged_user = ""
        st.session_state.pin_hash = ""
        st.session_state.file_sha = None
        st.session_state.saved_cfg = DEFAULT_CFG.copy()
        st.session_state.manual_overrides = {"TQQQ": {}, "UPRO": {}}
        st.query_params.clear()
        st.rerun()

saved_cfg = st.session_state.saved_cfg

tab_order, tab_cfg, tab_stats = st.tabs(["📋 실전 주문서", "⚙️ 내 투자 설정", "📊 성과 및 기록"])

with tab_cfg:
    st.subheader("공통 및 종목별 전략 파라미터")
    if st.session_state.logged_user:
        st.caption(f"**{st.session_state.logged_user}** 계정의 설정과 잔고는 저장 시 GitHub 클라우드에 영구 기록됩니다.")
    else:
        st.warning("상단에서 닉네임과 PIN으로 로그인하면 설정과 매매 기록이 영구 저장됩니다.")

    try:
        parsed_sd = datetime.date.fromisoformat(saved_cfg["sd"])
        default_start = min(max(parsed_sd, min_db_date), max_db_date)
    except Exception:
        default_start = max_db_date

    with st.form("pipeline_cfg_form"):
        st.markdown("**🌐 공통 운용 설정**")
        g1, g2, g3 = st.columns(3)
        start_date = g1.date_input("투자 시작일", value=default_start, min_value=min_db_date, max_value=max_db_date)
        fx_rate = g2.number_input("적용 환율(원/$)", min_value=500, max_value=3000, value=int(saved_cfg["fx"]), step=10)
        sgov_rate_pct = g3.number_input("예수금 연이자(%)", min_value=0.0, max_value=20.0, value=float(saved_cfg["sg"]), step=0.1)

        g4, g5 = st.columns(2)
        rebal_months = g4.number_input("정기 리밸런싱 주기(개월)", min_value=0, max_value=60, value=int(saved_cfg["rm"]), step=1)
        ma200_idx = 0 if saved_cfg["ma"] else 1
        use_ma200 = g5.selectbox("200일선 추세필터", options=[True, False], index=ma200_idx, format_func=lambda x: "사용함" if x else "미사용")

        st.divider()
        st.markdown("**① 메인 엔진: TQQQ (기본 U23 / 비중 50%)**")
        tc0_1, tc0_2, tc0_3 = st.columns(3)
        t_init_shares = tc0_1.number_input("TQQQ 초기수량(주)", min_value=0, value=int(saved_cfg["t_sh"]), step=1)
        t_init_cash_krw = tc0_2.number_input("TQQQ 원화예수금", min_value=0, value=int(saved_cfg["t_ca"]), step=500000)
        t_init_cash_usd = tc0_3.number_input("TQQQ 달러예수금($)", min_value=0.0, value=float(saved_cfg["t_ca_usd"]), step=100.0, format="%.2f")

        tc1, tc2 = st.columns(2)
        t_bband = tc1.number_input("TQQQ 매수 밴드(%)", value=float(saved_cfg["t_bb"]), step=1.0)
        t_sband = tc2.number_input("TQQQ 매도 밴드(%)", value=float(saved_cfg["t_sb"]), step=1.0)

        tc3, tc4 = st.columns(2)
        t_bsplit = tc3.number_input("TQQQ 분할 매수", min_value=1, max_value=20, value=int(saved_cfg["t_bs"]), step=1)
        t_ssplit = tc4.number_input("TQQQ 분할 매도", min_value=1, max_value=20, value=int(saved_cfg["t_ss"]), step=1)

        tc5, tc6 = st.columns(2)
        t_stock_ratio = tc5.number_input("TQQQ 주식 비중(%)", min_value=0, max_value=100, value=int(saved_cfg["t_sr"]), step=5)
        t_m_add = tc6.number_input("TQQQ 월 적립금(원)", min_value=0, value=int(saved_cfg["t_md"]), step=100000)

        tc7, tc8 = st.columns(2)
        t_thresh = tc7.number_input("인출 임계자산(원)", min_value=0, value=int(saved_cfg["t_th"]), step=5000000)
        t_withdraw = tc8.number_input("월 인출→UPRO 이체", min_value=0, value=int(saved_cfg["t_mw"]), step=100000)

        tc9, tc10 = st.columns(2)
        t_crisis_step = tc9.number_input("TQQQ 추가투입 회차", min_value=0, max_value=20, value=int(saved_cfg["t_es"]), step=1)
        t_crisis_amt = tc10.number_input("TQQQ 위기추가금(원)", min_value=0, value=int(saved_cfg["t_ea"]), step=1000000)

        st.divider()
        st.markdown("**② 보조 엔진: UPRO (기본 U10 / 비중 30%)**")
        uc0_1, uc0_2, uc0_3 = st.columns(3)
        u_init_shares = uc0_1.number_input("UPRO 초기수량(주)", min_value=0, value=int(saved_cfg["u_sh"]), step=1)
        u_init_cash_krw = uc0_2.number_input("UPRO 원화예수금", min_value=0, value=int(saved_cfg["u_ca"]), step=500000)
        u_init_cash_usd = uc0_3.number_input("UPRO 달러예수금($)", min_value=0.0, value=float(saved_cfg["u_ca_usd"]), step=100.0, format="%.2f")

        uc1, uc2 = st.columns(2)
        u_bband = uc1.number_input("UPRO 매수 밴드(%)", value=float(saved_cfg["u_bb"]), step=1.0)
        u_sband = uc2.number_input("UPRO 매도 밴드(%)", value=float(saved_cfg["u_sb"]), step=1.0)

        uc3, uc4 = st.columns(2)
        u_bsplit = uc3.number_input("UPRO 분할 매수", min_value=1, max_value=20, value=int(saved_cfg["u_bs"]), step=1)
        u_ssplit = uc4.number_input("UPRO 분할 매도", min_value=1, max_value=20, value=int(saved_cfg["u_ss"]), step=1)

        uc5, uc6 = st.columns(2)
        u_stock_ratio = uc5.number_input("UPRO 주식 비중(%)", min_value=0, max_value=100, value=int(saved_cfg["u_sr"]), step=5)
        u_m_add = uc6.number_input("UPRO 기본 월 적립", min_value=0, value=int(saved_cfg["u_md"]), step=50000)

        uc7, uc8 = st.columns(2)
        u_crisis_step = uc7.number_input("UPRO 추가투입 회차", min_value=0, max_value=20, value=int(saved_cfg["u_es"]), step=1)
        u_crisis_amt = uc8.number_input("UPRO 위기추가금(원)", min_value=0, value=int(saved_cfg["u_ea"]), step=500000)

        save_cfg_btn = st.form_submit_button("💾 투자 설정 저장하기", use_container_width=True)
        if save_cfg_btn:
            new_cfg = {
                "sd": start_date.isoformat(),
                "fx": int(fx_rate),
                "sg": float(sgov_rate_pct),
                "rm": int(rebal_months),
                "ma": bool(use_ma200),
                "t_sh": int(t_init_shares),
                "t_ca": int(t_init_cash_krw),
                "t_ca_usd": float(t_init_cash_usd),
                "t_bb": float(t_bband),
                "t_sb": float(t_sband),
                "t_bs": int(t_bsplit),
                "t_ss": int(t_ssplit),
                "t_sr": int(t_stock_ratio),
                "t_md": int(t_m_add),
                "t_th": int(t_thresh),
                "t_mw": int(t_withdraw),
                "t_ea": int(t_crisis_amt),
                "t_es": int(t_crisis_step),
                "u_sh": int(u_init_shares),
                "u_ca": int(u_init_cash_krw),
                "u_ca_usd": float(u_init_cash_usd),
                "u_bb": float(u_bband),
                "u_sb": float(u_sband),
                "u_bs": int(u_bsplit),
                "u_ss": int(u_ssplit),
                "u_sr": int(u_stock_ratio),
                "u_md": int(u_m_add),
                "u_ea": int(u_crisis_amt),
                "u_es": int(u_crisis_step),
            }
            st.session_state.saved_cfg = new_cfg
            if st.session_state.logged_user:
                new_sha, err = save_user_to_github(
                    st.session_state.logged_user,
                    st.session_state.pin_hash,
                    new_cfg,
                    st.session_state.manual_overrides,
                    st.session_state.file_sha,
                )
                if err:
                    st.error(err)
                else:
                    st.session_state.file_sha = new_sha
                    st.success("클라우드에 설정이 저장되었습니다!")
                    st.rerun()
            else:
                st.rerun()

    if st.button("🔄 모든 설정 및 기록 초기화", use_container_width=True):
        st.session_state.saved_cfg = DEFAULT_CFG.copy()
        st.session_state.manual_overrides = {"TQQQ": {}, "UPRO": {}}
        if st.session_state.logged_user:
            new_sha, _ = save_user_to_github(
                st.session_state.logged_user,
                st.session_state.pin_hash,
                DEFAULT_CFG.copy(),
                {"TQQQ": {}, "UPRO": {}},
                st.session_state.file_sha,
            )
            st.session_state.file_sha = new_sha
        st.rerun()


# --- 파이프라인 시뮬레이션 실행 ---
t_ov = st.session_state.manual_overrides.get("TQQQ", {})
u_ov = st.session_state.manual_overrides.get("UPRO", {})

# 1) 메인 엔진: TQQQ
t_res, t_withdrawals_map, t_stat = simulate_asset_engine(
    raw_df=t_df_raw,
    start_date=start_date,
    init_shares=t_init_shares,
    init_cash_krw=t_init_cash_krw,
    init_cash_usd=t_init_cash_usd,
    buy_band_pct=t_bband,
    sell_band_pct=t_sband,
    tot_buy_steps=t_bsplit,
    tot_sell_steps=t_ssplit,
    extra_amt=t_crisis_amt,
    extra_step=t_crisis_step,
    monthly_in_map={},
    default_monthly_in=float(t_m_add),
    thresh_asset=float(t_thresh),
    monthly_out=float(t_withdraw),
    asset_stock_ratio_pct=int(t_stock_ratio),
    rebal_months=int(rebal_months),
    use_ma200=bool(use_ma200),
    sgov_rate_pct=float(sgov_rate_pct),
    fx_rate=float(fx_rate),
    manual_overrides=t_ov,
)

# 2) 보조 엔진: UPRO (TQQQ 인출금 자동 합산 유입)
u_monthly_in_map = {d: float(u_m_add) + w for d, w in t_withdrawals_map.items()}
u_res, _, u_stat = simulate_asset_engine(
    raw_df=u_df_raw,
    start_date=start_date,
    init_shares=u_init_shares,
    init_cash_krw=u_init_cash_krw,
    init_cash_usd=u_init_cash_usd,
    buy_band_pct=u_bband,
    sell_band_pct=u_sband,
    tot_buy_steps=u_bsplit,
    tot_sell_steps=u_ssplit,
    extra_amt=u_crisis_amt,
    extra_step=u_crisis_step,
    monthly_in_map=u_monthly_in_map,
    default_monthly_in=float(u_m_add),
    thresh_asset=1e15,
    monthly_out=0.0,
    asset_stock_ratio_pct=int(u_stock_ratio),
    rebal_months=int(rebal_months),
    use_ma200=bool(use_ma200),
    sgov_rate_pct=float(sgov_rate_pct),
    fx_rate=float(fx_rate),
    manual_overrides=u_ov,
)


def render_order_card(ticker: str, stat: dict, buy_band_pct: float, sell_band_pct: float, buy_steps: int, stock_ratio: int):
    buy_est_usd = stat["next_buy_p"] * stat["next_buy_sh"]
    buy_est_krw = round(buy_est_usd * fx_rate)
    sell_est_usd = stat["next_sell_p"] * stat["next_sell_sh"]
    sell_est_krw = round(sell_est_usd * fx_rate)
    buy_step_label = f"{stat['buy_step']}단계" if stat["buy_step"] <= buy_steps else "분할완료"

    st.markdown(
        f"""
        <div class="order-card">
            <div style="display:flex; justify-content:space-between; color:#495057; font-size:0.85rem; margin-bottom:2px;">
                <span><b>{ticker}</b> (목표주식비중 {stock_ratio}%)</span>
                <span>보유 <b>{stat['last_shares']:,}주</b> · 예수금 <b>{stat['last_cash']:,}원</b></span>
            </div>
            <div style="font-size:1.7rem; font-weight:800; margin-bottom:4px;">${stat['last_close']:.2f}</div>
            <div class="guide-banner">{stat['last_note']}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    c_b, c_s = st.columns(2)
    with c_b:
        st.markdown(
            f"""
            <div class="buy-box">
                <div style="font-size:0.78rem;">기준저가(${stat['ref_low']:.2f}) {buy_band_pct:+.0f}%</div>
                <div style="font-size:1.35rem; font-weight:800; margin:3px 0;">${stat['next_buy_p']:.2f}</div>
                <div style="font-weight:700; font-size:0.88rem;">🛒 매수 {stat['next_buy_sh']}주 ({buy_step_label})</div>
                <div style="font-size:0.74rem; margin-top:3px; opacity:0.85;">예상 ${buy_est_usd:,.1f} ({buy_est_krw:,}원)</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with c_s:
        st.markdown(
            f"""
            <div class="sell-box">
                <div style="font-size:0.78rem;">기준고가(${stat['ref_high']:.2f}) {sell_band_pct:+.0f}%</div>
                <div style="font-size:1.35rem; font-weight:800; margin:3px 0;">${stat['next_sell_p']:.2f}</div>
                <div style="font-weight:700; font-size:0.88rem;">🎯 매도 {stat['next_sell_sh']}주 ({stat['sell_step']}회차)</div>
                <div style="font-size:0.74rem; margin-top:3px; opacity:0.85;">예상 ${sell_est_usd:,.1f} ({sell_est_krw:,}원)</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


with tab_order:
    # 파이프라인 이체 상태 안내 배너
    t_bal = t_stat["final_bal"]
    if t_bal >= t_thresh:
        st.markdown(
            f'<div class="transfer-badge">🚀 TQQQ 자산({t_bal:,.0f}원)이 임계치({t_thresh:,.0f}원) 이상입니다. 매월 초 <b>{t_withdraw:,.0f}원</b>을 인출해 UPRO로 이체합니다.</div>',
            unsafe_allow_html=True,
        )
    else:
        remain_to_thresh = t_thresh - t_bal
        st.markdown(
            f'<div class="transfer-badge">🌱 TQQQ 자산 축적 구간 (임계치까지 <b>{remain_to_thresh:,.0f}원</b> 남음 · 월 {t_m_add:,.0f}원 적립 중)</div>',
            unsafe_allow_html=True,
        )

    render_order_card("TQQQ (메인 엔진)", t_stat, t_bband, t_sband, t_bsplit, t_stock_ratio)
    st.markdown("<div style='height:14px;'></div>", unsafe_allow_html=True)
    render_order_card("UPRO (보조 엔진)", u_stat, u_bband, u_sband, u_bsplit, u_stock_ratio)

    st.divider()
    st.subheader("✏️ 실전 체결 잔고 반영")
    st.caption(f"종목별로 체결 후 수량과 원화·달러 예수금을 각각 입력하면 자동 합산(환율 {fx_rate:,}원)되어 저장됩니다.")

    with st.form("manual_trade_form"):
        fc0, fc1 = st.columns(2)
        target_ticker = fc0.selectbox("종목 선택", ["TQQQ", "UPRO"])
        m_date = fc1.date_input("체결 기준일", value=datetime.date.fromisoformat(latest_db_date))

        cur_stat = t_stat if target_ticker == "TQQQ" else u_stat
        cur_ov_dict = t_ov if target_ticker == "TQQQ" else u_ov

        def_sh = cur_stat["last_shares"]
        def_krw = cur_stat["last_cash"]
        def_usd = 0.0
        if cur_ov_dict:
            last_k = max(cur_ov_dict.keys())
            if last_k == latest_db_date:
                def_krw = int(cur_ov_dict[last_k].get("cash_krw", 0))
                def_usd = float(cur_ov_dict[last_k].get("cash_usd", 0.0))

        mc1, mc2, mc3 = st.columns(3)
        m_shares = mc1.number_input("실제 수량(주)", min_value=0, value=int(def_sh), step=1)
        m_cash_krw = mc2.number_input("원화 예수금(원)", min_value=0, value=int(def_krw), step=10000)
        m_cash_usd = mc3.number_input("달러 예수금($)", min_value=0.0, value=float(def_usd), step=10.0, format="%.2f")

        submitted = st.form_submit_button("💾 선택한 종목 잔고 적용하기", use_container_width=True)
        if submitted:
            d_key = m_date.strftime("%Y-%m-%d")
            st.session_state.manual_overrides.setdefault(target_ticker, {})[d_key] = {
                "shares": int(m_shares),
                "cash_krw": int(m_cash_krw),
                "cash_usd": float(m_cash_usd),
            }
            if st.session_state.logged_user:
                new_sha, err = save_user_to_github(
                    st.session_state.logged_user,
                    st.session_state.pin_hash,
                    st.session_state.saved_cfg,
                    st.session_state.manual_overrides,
                    st.session_state.file_sha,
                )
                if err:
                    st.error(err)
                else:
                    st.session_state.file_sha = new_sha
                    st.rerun()
            else:
                st.rerun()

    if t_ov or u_ov:
        if st.button("🗑️ 수동 입력 기록 전체 초기화", use_container_width=True):
            st.session_state.manual_overrides = {"TQQQ": {}, "UPRO": {}}
            if st.session_state.logged_user:
                new_sha, _ = save_user_to_github(
                    st.session_state.logged_user,
                    st.session_state.pin_hash,
                    st.session_state.saved_cfg,
                    {"TQQQ": {}, "UPRO": {}},
                    st.session_state.file_sha,
                )
                st.session_state.file_sha = new_sha
            st.rerun()

with tab_stats:
    tot_df = pd.DataFrame(
        {
            "날짜": t_res["날짜"],
            "TQQQ 잔고": t_res["계좌잔고(원)"],
            "UPRO 잔고": u_res["계좌잔고(원)"],
            "통합 순자산": t_res["계좌잔고(원)"] + u_res["계좌잔고(원)"],
        }
    ).set_index("날짜")

    tot_df["전고점"] = tot_df["통합 순자산"].cummax()
    tot_df["MDD(%)"] = np.where(
        tot_df["전고점"] > 0,
        ((tot_df["통합 순자산"] - tot_df["전고점"]) / tot_df["전고점"]) * 100.0,
        0.0,
    )

    # 순수 외부 투입 원금 (TQQQ->UPRO 내부 이체액은 중복 제외)
    ext_principal = (
        t_stat["principal"]
        + u_stat["principal"]
        - t_stat["withdrawn"]
    )
    final_combined = float(tot_df["통합 순자산"].iloc[-1])
    total_ret = ((final_combined - ext_principal) / ext_principal * 100.0) if ext_principal > 0 else 0.0
    days = (pd.to_datetime(tot_df.index[-1]) - pd.to_datetime(tot_df.index[0])).days
    cagr = ((final_combined / ext_principal) ** (365.25 / days) - 1.0) * 100.0 if (days > 0 and ext_principal > 0) else 0.0
    mdd = float(tot_df["MDD(%)"].min())

    transfer_dates = [d[:7] for d, w in t_withdrawals_map.items() if w > 0]
    first_transfer = transfer_dates[0] if transfer_dates else "미도달"

    m1, m2 = st.columns(2)
    m1.metric("통합 순자산", f"{final_combined:,.0f}원", f"{total_ret:+.2f}% (순수원금 {ext_principal:,.0f}원)")
    m2.metric("통합 연복리 (CAGR)", f"{cagr:.2f}%", f"MDD {mdd:.2f}%", delta_color="inverse")

    m3, m4 = st.columns(2)
    m3.metric("파이프라인 개시", first_transfer, f"T→U 누적 이체 {t_stat['withdrawn']:,.0f}원")
    t_share_pct = (t_stat["final_bal"] / final_combined * 100.0) if final_combined > 0 else 0.0
    u_share_pct = (u_stat["final_bal"] / final_combined * 100.0) if final_combined > 0 else 0.0
    m4.metric(
        "계좌별 잔고 비중",
        f"T {t_stat['final_bal']/10000:,.0f}만 / U {u_stat['final_bal']/10000:,.0f}만",
        f"{t_share_pct:.0f}% : {u_share_pct:.0f}%",
    )

    st.area_chart(tot_df[["TQQQ 잔고", "UPRO 잔고"]])

    sub_t, sub_u = st.tabs(["TQQQ 매매 내역", "UPRO 매매 내역"])
    with sub_t:
        st.dataframe(t_res.iloc[::-1].head(30), use_container_width=True, hide_index=True)
    with sub_u:
        st.dataframe(u_res.iloc[::-1].head(30), use_container_width=True, hide_index=True)
