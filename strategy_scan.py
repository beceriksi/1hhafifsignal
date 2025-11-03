import os, time, requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone

# --- Secrets ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# --- Endpoints ---
MEXC_FAPI = "https://contract.mexc.com"
BINANCE = "https://api.binance.com"
COINGECKO_GLOBAL = "https://api.coingecko.com/api/v3/global"

# ---------- utils ----------
def ts(): return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

def jget(url, params=None, retries=3, timeout=12):
    for _ in range(retries):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code == 200:
                return r.json()
        except:
            time.sleep(0.8)
    return None

def telegram(text):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print(text); return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
        )
    except:
        pass

# ---------- indicators ----------
def ema(x, n): return x.ewm(span=n, adjust=False).mean()

def rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0); dn = -d.clip(upper=0)
    rs = up.ewm(alpha=1/n, adjust=False).mean() / (dn.ewm(alpha=1/n, adjust=False).mean() + 1e-12)
    return 100 - (100/(1+rs))

def macd(s, f=12, m=26, sig=9):
    fast = ema(s,f); slow = ema(s,m)
    line = fast - slow
    signal = line.ewm(span=sig, adjust=False).mean()
    return line, signal, line - signal

def adx(df, n=14):
    up_move = df['high'].diff()
    dn_move = -df['low'].diff()
    plus_dm = np.where((up_move > dn_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((dn_move > up_move) & (dn_move > 0), dn_move, 0.0)
    tr1 = df['high'] - df['low']
    tr2 = (df['high'] - df['close'].shift()).abs()
    tr3 = (df['low'] - df['close'].shift()).abs()
    tr = pd.DataFrame({'a':tr1,'b':tr2,'c':tr3}).max(axis=1)
    atr = tr.ewm(alpha=1/n, adjust=False).mean()
    plus_di = 100 * pd.Series(plus_dm).ewm(alpha=1/n, adjust=False).mean() / (atr + 1e-12)
    minus_di = 100 * pd.Series(minus_dm).ewm(alpha=1/n, adjust=False).mean() / (atr + 1e-12)
    dx = ((plus_di - minus_di).abs() / ((plus_di + minus_di) + 1e-12)) * 100
    return dx.ewm(alpha=1/n, adjust=False).mean()

def bos_up(df, look=30, excl=1):
    hh = df['high'][:-excl].tail(look).max()
    return df['close'].iloc[-1] > hh

def bos_dn(df, look=30, excl=1):
    ll = df['low'][:-excl].tail(look).min()
    return df['close'].iloc[-1] < ll

def volume_spike(df, n=20, r=1.3):
    if len(df) < n+2: return False, 1.0
    last = df['volume'].iloc[-1]
    base = df['volume'].iloc[-(n+1):-1].mean()
    ratio = last / (base + 1e-12)
    return ratio >= r, ratio

# ---------- market states (1H) ----------
def coin_state_1h(symbol):
    d = jget(f"{BINANCE}/api/v3/klines", {"symbol":symbol,"interval":"1h","limit":200})
    if not d: return "NÖTR"
    df = pd.DataFrame(d, columns=["t","o","h","l","c","v","ct","x1","x2","x3","x4","x5"]).astype(float)
    c = df['c']; e20, e50 = ema(c,20).iloc[-1], ema(c,50).iloc[-1]; r = rsi(c,14).iloc[-1]
    if e20>e50 and r>50: return "GÜÇLÜ"
    if e20<e50 and r<50: return "ZAYIF"
    return "NÖTR"

def btc_eth_state_1h():
    return coin_state_1h("BTCUSDT"), coin_state_1h("ETHUSDT")

def btc_24h_change_pct():
    d = jget(f"{BINANCE}/api/v3/ticker/24hr", {"symbol":"BTCUSDT"})
    try: return float(d["priceChangePercent"])
    except: return None

def global_market_note():
    g = jget(COINGECKO_GLOBAL)
    try:
        total_pct = float(g["data"]["market_cap_change_percentage_24h_usd"])
        btc_dom   = float(g["data"]["market_cap_percentage"]["btc"])
        usdt_dom  = float(g["data"]["market_cap_percentage"]["usdt"])
    except:
        return "Piyasa: veri alınamadı.", None, None, None

    btc_pct = btc_24h_change_pct()
    if btc_pct is None:
        note = f"Piyasa: BTC 24h veri yok | BTC.D {btc_dom:.1f}% | USDT.D {usdt_dom:.1f}%"
        return note, total_pct, btc_dom, usdt_dom

    # BTC.D trend heuristiği: BTC 24h % vs total 24h %
    btc_d_arrow = "↑" if btc_pct > total_pct else ("↓" if btc_pct < total_pct else "→")
    btc_dir = "↑" if btc_pct>0 else ("↓" if btc_pct<0 else "→")

    # Total2 yorumu: BTC.D ↓ ve toplam piyasa ↑ ise altlara giriş var kabul
    total2_note = "↑ (Altlara giriş)" if (btc_d_arrow=="↓" and total_pct is not None and total_pct>=0) else \
                  ("↓ (Altlardan çıkış)" if (btc_d_arrow=="↑" and total_pct is not None and total_pct<=0) \
                   else "→ (Karışık)")

    # USDT.D yorumu: seviye yorumu (trend hesapsız, sade)
    usdt_note = f"{usdt_dom:.1f}%"
    if usdt_dom >= 7.0: usdt_note += " (riskten kaçış)"
    elif usdt_dom <= 5.0: usdt_note += " (risk alımı)"

    note = f"Piyasa: BTC {btc_dir} + BTC.D {btc_d_arrow} (BTC.D {btc_dom:.1f}%) | Total2: {total2_note} | USDT.D: {usdt_note}"
    return note, total_pct, btc_dom, usdt_dom

# ---------- mexc data ----------
def mexc_symbols():
    d = jget(f"{MEXC_FAPI}/api/v1/contract/detail")
    if not d or "data" not in d: return []
    return [s["symbol"] for s in d["data"] if s.get("quoteCoin")=="USDT"]

def klines_mexc(sym, interval="1h", limit=260):
    d = jget(f"{MEXC_FAPI}/api/v1/contract/kline/{sym}", {"interval": interval, "limit": limit})
    if not d or "data" not in d: return None
    df = pd.DataFrame(d["data"], columns=["ts","open","high","low","close","volume","turnover"]).astype(
        {"open":"float64","high":"float64","low":"float64","close":"float64","volume":"float64","turnover":"float64"}
    )
    return df

def funding_rate_mexc(sym):
    d = jget(f"{MEXC_FAPI}/api/v1/contract/funding_rate", {"symbol": sym})
    try: return float(d["data"]["fundingRate"])
    except: return None

# ---------- analysis ----------
def analyze_symbol(sym, vol_r=1.3):
    df = klines_mexc(sym, "1h", 200)
    if df is None or len(df) < 80: return None, None

    # likidite filtresi (son 1H turnover >= 500k USDT)
    if float(df["turnover"].iloc[-1]) < 500_000:
        return None, "lowliq"

    # GAP filtresi: son 1H %5'ten fazla hareketse atla (kısa vade için)
    c = df['close']
    last_change = abs(float(c.iloc[-1]/c.iloc[-2] - 1))
    if last_change > 0.05:
        return None, "gap"

    h, l = df['high'], df['low']
    e20, e50 = ema(c,20).iloc[-1], ema(c,50).iloc[-1]
    trend_up = e20 > e50
    r = float(rsi(c,14).iloc[-1])
    m_line, m_sig, _ = macd(c)
    macd_up = m_line.iloc[-1] > m_sig.iloc[-1]
    macd_dn = not macd_up
    adx_val = float(adx(pd.DataFrame({'high':h,'low':l,'close':c}),14).iloc[-1])
    strong_trend = adx_val >= 10
    bosU, bosD = bos_up(df, look=30), bos_dn(df, look=30)

    # Hacim şartı (x1.3)
    v_ok, v_ratio = volume_spike(df, n=20, r=vol_r)
    if not v_ok:
        return None, "novol"

    # SELL için hacim artışı + düşüş
    last_down = float(c.iloc[-1]) < float(c.iloc[-2])
    sell_vol_strong = last_down and v_ok

    side, bos_flag = None, False
    if trend_up and r > 55 and macd_up and strong_trend:
        side = "BUY"; bos_flag = bosU
    elif (not trend_up) and r < 45 and macd_dn and strong_trend and sell_vol_strong:
        side = "SELL"; bos_flag = bosD
    else:
        return None, None

    fr = funding_rate_mexc(sym)
    funding_txt = ""
    if fr is not None:
        if fr > 0.01:  funding_txt = f" | Funding:+{fr:.3f}"
        elif fr < -0.01: funding_txt = f" | Funding:{fr:.3f}"

    trend_txt = "↑" if trend_up else "↓"
    bos_txt = "↑" if bosU else ("↓" if bosD else "-")
    vol_txt = f"x{v_ratio:.2f}"
    px = float(c.iloc[-1])

    line = f"{sym} | Trend:{trend_txt} | RSI:{r:.1f} | Hacim {vol_txt} | ADX:{adx_val:.0f} | BoS:{bos_txt} | Fiyat:{px}{funding_txt}"
    return (side, line), None

def main():
    btc_s, eth_s = btc_eth_state_1h()
    market_note, _, _, _ = global_market_note()

    syms = mexc_symbols()
    if not syms:
        telegram("⚠️ Sembol listesi alınamadı (MEXC)."); return

    buys, sells = [], []
    skipped = {"lowliq":0,"gap":0,"novol":0}
    for i, s in enumerate(syms):
        try:
            res, flag = analyze_symbol(s, vol_r=1.3)
            if flag in skipped: skipped[flag] += 1
            if res:
                side, line = res
                if side == "BUY":  buys.append(f"- {line}")
                else:              sells.append(f"- {line}")
        except:
            pass
        if i % 15 == 0: time.sleep(0.25)

    parts = [f"⚡ *Kısa Vade Sinyal (1H)*\nBTC: {btc_s} | ETH: {eth_s}\n{market_note}"]
    if buys:
        parts.append("\n🟢 *BUY Potansiyeli:*")
        parts.extend(buys[:25])
    if sells:
        parts.append("\n🔴 *SELL Potansiyeli:*")
        parts.extend(sells[:25])
    if not buys and not sells:
        parts.append("\nℹ️ Şu an 1H kriterlerine uyan sinyal yok.")

    parts.append(f"\n📊 Özet: BUY:{len(buys)} | SELL:{len(sells)} | Atlanan (likidite:{skipped['lowliq']}, gap:{skipped['gap']}, hacim:{skipped['novol']})")
    telegram("\n".join(parts))

if __name__ == "__main__":
    main()
