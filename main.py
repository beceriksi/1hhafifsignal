import os
import time
import requests
from datetime import datetime, timezone

OKX_BASE = "https://www.okx.com"
COINGECKO_BASE = "https://api.coingecko.com/api/v3"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# ---- PARAMETRELER ----
TOP_LIMIT_DAILY = 80          # Günlük altcoin taramasında bakılacak en hacimli USDT spot sayısı
CANDLE_LIMIT_DAILY = 120      # Günlük mum sayısı (EMA, MACD için)
TRADES_LIMIT = 200            # Orderflow için alınacak trade sayısı
ORDERBOOK_DEPTH = 20          # Orderbook derinliği

# Market cap tabanlı eşikler
def ts():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


# ------------ HTTP Yardımcıları ------------

def jget_okx(path, params=None, retries=3, timeout=10):
    url = f"{OKX_BASE}{path}"
    for _ in range(retries):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code == 200:
                j = r.json()
                if j.get("code") == "0" and j.get("data") is not None:
                    return j["data"]
        except Exception:
            time.sleep(0.5)
    return None


def jget_json(url, params=None, retries=3, timeout=10):
    for _ in range(retries):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code == 200:
                return r.json()
        except Exception:
            time.sleep(0.5)
    return None


def telegram(msg: str):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("⚠ TELEGRAM_TOKEN veya CHAT_ID yok, mesaj gönderemem.")
        print("--- Mesaj içeriği ---")
        print(msg)
        print("---------------------")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"}
    try:
        r = requests.post(url, data=payload, timeout=10)
        if r.status_code != 200:
            print("Telegram hata:", r.text)
    except Exception as e:
        print("Telegram exception:", e)


# ------------ CoinGecko MCAP Haritası ------------

def load_mcap_map(max_pages: int = 2):
    """
    CoinGecko /coins/markets → symbol -> market_cap map
    En yüksek mcap'i olan symbol kazanır (aynı sembolü kullananlar için).
    """
    mcap_map = {}
    for page in range(1, max_pages + 1):
        data = jget_json(
            f"{COINGECKO_BASE}/coins/markets",
            params={
                "vs_currency": "usd",
                "order": "market_cap_desc",
                "per_page": 250,
                "page": page,
                "sparkline": "false",
            },
        )
        if not data:
            break
        for row in data:
            sym = str(row.get("symbol", "")).upper()
            mc = row.get("market_cap") or 0
            if not sym or not mc:
                continue
            if sym not in mcap_map or mc > mcap_map[sym]:
                mcap_map[sym] = mc
    return mcap_map


def classify_mcap(base: str, mcap_map: dict):
    mc = mcap_map.get(base.upper())
    if mc is None:
        return "UNKNOWN"
    if mc >= 10_000_000_000:
        return "HIGH"
    if mc >= 1_000_000_000:
        return "MID"
    if mc >= 100_000_000:
        return "LOW"
    return "MICRO"


def whale_thresholds(mcap_class: str):
    """
    MCAP sınıfına göre S/M/X whale eşikleri
    S: orta, M: büyük, X: süper whale
    """
    if mcap_class == "HIGH":
        return 500_000, 1_000_000, 1_500_000
    elif mcap_class == "MID":
        return 200_000, 400_000, 800_000
    elif mcap_class == "LOW":
        return 100_000, 200_000, 400_000
    else:
        return 80_000, 150_000, 300_000


def net_delta_thresholds(mcap_class: str):
    """
    Net delta eşikleri (MCAP'e göre ölçekli)
    """
    if mcap_class == "HIGH":
        return 200_000, -200_000
    elif mcap_class == "MID":
        return 100_000, -100_000
    elif mcap_class == "LOW":
        return 50_000, -50_000
    else:
        return 30_000, -30_000


def mcap_nice_label(mcap_class: str):
    if mcap_class == "HIGH":
        return "🟦 High-cap"
    if mcap_class == "MID":
        return "🟧 Mid-cap"
    if mcap_class == "LOW":
        return "🟨 Low-cap"
    if mcap_class == "MICRO":
        return "🟥 Micro-cap"
    return "⬜ Unknown-cap"


