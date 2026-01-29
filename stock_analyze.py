import streamlit as st
import yfinance as yf
import pandas as pd
import pandas_ta as ta
import time
import random
import requests
import urllib3
import json
import os
from datetime import datetime

# --- 基礎配置 ---
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
st.set_page_config(page_title="台股多因子決策系統 (加碼止損版)", layout="wide")

DB_FILE = "portfolio.json"

# 持倉管理
def load_portfolio():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r") as f: return json.load(f)
        except: return {}
    return {}

def save_portfolio(data):
    with open(DB_FILE, "w") as f: json.dump(data, f, indent=4)

if 'portfolio' not in st.session_state:
    st.session_state.portfolio = load_portfolio()

# --- 1. 全面獲取股票代碼 (全面模式) ---
@st.cache_data(ttl=86400)
def get_full_market_tickers():
    url = "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2"
    try:
        res = requests.get(url, timeout=10, verify=False, headers={'User-Agent': 'Mozilla/5.0'})
        res.encoding = 'big5'
        df = pd.read_html(res.text)[0]
        df.columns = df.iloc[0]
        df = df[df['有價證券代號及名稱'].str.contains("  ", na=False)]
        tickers = [f"{t.split('  ')[0].strip()}.TW" for t in df['有價證券代號及名稱'] if len(t.split('  ')[0].strip()) == 4]
        if len(tickers) > 800: return tickers
    except: pass
    return [f"{i:04d}.TW" for i in range(1101, 9999)]

# --- 2. 交易決策邏輯 (整合回測標準) ---
def analyze_stock_advanced(ticker, weights, params):
    try:
        df = yf.download(ticker, period="60d", interval="1d", progress=False, auto_adjust=True)
        if df.empty or len(df) < 20: return None
        if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)

        df['RSI'] = ta.rsi(df['Close'], length=14)
        df['MA5'] = ta.sma(df['Close'], length=5)
        df['MA10'] = ta.sma(df['Close'], length=10)

        curr, prev = df.iloc[-1], df.iloc[-2]
        c_price = float(curr['Close'])
        c_rsi = float(curr['RSI'])
        
        # 評分邏輯
        score = 0
        if c_rsi < 30: score += weights['rsi']
        if float(prev['MA5']) < float(prev['MA10']) and float(curr['MA5']) > float(curr['MA10']): score += weights['ma']
        chg = ((c_price - float(prev['Close'])) / float(prev['Close'])) * 100
        if abs(chg) >= 7.0: score += weights['vol']
        if float(curr['Volume']) > df['Volume'].mean() * 2: score += weights['vxx']

        # 動作判定 (結合持倉與回測參數)
        holdings = st.session_state.portfolio.get(ticker, [])
        action = "觀望"
        
        if holdings:
            avg_cost = sum([h['price'] for h in holdings]) / len(holdings)
            roi = (c_price - avg_cost) / avg_cost
            
            # 止損判定
            if roi <= -params['stop_loss_pct']: action = "🚨 止損賣出"
            # RSI 獲利調節
            elif c_rsi > params['profit_take_rsi']: action = "🟠 部分調節"
            # RSI 全清倉
            elif c_rsi > params['overbought_rsi']: action = "🔵 獲利清倉"
        
        # 買入/加碼判定 (檢查最大加碼次數)
        if action == "觀望" and score >= params['buy_threshold']:
            if len(holdings) < params['max_entries']:
                action = "🟢 建議加碼" if len(holdings) > 0 else "🟢 建議買入"

        return {
            "代碼": ticker, "總分": score, "現價": round(c_price, 2),
            "RSI": round(c_rsi, 1), "建議動作": action, "持倉批數": len(holdings)
        }
    except: return None

# --- UI 導航 ---
page = st.sidebar.radio("功能選單", ["1. 全市場資金選股", "2. 進階決策與持倉"])

