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

# --- 基礎配置 ---
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
st.set_page_config(page_title="台股多因子決策與回測優化系統", layout="wide")

DB_FILE = "portfolio.json"

# --- 1. 資料管理與核心函數 ---
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

@st.cache_data(ttl=86400)
def get_full_market_tickers():
    """全面獲取台股代碼"""
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

# --- 2. 核心回測引擎 (整合自 stock_analyze(1).py) ---
def run_backtest_engine(df, params):
    """執行真實資金回測邏輯"""
    cash = params['initial_capital']
    holdings = 0
    inventory = []
    equity_curve = []
    
    df = df.copy()
    df['RSI'] = ta.rsi(df['Close'], length=params['rsi_period'])
    df.dropna(inplace=True)
    
    for _, row in df.iterrows():
        rsi_val = row['RSI']
        price = row['Close']
        
        # 1. 檢查止損
        if holdings > 0:
            avg_cost = sum(item['shares'] * item['price'] for item in inventory) / holdings
            if (price - avg_cost) / avg_cost <= -params['stop_loss_pct']:
                cash += holdings * price * (1 - 0.004425)
                holdings = 0
                inventory = []

        # 2. 獲利清倉/調節
        if holdings > 0:
            if rsi_val > params['overbought_rsi']:
                cash += holdings * price * (1 - 0.004425)
                holdings = 0
                inventory = []
            elif rsi_val > params['profit_take_rsi'] and len(inventory) > 1:
                shares_to_sell = inventory.pop(0)['shares']
                cash += shares_to_sell * price * (1 - 0.004425)
                holdings -= shares_to_sell

        # 3. 買入/加碼
        if rsi_val < params['oversold_rsi'] and len(inventory) < params['max_entries']:
            entry_cash = params['initial_capital'] * params['entry_pct']
            shares_to_buy = int(min(entry_cash, cash) / (price * (1 + 0.001425)))
            if shares_to_buy > 0:
                cash -= shares_to_buy * price * (1 + 0.001425)
                holdings += shares_to_buy
                inventory.append({'shares': shares_to_buy, 'price': price})
        
        equity_curve.append(cash + (holdings * price))
    
    return ((equity_curve[-1] - params['initial_capital']) / params['initial_capital']) if equity_curve else -1

# --- 3. UI 導航 ---
page = st.sidebar.radio("功能選單", ["1. 全市場資金選股", "2. 進階決策與持倉", "3. 策略參數自動優化"])

# --- 頁面 1 & 2 保持原有邏輯 (批次處理與錯誤跳過) ---
if page == "1. 全市場資金選股":
    st.title("🏆 全市場資金指標排行")
    if st.button("🚀 啟動深度掃描"):
        all_list = get_full_market_tickers()
        res_rank = []
        p_bar = st.progress(0, text="分批下載數據中...")
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
            st.session_state.top_100_list = pd.DataFrame(res_rank).sort_values("成交值(億)", ascending=False).head(100)['股票代號'].tolist()
            st.success("✅ 掃描完成！")

elif page == "2. 進階決策與持倉":
    st.title("🛡️ 進階量化決策中心")
    # 此處邏輯同前次回答，整合 RSI 加碼與止損判定
    # (代碼略，確保整合 analyze_stock_advanced 函數)

# --- 4. 頁面 3：策略參數自動優化 (新增功能) ---
elif page == "3. 策略參數自動優化":
    st.title("🧪 策略參數最大化測試")
    st.markdown("針對您**持倉中**的標的，自動回測數百種參數組合，找出「總報酬率」最高的設定。")
    
    held_tickers = [k for k, v in st.session_state.portfolio.items() if v]
    
    if not held_tickers:
        st.warning("⚠️ 目前尚無持倉股票，請先在第二頁加入持倉。")
    else:
        target_t = st.selectbox("選擇要優化的持倉標的", held_tickers)
        
        col1, col2 = st.columns(2)
        with col1:
            test_days = st.slider("回測天數", 60, 365, 180)
            initial_cap = st.number_input("模擬初始資金", value=1000000)
        with col2:
            st.write("🏃 優化範圍設定 (網格搜索)")
            rsi_range = st.multiselect("RSI 週期測試範圍", [7, 10, 14, 21], default=[7, 14])
            stop_loss_range = st.multiselect("止損百分比測試範圍 (%)", [5, 10, 15], default=[10])

        if st.button("🔥 開始暴力破解最優參數"):
            # 下載歷史數據
            df_hist = yf.download(target_t, start=datetime.now()-timedelta(days=test_days), interval="1h", progress=False)
            if df_hist.empty:
                st.error("無法取得該標的之 1 小時線數據")
            else:
                optimization_results = []
                # 簡單的網格搜索
                total_combinations = len(rsi_range) * len(stop_loss_range) * 3 * 3
                curr_comb = 0
                p_opt = st.progress(0)
                
                for r in rsi_range:
                    for sl in stop_loss_range:
                        for buy_r in [20, 25, 30]: # 超賣買入線
                            for sell_r in [70, 75, 80]: # 超買賣出線
                                params = {
                                    'rsi_period': r, 'stop_loss_pct': sl/100, 
                                    'oversold_rsi': buy_r, 'overbought_rsi': sell_r,
                                    'profit_take_rsi': sell_r - 10, 'max_entries': 5,
                                    'initial_capital': initial_cap, 'entry_pct': 0.1
                                }
                                ret = run_backtest_engine(df_hist, params)
                                optimization_results.append({
                                    "RSI週期": r, "止損%": sl, "買入RSI": buy_r, 
                                    "賣出RSI": sell_r, "總報酬率": f"{ret:.2%}", "raw_ret": ret
                                })
                                curr_comb += 1
                                p_opt.progress(curr_comb / total_combinations)
                
                res_df = pd.DataFrame(optimization_results).sort_values("raw_ret", ascending=False)
                st.subheader(f"🏆 {target_t} 最佳參數組合排行")
                st.dataframe(res_df.drop(columns=['raw_ret']).head(10), use_container_width=True)
                
                best = res_df.iloc[0]
                st.success(f"🚩 建議設定：RSI週期 {best['RSI週期']}, 買入線 {best['買入RSI']}, 賣出線 {best['賣出RSI']}。")
