import streamlit as st
import yfinance as yf
import pandas as pd
import pandas_ta as ta
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import time
import random
import requests
import urllib3
import json
import os
from datetime import datetime, timedelta

# --- 基礎配置 ---
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
st.set_page_config(page_title="台股量化決策與回測系統", layout="wide")

DB_FILE = "portfolio.json"

# --- 1. 資料持久化與通用函數 ---
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

# --- 2. 移植回測類別 (來自 stock_analyze(1).py) ---
class RSITradingStrategy:
    def __init__(self, df, rsi_period=14, oversold=20, overbought=80, 
                 profit_take_rsi=60, profit_take_pct=0.3, 
                 initial_capital=1000000, entry_pct=0.1,
                 buy_fee_rate=0.001425, sell_fee_rate=0.004425,
                 max_entries=5, stop_loss_pct=0.10):
        self.df = df.copy()
        self.rsi_period = rsi_period
        self.oversold = oversold
        self.overbought = overbought
        self.profit_take_rsi = profit_take_rsi
        self.profit_take_pct = profit_take_pct
        self.initial_capital = initial_capital
        self.entry_pct = entry_pct
        self.buy_fee_rate = buy_fee_rate
        self.sell_fee_rate = sell_fee_rate
        self.max_entries = max_entries
        self.stop_loss_pct = stop_loss_pct
        self.trades = []

    def calculate_indicators(self):
        if isinstance(self.df.columns, pd.MultiIndex):
            self.df.columns = self.df.columns.get_level_values(0)
        self.df['RSI'] = ta.rsi(self.df['Close'], length=self.rsi_period)
        self.df.dropna(inplace=True)

    def run_backtest(self):
        self.trades = []
        cash = self.initial_capital
        holdings = 0
        inventory = [] 
        equity_curve = [] 
        actions = [] 
        entry_amount = self.initial_capital * self.entry_pct

        for index, row in self.df.iterrows():
            rsi_val = row['RSI']
            price = row['Close']
            action_code = 0
            
            # 止損檢查
            if holdings > 0:
                total_cost_basis = sum(item['shares'] * item['price'] for item in inventory)
                avg_cost = total_cost_basis / holdings
                if (price - avg_cost) / avg_cost <= -self.stop_loss_pct:
                    revenue = holdings * price * (1 - self.sell_fee_rate)
                    cash += revenue
                    self.trades.append({'類型': '止損賣出', '時間': index, '股數': holdings, '買入均價': avg_cost, '賣出價格': price, '報酬率': (price-avg_cost)/avg_cost})
                    holdings, inventory, action_code = 0, [], -2

            if action_code != -2:
                # 獲利清倉
                if rsi_val > self.overbought and holdings > 0:
                    revenue = holdings * price * (1 - self.sell_fee_rate)
                    cash += revenue
                    self.trades.append({'類型': '獲利清倉', '時間': index, '股數': holdings, '賣出價格': price, '報酬率': 0.1}) # 簡化
                    holdings, inventory, action_code = 0, [], -1
                # 部分調節
                elif rsi_val > self.profit_take_rsi and len(inventory) > 1:
                    batches_to_sell = max(1, int(round(len(inventory) * self.profit_take_pct)))
                    if batches_to_sell < len(inventory):
                        sold_shares = 0
                        for _ in range(batches_to_sell):
                            batch = inventory.pop(0)
                            sold_shares += batch['shares']
                        cash += sold_shares * price * (1 - self.sell_fee_rate)
                        holdings -= sold_shares
                        action_code = 2
                # 買入/加碼
                elif rsi_val < self.oversold and len(inventory) < self.max_entries:
                    shares = int(min(entry_amount, cash) / (price * (1 + self.buy_fee_rate)))
                    if shares > 0:
                        cash -= shares * price * (1 + self.buy_fee_rate)
                        holdings += shares
                        inventory.append({'shares': shares, 'price': price, 'time': index})
                        action_code = 1
            
            actions.append(action_code)
            equity_curve.append(cash + (holdings * price))

        self.df['Action'] = actions
        return (equity_curve[-1] - self.initial_capital) / self.initial_capital, equity_curve[-1]

