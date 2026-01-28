import streamlit as st
import yfinance as yf
import pandas as pd
import pandas_ta as ta
import numpy as np
import time
import random
import requests
import urllib3
import json
import os
from datetime import datetime, timedelta
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# --- 基礎配置 ---
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
st.set_page_config(page_title="台股量化決策與回測系統", layout="wide")

DB_FILE = "portfolio.json"

# --- 核心函數：資料持久化 ---
def load_portfolio():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r") as f: return json.load(f)
        except: return {}
    return {}

def save_portfolio(data):
    with open(DB_FILE, "w") as f: json.dump(data, f, indent=4)

# 初始化 Session State
if 'portfolio' not in st.session_state:
    st.session_state.portfolio = load_portfolio()
if 'top_100_df' not in st.session_state:
    st.session_state.top_100_df = pd.DataFrame()

# --- 核心函數：全市場掃描 ---
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

# --- 導航選單 ---
page = st.sidebar.radio("功能選單", ["1. 全市場資金選股", "2. 進階決策與持倉", "3. 策略參數自動優化"])

# --- 頁面 1：全市場掃描 ---
if page == "1. 全市場資金選股":
    st.title("🏆 全市場資金指標排行")
    if st.button("🚀 啟動深度掃描"):
        tickers = get_full_market_tickers()
        res_rank = []
        p_bar = st.progress(0, text="正在分批獲取數據...")
        
        batch_size = 50 
        for i in range(0, len(tickers), batch_size):
            batch = tickers[i : i + batch_size]
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
            p_bar.progress(min((i + batch_size) / len(tickers), 1.0))
            time.sleep(random.uniform(0.1, 0.3))
        
        if res_rank:
            st.session_state.top_100_df = pd.DataFrame(res_rank).sort_values("成交值(億)", ascending=False).head(100)
            st.success("✅ 掃描完成！請前往第二頁查看決策建議。")
    
    if not st.session_state.top_100_df.empty:
        st.dataframe(st.session_state.top_100_df, use_container_width=True)

# --- 頁面 2：多因子決策 ---
elif page == "2. 進階決策與持倉":
    st.title("🛡️ 進階量化決策中心")
    if st.session_state.top_100_df.empty:
        st.warning("⚠️ 請先在第一頁執行全市場掃描。")
    else:
        # 權重設定
        st.sidebar.header("⚙️ 因子權重分配")
        w_rsi = st.sidebar.slider("RSI 超賣權重", 0, 100, 40)
        w_ma = st.sidebar.slider("MA 金叉權重", 0, 100, 30)
        buy_threshold = st.sidebar.slider("建議買入門檻", 10, 100, 30)

        results = []
        p_check = st.progress(0, text="計算因子得分中...")
        tickers_to_check = st.session_state.top_100_df['股票代號'].tolist()
        
        for idx, t in enumerate(tickers_to_check):
            try:
                df = yf.download(t, period="60d", progress=False, auto_adjust=True)
                if not df.empty:
                    df['RSI'] = ta.rsi(df['Close'], length=14)
                    curr = df.iloc[-1]
                    score = 0
                    if curr['RSI'] < 30: score += w_rsi
                    # ... 這裡可依據 stock_analyze.py 加入更多因子
                    results.append({"代號": t, "總分": score, "RSI": round(curr['RSI'], 1), "現價": round(curr['Close'], 2)})
            except: pass
            p_check.progress((idx + 1) / len(tickers_to_check))
        
        df_results = pd.DataFrame(results).sort_values("總分", ascending=False)
        st.dataframe(df_results, use_container_width=True)

# --- 頁面 3：回測優化 (整合 stock_analyze(1).py) ---
elif page == "3. 策略參數自動優化":
    st.title("🧪 策略參數測試與最大化")
    
    # 從持倉中選取標的或手動輸入
    target_ticker = st.text_input("輸入要優化的股票代號 (例: 2330.TW)", value="2330.TW")
    
    if st.button("🔥 執行參數網格搜索"):
        df_hist = yf.download(target_ticker, period="1y", interval="1d", progress=False)
        if not df_hist.empty:
            # 簡化版回測迴圈：尋找最優 RSI 週期與止損
            opt_res = []
            for r_period in [7, 14, 21]:
                for sl in [0.05, 0.10, 0.15]:
                    # 這裡調用您 stock_analyze(1).py 的 RSITradingStrategy 邏輯
                    # 範例僅展示結構
                    ret = random.uniform(-0.2, 0.5) # 模擬結果
                    opt_res.append({"RSI週期": r_period, "止損%": sl*100, "報酬率": ret})
            
            st.table(pd.DataFrame(opt_res).sort_values("報酬率", ascending=False))
