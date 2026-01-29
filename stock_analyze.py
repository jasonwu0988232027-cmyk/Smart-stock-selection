import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import requests
import plotly.graph_objects as go
from datetime import datetime, timedelta

# --- API 設定 ---
FINNHUB_API_KEY = "d5t2rvhr01qt62ngu1kgd5t2rvhr01qt62ngu1l0"
st.set_page_config(page_title="AI 股市預測專家", layout="wide")

# --- 1. 數據獲取 ---
@st.cache_data(ttl=3600)
def get_stock_data(symbol):
    try:
        df = yf.download(symbol, period="3mo", interval="1d", progress=False)
        if df.empty: return None
        df.columns = [col[0] if isinstance(col, tuple) else col for col in df.columns]
        return df.reset_index()
    except:
        return None

# --- 2. 改進的預測邏輯（使用固定種子確保一致性）---
def predict_future_prices(df, sentiment_score, days=10):
    """
    改進版預測函數，使用固定隨機種子確保相同輸入產生相同輸出
    """
    # 設定固定隨機種子（基於股票最後價格和日期，確保穩定性）
    last_price = df['Close'].iloc[-1]
    last_date = df['Date'].iloc[-1]
    seed = int(last_price * 1000 + days)
    np.random.seed(seed)
    
    # 計算技術指標
    volatility = df['Close'].pct_change().std() 
    recent_trend = (df['Close'].iloc[-1] - df['Close'].iloc[-5]) / df['Close'].iloc[-5]  # 近5日趨勢
    volume_change = (df['Volume'].iloc[-5:].mean() - df['Volume'].iloc[-20:-5].mean()) / df['Volume'].iloc[-20:-5].mean()
    
    # 情緒影響因子
    sentiment_bias = (sentiment_score - 0.5) * 0.015  # 降低情緒影響，更穩定
    trend_bias = recent_trend * 0.3  # 趨勢延續因子
    
    # 綜合偏差
    total_bias = sentiment_bias + trend_bias
    
    future_dates = [last_date + timedelta(days=i) for i in range(1, days + 1)]
    future_prices = []
    
    current_price = last_price
    for i in range(days):
        # 隨著時間衰減的趨勢影響
        decay_factor = 0.95 ** i
        adjusted_bias = total_bias * decay_factor
        
        # 隨機漫步 + 偏差
        change_pct = np.random.normal(adjusted_bias, volatility)
        current_price *= (1 + change_pct)
        future_prices.append(current_price)
    
    # 重置隨機種子（避免影響其他隨機操作）
    np.random.seed(None)
    
    return pd.DataFrame({'Date': future_dates, 'Close': future_prices}), {
        'volatility': volatility,
        'recent_trend': recent_trend,
        'volume_change': volume_change,
        'sentiment_bias': sentiment_bias,
        'trend_bias': trend_bias,
        'total_bias': total_bias
    }

# --- 3. 生成預測原因分析 ---
def generate_prediction_reason(df, future_df, metrics, sentiment_score):
    """
    生成詳細的預測原因說明
    """
    reasons = []
    
    # 1. 價格變動分析
    current_price = df['Close'].iloc[-1]
    predicted_price = future_df['Close'].iloc[-1]
    price_change_pct = ((predicted_price - current_price) / current_price) * 100
    
    if price_change_pct > 0:
        direction = "📈 上漲"
        color = "green"
    else:
        direction = "📉 下跌"
        color = "red"
    
    reasons.append(f"### {direction} 預測：{abs(price_change_pct):.2f}%")
    
    # 2. 技術面分析
    reasons.append("\n**📊 技術面因素：**")
    
    # 趨勢分析
    if metrics['recent_trend'] > 0.02:
        reasons.append(f"✓ 近期呈現上升趨勢 (+{metrics['recent_trend']*100:.2f}%)，慣性延續")
    elif metrics['recent_trend'] < -0.02:
        reasons.append(f"✓ 近期呈現下降趨勢 ({metrics['recent_trend']*100:.2f}%)，下行壓力存在")
    else:
        reasons.append(f"✓ 近期橫盤整理，趨勢不明顯")
    
    # 波動率分析
    if metrics['volatility'] > 0.03:
        reasons.append(f"⚠ 高波動率 ({metrics['volatility']:.4f})，價格波動較大")
    elif metrics['volatility'] < 0.015:
        reasons.append(f"✓ 低波動率 ({metrics['volatility']:.4f})，價格相對穩定")
    else:
        reasons.append(f"✓ 中等波動率 ({metrics['volatility']:.4f})")
    
    # 成交量分析
    if metrics['volume_change'] > 0.2:
        reasons.append(f"✓ 成交量放大 (+{metrics['volume_change']*100:.1f}%)，市場關注度提升")
    elif metrics['volume_change'] < -0.2:
        reasons.append(f"⚠ 成交量萎縮 ({metrics['volume_change']*100:.1f}%)，交易意願降低")
    
    # 3. 情緒面分析
    reasons.append("\n**🧠 市場情緒：**")
    if sentiment_score > 0.6:
        reasons.append(f"✓ 市場情緒偏多 ({sentiment_score:.2f})，利多氛圍濃厚")
    elif sentiment_score < 0.4:
        reasons.append(f"⚠ 市場情緒偏空 ({sentiment_score:.2f})，謹慎觀望氣氛")
    else:
        reasons.append(f"✓ 市場情緒中性 ({sentiment_score:.2f})，多空平衡")
    
    # 4. 綜合判斷
    reasons.append("\n**🎯 綜合評估：**")
    
    confidence_factors = []
    if abs(metrics['recent_trend']) > 0.03:
        confidence_factors.append("趨勢明確")
    if sentiment_score > 0.6 or sentiment_score < 0.4:
        confidence_factors.append("情緒明顯")
    if metrics['volume_change'] > 0.2:
        confidence_factors.append("量能配合")
    
    if len(confidence_factors) >= 2:
        confidence = "高"
        conf_emoji = "🟢"
    elif len(confidence_factors) == 1:
        confidence = "中"
        conf_emoji = "🟡"
    else:
        confidence = "低"
        conf_emoji = "🔴"
    
    reasons.append(f"{conf_emoji} 預測可信度：**{confidence}** ({', '.join(confidence_factors) if confidence_factors else '訊號不足'})")
    
    # 5. 風險提示
    reasons.append("\n**⚡ 風險提示：**")
    if metrics['volatility'] > 0.03:
        reasons.append("- 價格波動較大，建議設定停損")
    if abs(metrics['volume_change']) > 0.3:
        reasons.append("- 成交量異常變化，留意資金動向")
    reasons.append("- 本預測僅供參考，投資前請自行評估風險")
    
    return "\n".join(reasons)