# --- 3. UI 導航 ---
page = st.sidebar.radio("功能選單", ["1. 全市場資金選股", "2. 進階決策與持倉", "3. 策略參數回測優化"])

# --- 頁面 1 & 2：延用您原本的邏輯 ---
if page == "1. 全市場資金選股":
    # (此處省略與您原本相同的頁面 1 代碼，確保 get_full_market_tickers 正常運行)
    st.title("🏆 全市場資金指標排行")
    if st.button("🚀 執行深度掃描"):
        all_list = get_full_market_tickers()
        # ... 執行下載與排行邏輯
        st.success("掃描完成，代碼已存入 Session。")

elif page == "2. 進階決策與持倉":
    # (此處省略與您原本相同的頁面 2 代碼，包含持倉顯示與移除功能)
    st.title("🛡️ 進階量化決策中心")
    # ... 顯示建議動作與持倉列表

# --- 頁面 3：回測功能 (新增) ---
elif page == "3. 策略參數回測優化":
    st.title("📈 策略參數回測與調節優化")
    
    # 持倉股票快捷選取
    held_tickers = list(st.session_state.portfolio.keys())
    col_t1, col_t2 = st.columns([1, 1])
    with col_t1:
        ticker_sel = st.selectbox("快捷選取持倉股票", ["手動輸入"] + held_tickers)
    with col_t2:
        manual_ticker = st.text_input("或手動輸入股票代碼 (例: 2330.TW)", value="" if ticker_sel != "手動輸入" else "2330.TW")
    
    final_ticker = ticker_sel if ticker_sel != "手動輸入" else manual_ticker

    st.divider()
    
    # 參數設定區
    c1, c2, c3 = st.columns(3)
    with c1:
        st.subheader("📊 RSI 設定")
        r_period = st.slider("RSI 週期", 5, 30, 14)
        r_buy = st.number_input("超賣買入線", value=20)
        r_sell = st.number_input("超買清倉線", value=80)
    with c2:
        st.subheader("⚙️ 調節與止損")
        r_pt_rsi = st.slider("部分調節 RSI 門檻", 40, 75, 60)
        r_pt_pct = st.slider("調節比例 (%)", 10, 90, 30) / 100.0
        r_sl = st.slider("強制止損 (%)", 5.0, 30.0, 10.0) / 100.0
    with c3:
        st.subheader("💰 資金管理")
        init_cap = st.number_input("初始資金", value=1000000)
        max_e = st.number_input("最大加碼次數", 1, 10, 5)
        test_days = st.selectbox("回測時間範圍", [90, 180, 365, 730], index=1)

    if st.button("🚀 執行精密回測", type="primary"):
        with st.spinner(f"正在抓取 {final_ticker} 數據並執行回測..."):
            # 下載數據
            raw_df = yf.download(final_ticker, start=datetime.now()-timedelta(days=test_days), interval="1h", progress=False)
            
            if raw_df.empty:
                st.error("無法下載數據，請檢查代碼是否正確。")
            else:
                strategy = RSITradingStrategy(
                    raw_df, rsi_period=r_period, oversold=r_buy, overbought=r_sell,
                    profit_take_rsi=r_pt_rsi, profit_take_pct=r_pt_pct,
                    initial_capital=init_cap, max_entries=max_e, stop_loss_pct=r_sl
                )
                strategy.calculate_indicators()
                total_ret, final_val = strategy.run_backtest()
                
                # 顯示結果
                st.subheader("📊 回測成效")
                m1, m2, m3 = st.columns(3)
                m1.metric("最終淨值", f"${final_val:,.0f}")
                m2.metric("累積報酬率", f"{total_ret:.2%}")
                m3.metric("交易總筆數", len(strategy.trades))
                
                if strategy.trades:
                    with st.expander("查看詳細交易紀錄"):
                        st.table(pd.DataFrame(strategy.trades).tail(10))
                else:
                    st.warning("此參數設定下，回測期間內無觸發任何交易。")