def tier_nice_label(tier: str):
    if tier == "S":
        return "S (Orta whale)"
    if tier == "M":
        return "M (Büyük whale)"
    if tier == "X":
        return "X (Süper whale)"
    return "-"


# ------------ OKX Yardımcıları ------------

def get_spot_usdt_top_tickers(limit=TOP_LIMIT_DAILY):
    """
    OKX SPOT tickers → USDT pariteleri içinden en yüksek 24h notional hacme göre ilk N'yi döndürür.
    Her eleman:
    {
        "inst_id": "ARB-USDT",
        "last": son fiyat,
        "sod": UTC0 açılış fiyatı (varsa, yoksa None),
        "vol_quote": 24h quote hacmi
    }
    """
    data = jget_okx("/api/v5/market/tickers", {"instType": "SPOT"})
    if not data:
        return []

    rows = []
    for d in data:
        inst_id = d.get("instId", "")
        if not inst_id.endswith("-USDT"):
            continue
        volCcy24h = d.get("volCcy24h")
        last = d.get("last")
        sod = d.get("sodUtc0")  # UTC0 günü başı fiyatı
        try:
            vol_quote = float(volCcy24h)
        except Exception:
            vol_quote = 0.0
        try:
            last_px = float(last)
        except Exception:
            last_px = None
        try:
            sod_px = float(sod) if sod is not None else None
        except Exception:
            sod_px = None

        rows.append(
            {
                "inst_id": inst_id,
                "last": last_px,
                "sod": sod_px,
                "vol_quote": vol_quote,
            }
        )

    rows.sort(key=lambda x: x["vol_quote"], reverse=True)
    return rows[:limit]


def get_candles(inst_id, bar="1D", limit=CANDLE_LIMIT_DAILY):
    data = jget_okx("/api/v5/market/candles", {"instId": inst_id, "bar": bar, "limit": limit})
    if not data:
        return []

    data = list(reversed(data))  # en eski en başa
    candles = []
    for row in data:
        try:
            ts_ms = int(row[0])
            o = float(row[1])
            h = float(row[2])
            l = float(row[3])
            c = float(row[4])
        except Exception:
            continue
        candles.append(
            {
                "ts": ts_ms,
                "open": o,
                "high": h,
                "low": l,
                "close": c,
            }
        )
    return candles


def get_trades(inst_id, limit=TRADES_LIMIT):
    data = jget_okx("/api/v5/market/trades", {"instId": inst_id, "limit": limit})
    return data or []


# ------------ Teknik Hesaplar ------------

def ema(values, period):
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    ema_val = sum(values[:period]) / period
    for v in values[period:]:
        ema_val = v * k + ema_val * (1 - k)
    return ema_val


def analyze_trades_orderflow(trades, medium_thr, whale_thr, super_thr):
    """
    Spot için orderflow:
    - Net notional delta (buy_notional - sell_notional)
    - S / M / X seviyesinde en büyük buy whale
    - S / M / X seviyesinde en büyük sell whale
    """
    buy_notional = 0.0
    sell_notional = 0.0
    best_buy = None
    best_sell = None

    for t in trades:
        try:
            px = float(t.get("px"))
            sz = float(t.get("sz"))
            side = t.get("side", "").lower()
        except Exception:
            continue

        notional = px * abs(sz)

        tier = None
        if notional >= super_thr:
            tier = "X"
        elif notional >= whale_thr:
            tier = "M"
        elif notional >= medium_thr:
            tier = "S"

        if side == "buy":
            buy_notional += notional
            if tier:
                if (best_buy is None) or (notional > best_buy["usd"]):
                    best_buy = {
                        "px": px,
                        "sz": sz,
                        "usd": notional,
                        "side": side,
                        "tier": tier,
                        "ts": t.get("ts"),
                    }
        elif side == "sell":
            sell_notional += notional
            if tier:
                if (best_sell is None) or (notional > best_sell["usd"]):
                    best_sell = {
                        "px": px,
                        "sz": sz,
                        "usd": notional,
                        "side": side,
                        "tier": tier,
                        "ts": t.get("ts"),
                    }

    net_delta = buy_notional - sell_notional

    return {
        "buy_notional": buy_notional,
        "sell_notional": sell_notional,
        "net_delta": net_delta,
        "buy_whale": best_buy,
        "sell_whale": best_sell,
        "has_buy_whale": best_buy is not None,
        "has_sell_whale": best_sell is not None,
    }