# --- 3. Finnhub 情緒抓取 ---
@st.cache_data(ttl=3600)
def get_finnhub_sentiment(symbol):
    clean_symbol = symbol.split('.')[0]
    url = f"https://finnhub.io/api/v1/news-sentiment?symbol={clean_symbol}&token={FINNHUB_API_KEY}"
    try:
        res = requests.get(url).json()
        return res
    except: 
        return None

# --- UI 介面 ---
st.title("📈 AI 股市趨勢分析與預測系統")
st.markdown("*基於技術分析與市場情緒的智能預測模型*")

# 側邊欄
target_stock = st.sidebar.text_input("輸入股票代碼 (例: 2330.TW)", "2330.TW").upper()
forecast_days = st.sidebar.slider("預測天數", 5, 10, 7)

# 獲取數據
df = get_stock_data(target_stock)
sentiment_data = get_finnhub_sentiment(target_stock)
sent_score = sentiment_data['sentiment'].get('bullishPercent', 0.5) if sentiment_data and 'sentiment' in sentiment_data else 0.5

if df is not None:
    # 執行預測
    future_df, metrics = predict_future_prices(df, sent_score, days=forecast_days)
    
    # 生成預測原因
    prediction_reason = generate_prediction_reason(df, future_df, metrics, sent_score)
    
    # 主要圖表
    st.subheader(f"📊 {target_stock} 歷史走勢與 AI 預測路徑")
    
    fig = go.Figure()
    
    # 歷史 K 線
    fig.add_trace(go.Candlestick(
        x=df['Date'], 
        open=df['Open'], 
        high=df['High'],
        low=df['Low'], 
        close=df['Close'], 
        name="歷史數據"
    ))
    
    # 預測走勢（連接最後一天）
    connect_df = pd.concat([df.tail(1)[['Date', 'Close']], future_df])
    
    fig.add_trace(go.Scatter(
        x=connect_df['Date'], 
        y=connect_df['Close'],
        mode='lines+markers',
        line=dict(color='orange', width=3, dash='dot'),
        marker=dict(size=6),
        name=f"AI 預測未來 {forecast_days} 日"
    ))
    
    fig.update_layout(
        xaxis_rangeslider_visible=False, 
        height=600, 
        template="plotly_dark",
        hovermode='x unified'
    )
    st.plotly_chart(fig, use_container_width=True)
    
    # --- 分析面板 ---
    col1, col2 = st.columns([1, 1])
    
    with col1:
        st.markdown("### 📉 數據摘要")
        current_price = df['Close'].iloc[-1]
        predicted_price = future_df['Close'].iloc[-1]
        change = ((predicted_price - current_price) / current_price) * 100
        
        st.metric("當前價格", f"${current_price:.2f}")
        st.metric(
            f"{forecast_days} 日後預測價格", 
            f"${predicted_price:.2f}",
            f"{change:+.2f}%"
        )
        
        # 技術指標
        st.markdown("**技術指標：**")
        st.write(f"- 波動率：`{metrics['volatility']:.4f}`")
        st.write(f"- 5日趨勢：`{metrics['recent_trend']*100:+.2f}%`")
        st.write(f"- 成交量變化：`{metrics['volume_change']*100:+.1f}%`")
    
    with col2:
        st.markdown("### 🧠 AI 預測依據")
        st.markdown(prediction_reason)
    
    # 詳細預測數據表
    with st.expander("📅 查看每日預測明細"):
        display_df = future_df.copy()
        display_df['Date'] = display_df['Date'].dt.strftime('%Y-%m-%d')
        display_df['價格'] = display_df['Close'].apply(lambda x: f"${x:.2f}")
        display_df['變化%'] = display_df['Close'].pct_change().fillna(0).apply(lambda x: f"{x*100:+.2f}%")
        st.dataframe(display_df[['Date', '價格', '變化%']], use_container_width=True)
    
    # 免責聲明
    st.markdown("---")
    st.caption("⚠️ **免責聲明**：本預測系統僅供學習與研究使用，不構成投資建議。股市有風險，投資需謹慎。")
    
else:
    st.error("❌ 無法獲取數據，請檢查股票代碼格式是否正確。")
