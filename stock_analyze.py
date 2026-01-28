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

# --- 1. 回測核心類別 ---
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
        
        for index, row in self.df.iterrows():
            rsi_val = row['RSI']
            price = row['Close']
            action_code = 0 # 0:無, 1:買, 2:調節, -1:清倉, -2:止損
            
            # 止損邏輯
            if holdings > 0:
                avg_cost = sum(item['shares'] * item['price'] for item in inventory) / holdings
                if (price - avg_cost) / avg_cost <= -self.stop_loss_pct:
                    cash += holdings * price * (1 - self.sell_fee_rate)
                    self.trades.append({'類型': '止損賣出', '時間': index, '報酬率': (price-avg_cost)/avg_cost})
                    holdings, inventory, action_code = 0, [], -2

            if action_code != -2:
                # 清倉/調節/買入
                if rsi_val > self.overbought and holdings > 0:
                    cash += holdings * price * (1 - self.sell_fee_rate)
                    holdings, inventory, action_code = 0, [], -1
                elif rsi_val > self.profit_take_rsi and len(inventory) > 1:
                    batches = max(1, int(len(inventory) * self.profit_take_pct))
                    for _ in range(batches):
                        if inventory:
                            batch = inventory.pop(0)
                            cash += batch['shares'] * price * (1 - self.sell_fee_rate)
                            holdings -= batch['shares']
                    action_code = 2
                elif rsi_val < self.oversold and len(inventory) < self.max_entries:
                    buy_val = self.initial_capital * self.entry_pct
                    shares = int(min(buy_val, cash) / (price * (1 + self.buy_fee_rate)))
                    if shares > 0:
                        cash -= shares * price * (1 + self.buy_fee_rate)
                        holdings += shares
                        inventory.append({'shares': shares, 'price': price, 'time': index})
                        action_code = 1
            
            actions.append(action_code)
            equity_curve.append(cash + (holdings * price))

        self.df['Action'] = actions
        final_ret = (equity_curve[-1] - self.initial_capital) / self.initial_capital if equity_curve else 0
        return final_ret, equity_curve[-1]

# --- 2. 核心功能與資料管理 ---
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
if 'top_100_list' not in st.session_state:
    st.session_state.top_100_list = []

@st.cache_data(ttl=86400)
def get_full_market_tickers():
    url = "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2"
    try:
        res = requests.get(url, timeout=10, verify=False, headers={'User-Agent': 'Mozilla/5.0'})
        res.encoding = 'big5'
        df = pd.read_html(res.text)[0]
        df.columns = df.iloc[0]
        df = df[df['有價證券代號及名稱'].str.contains("  ", na=False)]
        return [f"{t.split('  ')[0].strip()}.TW" for t in df['有價證券代號及名稱'] if len(t.split('  ')[0].strip()) == 4]
    except:
        return [f"{i:04d}.TW" for i in range(1101, 9999)]

# --- 3. UI 頁面導航 ---
page = st.sidebar.radio("功能選單", ["1. 全市場資金選股", "2. 進階決策與持倉", "3. 策略參數回測優化"])

if page == "1. 全市場資金選股":
    st.title("🏆 全市場資金指標排行")
    if st.button("🚀 執行深度掃描"):
        all_list = get_full_market_tickers()
        res_rank = []
        p_bar = st.progress(0, text="正在獲取數據（關閉多線程模式以確保穩定）...")
        
        # 修正：縮小批次，關閉 threads
        batch_size = 20
        for i in range(0, 200, batch_size): # 測試先取前200隻，可改回 len(all_list)
            batch = all_list[i : i + batch_size]
            try:
                # 關鍵修正點：threads=False
                data = yf.download(batch, period="2d", group_by='ticker', threads=False, progress=False)
                for t in batch:
                    try:
                        t_df = data[t].dropna() if isinstance(data, pd.DataFrame) and len(batch)>1 else data.dropna()
                        if not t_df.empty:
                            last = t_df.iloc[-1]
                            val = (float(last['Close']) * float(last['Volume'])) / 1e8
                            res_rank.append({"股票代號": t, "收盤價": float(last['Close']), "成交值(億)": val})
                    except: continue
            except: pass
            p_bar.progress((i + batch_size) / 200)
            time.sleep(0.5)
        
        if res_rank:
            top_df = pd.DataFrame(res_rank).sort_values("成交值(億)", ascending=False).head(100)
            st.session_state.top_100_list = top_df['股票代號'].tolist()
            st.dataframe(top_df, use_container_width=True)

elif page == "2. 進階決策與持倉":
    st.title("🛡️ 進階量化決策中心")
    if not st.session_state.top_100_list:
        st.warning("請先執行第一頁掃描。")
    else:
        results = []
        for t in st.session_state.top_100_list[:20]: # 示範前20名
            df = yf.download(t, period="60d", progress=False)
            if not df.empty:
                df['RSI'] = ta.rsi(df['Close'], length=14)
                curr = df.iloc[-1]
                results.append({"代碼": t, "現價": round(curr['Close'],2), "RSI": round(curr['RSI'],1)})
        st.table(pd.DataFrame(results))

elif page == "3. 策略參數回測優化":
    st.title("📈 策略參數回測與調節優化")
    
    # 圖片上要求的介面功能
    col_t1, col_t2 = st.columns(2)
    held_tickers = list(st.session_state.portfolio.keys())
    with col_t1:
        ticker_sel = st.selectbox("快捷選取持倉股票", ["手動輸入"] + held_tickers)
    with col_t2:
        manual_ticker = st.text_input("或手動輸入股票代碼", value="2330.TW" if ticker_sel == "手動輸入" else ticker_sel)
    
    st.divider()
    c1, c2, c3 = st.columns(3)
    with c1:
        st.subheader("📊 RSI 設定")
        r_period = st.slider("RSI 週期", 5, 30, 14)
        r_buy = st.number_input("超賣買入線", value=20)
        r_sell = st.number_input("超買清倉線", value=80)
    with c2:
        st.subheader("⚙️ 調節與止損")
        r_pt_rsi = st.slider("部分調節 RSI", 40, 75, 60)
        r_pt_pct = st.slider("調節比例(%)", 10, 90, 30) / 100
        r_sl = st.slider("止損(%)", 5.0, 30.0, 10.0) / 100
    with c3:
        st.subheader("💰 資金管理")
        init_cap = st.number_input("初始資金", value=1000000)
        max_e = st.number_input("最大加碼次數", 1, 10, 5)

    if st.button("🚀 執行精密回測"):
        df_hist = yf.download(manual_ticker, period="1y", interval="1d", progress=False)
        if not df_hist.empty:
            strategy = RSITradingStrategy(df_hist, rsi_period=r_period, oversold=r_buy, overbought=r_sell, 
                                         profit_take_rsi=r_pt_rsi, profit_take_pct=r_pt_pct, 
                                         initial_capital=init_cap, max_entries=max_e, stop_loss_pct=r_sl)
            strategy.calculate_indicators()
            ret, final_v = strategy.run_backtest()
            
            st.metric("最終淨值", f"${final_v:,.0f}", f"{ret:.2%}")
            
            # 簡易走勢圖
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=df_hist.index, y=df_hist['Close'], name="股價"))
            st.plotly_chart(fig, use_container_width=True)