# ------------ BTC & ETH Günlük Özeti ------------

def daily_direction_label(trend_txt, mom_txt, net_delta):
    """
    BTC/ETH için basit yön yorumu:
    Trend + momentum + net delta kombinasyonu.
    """
    if trend_txt == "Yukarı" and mom_txt == "Pozitif" and net_delta > 0:
        return "LONG baskın"
    if trend_txt == "Aşağı" and mom_txt == "Negatif" and net_delta < 0:
        return "SHORT baskın"
    if net_delta > 0 and mom_txt == "Pozitif":
        return "LONG ağırlıklı"
    if net_delta < 0 and mom_txt == "Negatif":
        return "SHORT ağırlıklı"
    return "Yönsüz / Nötr"


def get_daily_summary(inst_id, mcap_map):
    candles = get_candles(inst_id, bar="1D", limit=CANDLE_LIMIT_DAILY)
    if len(candles) < 50:
        return None

    closes = [c["close"] for c in candles]
    last = closes[-1]

    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)
    ema200 = ema(closes, 200) if len(closes) >= 200 else None

    # MACD (12-26)
    ema_fast = ema(closes, 12)
    ema_slow = ema(closes, 26)
    macd = None
    if ema_fast is not None and ema_slow is not None:
        macd = ema_fast - ema_slow

    # Trend yorumu
    if ema200 is not None:
        if last > ema200 * 1.01:
            trend_txt = "Yukarı"
        elif last < ema200 * 0.99:
            trend_txt = "Aşağı"
        else:
            trend_txt = "Yatay"
    else:
        # 200 yoksa 50 EMA'ya göre
        if last > ema50 * 1.01:
            trend_txt = "Yukarı"
        elif last < ema50 * 0.99:
            trend_txt = "Aşağı"
        else:
            trend_txt = "Yatay"

    # Momentum yorumu
    if macd is None:
        mom_txt = "Bilinmiyor"
    else:
        if macd > 0:
            mom_txt = "Pozitif"
        elif macd < 0:
            mom_txt = "Negatif"
        else:
            mom_txt = "Düz"

    base = inst_id.split("-")[0]
    mcap_class = classify_mcap(base, mcap_map)
    medium_thr, whale_thr, super_thr = whale_thresholds(mcap_class)

    trades = get_trades(inst_id)
    of = analyze_trades_orderflow(trades, medium_thr, whale_thr, super_thr) if trades else None

    whale_txt = "Veri yok"
    delta_txt = "Veri yok"
    net_delta_val = 0.0

    if of:
        net_delta_val = of["net_delta"]
        delta_txt = f"Net delta (son {TRADES_LIMIT} trade): {of['net_delta']:.0f} USDT"
        w_buy = of["buy_whale"]
        w_sell = of["sell_whale"]
        if w_buy and (not w_sell or w_buy["usd"] >= (w_sell["usd"] if w_sell else 0)):
            whale_txt = f"BUY whale: {tier_nice_label(w_buy['tier'])} ~${w_buy['usd']:,.0f}"
        elif w_sell:
            whale_txt = f"SELL whale: {tier_nice_label(w_sell['tier'])} ~${w_sell['usd']:,.0f}"
        else:
            whale_txt = "Anlamlı whale yok"

    day_dir = daily_direction_label(trend_txt, mom_txt, net_delta_val)

    return {
        "inst_id": inst_id,
        "last": last,
        "trend": trend_txt,
        "momentum": mom_txt,
        "delta_txt": delta_txt,
        "whale_txt": whale_txt,
        "mcap_class": mcap_class,
        "direction": day_dir,
        "net_delta": net_delta_val,
    }


# ------------ Altcoin Tarama (Günün adayları) ------------