# 參數設定
st.sidebar.divider()
st.sidebar.header("⚙️ 交易策略參數")
max_e = st.sidebar.number_input("最大加碼次數", 1, 10, 5)
sl_pct = st.sidebar.slider("止損百分比 (%)", 5.0, 30.0, 10.0) / 100.0
pt_rsi = st.sidebar.slider("部分調節 RSI", 40, 70, 60)
ob_rsi = st.sidebar.slider("獲利清倉 RSI", 70, 95, 80)

# --- 頁面 1：選股 ---
if page == "1. 全市場資金選股":
    st.title("🏆 全市場資金指標排行")
    if st.button("🚀 執行深度掃描"):
        all_list = get_full_market_tickers()
        res_rank = []
        p_bar = st.progress(0, text="分批下載中...")
        
        batch_size = 50
        for i in range(0, len(all_list), batch_size):
            batch = all_list[i : i + batch_size]
            try:
                data = yf.download(batch, period="2d", group_by='ticker', threads=True, progress=False)
                for t in batch:
                    try:
                        t_df = data[t].dropna() if isinstance(data.columns, pd.MultiIndex) else data.dropna()
                        if not t_df.empty:
                            last = t_df.iloc[-1]
                            val = (float(last['Close']) * float(last['Volume'])) / 1e8
                            res_rank.append({"股票代號": t, "收盤價": float(last['Close']), "成交值(億)": val})
                    except: continue
            except: pass
            p_bar.progress(min((i + batch_size) / len(all_list), 1.0))
            time.sleep(random.uniform(0.5, 1.0))
        
        if res_rank:
            top_100 = pd.DataFrame(res_rank).sort_values("成交值(億)", ascending=False).head(100)
            st.session_state.top_100_list = top_100['股票代號'].tolist()
            st.dataframe(top_100, use_container_width=True)

# --- 頁面 2：決策 ---
elif page == "2. 進階決策與持倉":
    st.title("🛡️ 進階量化決策中心")
    if 'top_100_list' not in st.session_state:
        st.warning("請先執行第一頁掃描。")
    else:
        weights = {'rsi': 40, 'ma': 30, 'vol': 20, 'vxx': 10}
        params = {
            'max_entries': max_e, 'stop_loss_pct': sl_pct,
            'profit_take_rsi': pt_rsi, 'overbought_rsi': ob_rsi, 'buy_threshold': 30
        }
        
        signals = []
        p_check = st.progress(0, text="計算指標中...")
        for idx, t in enumerate(st.session_state.top_100_list):
            res = analyze_stock_advanced(t, weights, params)
            if res: signals.append(res)
            p_check.progress((idx + 1) / 100)
        
        if signals:
            st.dataframe(pd.DataFrame(signals).sort_values("總分", ascending=False), use_container_width=True)
            
            # 手動記錄買入
            st.divider()
            c1, c2 = st.columns(2)
            with c1: t_in = st.selectbox("選股代號", [s['代碼'] for s in signals])
            with c2: p_in = st.number_input("買入價格", value=0.0)
            if st.button("➕ 更新持倉"):
                if t_in not in st.session_state.portfolio: st.session_state.portfolio[t_in] = []
                st.session_state.portfolio[t_in].append({"price": p_in, "date": str(datetime.now().date())})
                save_portfolio(st.session_state.portfolio)
                st.rerun()

    # --- 持倉顯示 ---
    st.subheader("💼 我的持倉紀錄")
    p_summary = []
    for k, v in st.session_state.portfolio.items():
        if v:
            avg = sum([i['price'] for i in v])/len(v)
            p_summary.append({"代號": k, "持倉批數": len(v), "平均成本": round(avg, 2)})
    if p_summary:
        st.table(pd.DataFrame(p_summary))
        t_del = st.selectbox("移除標的", [d['代號'] for d in p_summary])
        if st.button("🗑️ 移除"):
            st.session_state.portfolio[t_del] = []
            save_portfolio(st.session_state.portfolio)
            st.rerun()