def analyze_altcoin_for_daily(inst_id, ticker_info, mcap_map):
    """
    Günlük altcoin analizi:
    - Trend (fiyat vs EMA20)
    - Net delta + whale
    - 24h % değişim
    """
    candles = get_candles(inst_id, bar="1D", limit=60)
    if len(candles) < 30:
        return None

    closes = [c["close"] for c in candles]
    last = closes[-1]
    ema20 = ema(closes, 20)
    if ema20 is None:
        return None

    # Trend etiketi
    if last > ema20 * 1.01:
        trend_tag = "UP"
    elif last < ema20 * 0.99:
        trend_tag = "DOWN"
    else:
        trend_tag = "FLAT"

    base = inst_id.split("-")[0]
    mcap_class = classify_mcap(base, mcap_map)
    medium_thr, whale_thr, super_thr = whale_thresholds(mcap_class)
    nd_pos, nd_neg = net_delta_thresholds(mcap_class)

    trades = get_trades(inst_id)
    if not trades:
        return None

    of = analyze_trades_orderflow(trades, medium_thr, whale_thr, super_thr)

    last_ticker_px = ticker_info.get("last")
    sod_px = ticker_info.get("sod")
    pct_change_24h = None
    if last_ticker_px is not None and sod_px is not None and sod_px > 0:
        pct_change_24h = (last_ticker_px - sod_px) / sod_px * 100.0

    return {
        "inst_id": inst_id,
        "last": last,
        "ema20": ema20,
        "trend_tag": trend_tag,
        "net_delta": of["net_delta"],
        "buy_whale": of["buy_whale"],
        "sell_whale": of["sell_whale"],
        "has_buy_whale": of["has_buy_whale"],
        "has_sell_whale": of["has_sell_whale"],
        "mcap_class": mcap_class,
        "nd_pos_thr": nd_pos,
        "nd_neg_thr": nd_neg,
        "pct_change_24h": pct_change_24h,
    }


def pick_daily_candidates(alt_stats_list, max_each=3):
    """
    En güçlü 3 LONG, 3 SHORT ve "buyer var ama hareket yok" 3 coin'i seçer.
    """
    long_cands = []
    short_cands = []
    buyer_accum = []

    for s in alt_stats_list:
        nd = s["net_delta"]
        nd_pos_thr = s["nd_pos_thr"]
        nd_neg_thr = s["nd_neg_thr"]
        trend = s["trend_tag"]
        buy_whale = s["buy_whale"]
        sell_whale = s["sell_whale"]
        pct_ch = s["pct_change_24h"]

        # LONG adayları: trend yukarı/yatay + pozitif net delta + buy whale
        if (trend in ["UP", "FLAT"]) and (nd >= nd_pos_thr) and s["has_buy_whale"]:
            long_cands.append(s)

        # SHORT adayları: trend aşağı/yatay + negatif net delta + sell whale
        if (trend in ["DOWN", "FLAT"]) and (nd <= nd_neg_thr) and s["has_sell_whale"]:
            short_cands.append(s)

        # Buyer var ama hareket yok adayları:
        if s["has_buy_whale"] and nd > 0:
            # Günlük değişim küçükse veya fiyat EMA20'ye çok yakınsa → birikim adayı
            near_ema = abs(s["last"] - s["ema20"]) / s["ema20"] < 0.01
            low_move = (pct_ch is not None and abs(pct_ch) < 2.0)
            if near_ema or low_move:
                buyer_accum.append(s)

    # Sıralama
    long_cands.sort(key=lambda x: x["net_delta"], reverse=True)
    short_cands.sort(key=lambda x: x["net_delta"])  # en negatif öne
    buyer_accum.sort(
        key=lambda x: (x["buy_whale"]["usd"] if x["buy_whale"] else 0), reverse=True
    )

    return long_cands[:max_each], short_cands[:max_each], buyer_accum[:max_each]


# ------------ Telegram Mesajı (Günlük Rapor) ------------

def build_daily_report(btc_info, eth_info, long_list, short_list, buyer_list):
    lines = []
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    lines.append(f"*📅 Günlük Piyasa Özeti – 1D (OKX)*")
    lines.append(f"_Tarih (UTC):_ `{today_str}`\n")

    lines.append("`#################################`")
    lines.append("*1) BTC & ETH Günlük Durum*")
    lines.append("`#################################`\n")

    if btc_info:
        lines.append(f"*BTC-USDT* {mcap_nice_label(btc_info['mcap_class'])}")
        lines.append(f"- Fiyat: `{btc_info['last']:.2f}`")
        lines.append(f"- Trend (1D): *{btc_info['trend']}*")
        lines.append(f"- Momentum (MACD): *{btc_info['momentum']}*")
        lines.append(f"- {btc_info['delta_txt']}")
        lines.append(f"- {btc_info['whale_txt']}")
        lines.append(f"- *Günlük yön yorumu:* `{btc_info['direction']}`\n")

    if eth_info:
        lines.append(f"*ETH-USDT* {mcap_nice_label(eth_info['mcap_class'])}")
        lines.append(f"- Fiyat: `{eth_info['last']:.2f}`")
        lines.append(f"- Trend (1D): *{eth_info['trend']}*")
        lines.append(f"- Momentum (MACD): *{eth_info['momentum']}*")
        lines.append(f"- {eth_info['delta_txt']}")
        lines.append(f"- {eth_info['whale_txt']}")
        lines.append(f"- *Günlük yön yorumu:* `{eth_info['direction']}`\n")

    # Genel yön
    lines.append("`#################################`")
    lines.append("*2) Bugünün Genel Yönü*")
    lines.append("`#################################`\n")

    dir_scores = {"LONG": 0, "SHORT": 0}
    for info in [btc_info, eth_info]:
        if not info:
            continue
        d = info["direction"]
        nd = info["net_delta"]
        if "LONG" in d:
            dir_scores["LONG"] += 1
            if nd > 0:
                dir_scores["LONG"] += 0.5
        if "SHORT" in d:
            dir_scores["SHORT"] += 1
            if nd < 0:
                dir_scores["SHORT"] += 0.5

    if dir_scores["LONG"] > dir_scores["SHORT"]:
        overall_dir = "LONG tarafı daha güvenli (günlükte yukarı ağırlık var)"
    elif dir_scores["SHORT"] > dir_scores["LONG"]:
        overall_dir = "SHORT tarafı daha güvenli (günlükte aşağı ağırlık var)"
    else:
        overall_dir = "Net bir yön yok, gün içi trade daha mantıklı"

    lines.append(f"🎯 *Bugünün genel yön yorumu:* {overall_dir}\n")

    # LONG adayları
    lines.append("`#################################`")
    lines.append("*3) Günün En Güçlü LONG Adayları*")
    lines.append("`#################################`\n")

    if not long_list:
        lines.append("_Bugün için özel LONG adayı bulunamadı._\n")
    else:
        for idx, s in enumerate(long_list, start=1):
            nd = s["net_delta"]
            w = s["buy_whale"]
            w_txt = "Whale: Yok"
            if w:
                w_txt = f"Whale: {tier_nice_label(w['tier'])} ~`${w['usd']:,.0f}` @ {w['px']:.4f}"
            ch_txt = ""
            if s["pct_change_24h"] is not None:
                ch_txt = f"{s['pct_change_24h']:.2f}%"
            lines.append(f"*{idx}) {s['inst_id']}* {mcap_nice_label(s['mcap_class'])}")
            lines.append(f"- Fiyat: `{s['last']:.4f}`  | EMA20: `{s['ema20']:.4f}`")
            lines.append(f"- Trend: `{s['trend_tag']}`  | 24h Değişim: `{ch_txt}`")
            lines.append(f"- Net delta: `{nd:.0f} USDT`")
            lines.append(f"- {w_txt}\n")

    # SHORT adayları
    lines.append("`#################################`")
    lines.append("*4) Günün En Güçlü SHORT Adayları*")
    lines.append("`#################################`\n")

    if not short_list:
        lines.append("_Bugün için özel SHORT adayı bulunamadı._\n")
    else:
        for idx, s in enumerate(short_list, start=1):
            nd = s["net_delta"]
            w = s["sell_whale"]
            w_txt = "Whale: Yok"
            if w:
                w_txt = f"Whale: {tier_nice_label(w['tier'])} ~`${w['usd']:,.0f}` @ {w['px']:.4f}"
            ch_txt = ""
            if s["pct_change_24h"] is not None:
                ch_txt = f"{s['pct_change_24h']:.2f}%"
            lines.append(f"*{idx}) {s['inst_id']}* {mcap_nice_label(s['mcap_class'])}")
            lines.append(f"- Fiyat: `{s['last']:.4f}`  | EMA20: `{s['ema20']:.4f}`")
            lines.append(f"- Trend: `{s['trend_tag']}`  | 24h Değişim: `{ch_txt}`")
            lines.append(f"- Net delta: `{nd:.0f} USDT`")
            lines.append(f"- {w_txt}\n")

    # Buyer var ama hareket yok
    lines.append("`#################################`")
    lines.append("*5) Buyer Gelmiş Ama Hareket Yok (Birikim Adayları)*")
    lines.append("`#################################`\n")

    if not buyer_list:
        lines.append("_Bugün için belirgin 'buyer var ama patlamamış' coin tespit edilmedi._\n")
    else:
        for idx, s in enumerate(buyer_list, start=1):
            nd = s["net_delta"]
            w = s["buy_whale"]
            w_txt = "Whale: Yok"
            if w:
                w_txt = f"Whale: {tier_nice_label(w['tier'])} ~`${w['usd']:,.0f}` @ {w['px']:.4f}"
            ch_txt = ""
            if s["pct_change_24h"] is not None:
                ch_txt = f"{s['pct_change_24h']:.2f}%"
            lines.append(f"*{idx}) {s['inst_id']}* {mcap_nice_label(s['mcap_class'])}")
            lines.append(f"- Fiyat: `{s['last']:.4f}`  | EMA20: `{s['ema20']:.4f}`")
            lines.append(f"- Trend: `{s['trend_tag']}`  | 24h Değişim: `{ch_txt}`")
            lines.append(f"- Net delta: `{nd:.0f} USDT`")
            lines.append(f"- {w_txt}")
            lines.append(f"_Not:_ Whale alımı + pozitif net delta var ama günlük hareket sınırlı. Gün içinde patlama potansiyeli olabilir.\n")

    lines.append(f"_Rapor oluşturma zamanı (UTC):_ `{ts()}`")

    return "\n".join(lines)


# ------------ MAIN ------------

def main():
    print(f"[{ts()}] Günlük analiz botu çalışıyor...")

    # MCAP haritası
    print("CoinGecko market cap verisi çekiliyor...")
    mcap_map = load_mcap_map()
    print(f"MCAP haritası yüklendi. Sembol sayısı: {len(mcap_map)}")

    # BTC & ETH günlük özet
    print("BTC & ETH günlük analiz yapılıyor...")
    btc_info = get_daily_summary("BTC-USDT", mcap_map)
    eth_info = get_daily_summary("ETH-USDT", mcap_map)

    # Top USDT spot tickers
    print("OKX top USDT spot listesi çekiliyor...")
    tickers = get_spot_usdt_top_tickers(limit=TOP_LIMIT_DAILY)
    if not tickers:
        print("Top tickers alınamadı, sadece BTC/ETH raporlanacak.")

    alt_stats = []
    if tickers:
        print(f"{len(tickers)} sembol için günlük altcoin taraması başlıyor...")
        for i, t in enumerate(tickers, start=1):
            inst_id = t["inst_id"]
            # BTC & ETH'yi altcoin listesinden hariç tutabiliriz, zaten yukarıda analiz edildi
            if inst_id in ("BTC-USDT", "ETH-USDT"):
                continue
            print(f"[{i}/{len(tickers)}] {inst_id} analiz ediliyor...")
            try:
                s = analyze_altcoin_for_daily(inst_id, t, mcap_map)
                if s:
                    alt_stats.append(s)
            except Exception as e:
                print(f"  {inst_id} analiz hatası:", e)
            time.sleep(0.1)

    long_list, short_list, buyer_list = pick_daily_candidates(alt_stats, max_each=3)

    msg = build_daily_report(btc_info, eth_info, long_list, short_list, buyer_list)
    telegram(msg)
    print("✅ Günlük rapor Telegram'a gönderildi.")


if __name__ == "__main__":
    main()
