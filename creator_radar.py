"""
創作者雷達 Creator Radar  v1.0
==================================
用途：把一批「已合法取得」的 IG/Threads 創作者候選資料，
      自動算互動率、依指標篩選、用 AI 判斷品牌調性，產出排序清單。

界線（重要）：
  本工具「不」爬取 IG/Threads。Meta 禁止未授權爬取，帳號與法律風險
  由執行者自負。輸入的 CSV 來源請自行透過以下合法管道取得：
    - 官方/授權的第三方 creator discovery API（Modash、Phyllo、HypeAuditor…）
    - 手動整理
  本工具負責的是「取得資料之後」的篩選與判斷自動化。

作者交付規範：設定外部化、AI 呼叫多層 fallback、NaN 全處理、參數集中。
"""

import json
import os
import csv
import math
import time
import random
import argparse

# ────────────────────────────────────────────────────────────
# 可調參數集中區（依鐵則 5.1）
# ────────────────────────────────────────────────────────────
MIN_FOLLOWERS = 5_000        # 粉絲數下限；調大→只留大帳號，調小→納入微型創作者
MAX_FOLLOWERS = 500_000      # 粉絲數上限；酒商常偏好中腰部(1萬~10萬)，過大帳號業配貴且互動稀釋
MIN_ENGAGEMENT = 1.5         # 互動率(%)下限；調大→更嚴格只留高互動，調小→放寬
RECENT_DAYS_ACTIVE = 45      # 近幾天內有貼文才算「活躍」；調大→放寬活躍認定
TOP_N = 30                   # 最終輸出前幾名

# 品牌調性描述——這段決定 AI 怎麼判斷「對不對味」，是最該依客戶調整的地方
BRAND_PROFILE = (
    "潮流、夜生活、酒吧調酒、有梗幽默、吃喝玩樂、派對社交、生活風格。"
    "目標客群為 25-40 歲、對威士忌/琴酒/利口酒等烈酒品牌有興趣的族群。"
    "偏好本人風格突出、留言有梗、會玩會鬧的創作者，非教條式或過度商業化的帳號。"
)

# ────────────────────────────────────────────────────────────
# 設定載入（依鐵則 1.2 標準模板）
# ────────────────────────────────────────────────────────────
_SETTINGS_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "settings.json"
)


def _load_settings() -> dict:
    if not os.path.exists(_SETTINGS_PATH):
        print("⚠️  找不到 settings.json，AI 調性分類將停用，僅執行指標篩選（程式仍可執行）。")
        return {}
    try:
        with open(_SETTINGS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        print(f"⚠️  settings.json 格式錯誤：{e}（常見原因：最後一項多了逗號）")
        return {}


_SETTINGS = _load_settings()


# ────────────────────────────────────────────────────────────
# 工具函式
# ────────────────────────────────────────────────────────────
def _safe_float(val, default=float("nan")):
    """把任何輸入安全轉 float；空字串/None/爛資料 → NaN（依鐵則 4.1）"""
    if val is None:
        return default
    s = str(val).strip().replace(",", "").replace("%", "")
    if s == "":
        return default
    try:
        return float(s)
    except ValueError:
        return default


def _safe_int(val, default=0):
    f = _safe_float(val, default=float("nan"))
    return default if math.isnan(f) else int(f)


def _is_nan(x) -> bool:
    return isinstance(x, float) and math.isnan(x)


# ────────────────────────────────────────────────────────────
# 互動率計算
# ────────────────────────────────────────────────────────────
def compute_engagement_rate(row: dict) -> float:
    """
    互動率 = (平均按讚 + 平均留言) / 粉絲數 × 100
    若 CSV 已直接提供 engagement_rate 欄位則優先採用。
    """
    direct = _safe_float(row.get("engagement_rate"))
    if not _is_nan(direct):
        return direct

    followers = _safe_float(row.get("followers"))
    avg_likes = _safe_float(row.get("avg_likes"))
    avg_comments = _safe_float(row.get("avg_comments"), default=0.0)

    if _is_nan(followers) or followers <= 0 or _is_nan(avg_likes):
        return float("nan")

    interactions = avg_likes + (0.0 if _is_nan(avg_comments) else avg_comments)
    return round(interactions / followers * 100, 2)


# ────────────────────────────────────────────────────────────
# 第一層篩選：硬指標（依鐵則 5.2 分層）
# ────────────────────────────────────────────────────────────
def passes_hard_filters(row: dict) -> tuple[bool, str]:
    """回傳 (是否通過, 未通過原因)。單筆爛資料 continue 不中斷整批（鐵則 4.2）"""
    followers = _safe_int(row.get("followers"), default=0)
    if followers < MIN_FOLLOWERS:
        return False, f"粉絲數 {followers} < {MIN_FOLLOWERS}"
    if followers > MAX_FOLLOWERS:
        return False, f"粉絲數 {followers} > {MAX_FOLLOWERS}（過大）"

    er = row["_engagement_rate"]
    if _is_nan(er):
        return False, "互動率資料不足無法計算"
    if er < MIN_ENGAGEMENT:
        return False, f"互動率 {er}% < {MIN_ENGAGEMENT}%"

    days = _safe_int(row.get("days_since_last_post"), default=999)
    if days > RECENT_DAYS_ACTIVE:
        return False, f"近 {days} 天無貼文（門檻 {RECENT_DAYS_ACTIVE}）"

    return True, ""


# ────────────────────────────────────────────────────────────
# 第二層：AI 調性分類（多層 fallback，依鐵則 2.1）
# ────────────────────────────────────────────────────────────
def classify_brand_fit(row: dict) -> dict:
    """
    用 LLM 判斷帳號調性與品牌的契合度。
    層① 有 API key → 呼叫 Anthropic API
    層② 無 key 或呼叫失敗 → 關鍵字啟發式評分（保證不中斷）
    回傳 {score: 0-100, tags: [...], reason: str}
    """
    api_key = _SETTINGS.get("ANTHROPIC_API_KEY", "")
    bio = str(row.get("bio", "")).strip()
    recent = str(row.get("recent_captions", "")).strip()

    if api_key:
        try:
            return _classify_with_llm(api_key, bio, recent, row.get("username", ""))
        except Exception as e:
            print(f"⚠️  AI 分類失敗（{row.get('username','?')}）：{e} → 降級為關鍵字評分")

    return _classify_heuristic(bio, recent)


def _classify_with_llm(api_key: str, bio: str, recent: str, username: str) -> dict:
    import urllib.request

    prompt = (
        f"你是烈酒品牌的網紅行銷選角助手。品牌調性如下：\n{BRAND_PROFILE}\n\n"
        f"以下是一位 IG/Threads 創作者的公開資料：\n"
        f"帳號：{username}\n個人簡介：{bio}\n近期貼文文案：{recent}\n\n"
        f"請評估此創作者與品牌調性的契合度。只回傳 JSON，不要任何其他文字，格式：\n"
        f'{{"score": 0-100的整數, "tags": ["最多4個風格標籤"], "reason": "20字內中文理由"}}'
    )

    payload = json.dumps({
        "model": "claude-sonnet-4-5",
        "max_tokens": 300,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:  # timeout 依鐵則 2.3
        data = json.loads(resp.read().decode("utf-8"))

    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    text = text.strip().replace("```json", "").replace("```", "").strip()
    parsed = json.loads(text)
    return {
        "score": _safe_int(parsed.get("score"), default=0),
        "tags": parsed.get("tags", [])[:4],
        "reason": str(parsed.get("reason", ""))[:40],
        "method": "AI",
    }


# 關鍵字啟發式：無 API key 時的最後防線（鐵則 2.1 層③）
_POSITIVE_KW = ["調酒", "酒吧", "威士忌", "琴酒", "夜生活", "派對", "梗", "幽默",
                "吃喝", "玩樂", "生活", "潮", "bartender", "cocktail", "party",
                "nightlife", "whisky", "gin", "美食", "旅遊", "musician", "dj"]
_NEGATIVE_KW = ["親子", "育兒", "宗教", "課程", "團購", "代購", "醫療", "保險",
                "religion", "kids", "教學", "考試"]


def _classify_heuristic(bio: str, recent: str) -> dict:
    text = (bio + " " + recent).lower()
    pos = sum(1 for kw in _POSITIVE_KW if kw.lower() in text)
    neg = sum(1 for kw in _NEGATIVE_KW if kw.lower() in text)
    raw = pos * 15 - neg * 20
    score = max(0, min(100, 40 + raw))  # 40 為中性基準
    tags = [kw for kw in _POSITIVE_KW if kw.lower() in text][:4]
    return {
        "score": score,
        "tags": tags,
        "reason": f"關鍵字命中 正{pos}/負{neg}",
        "method": "關鍵字",
    }


# ────────────────────────────────────────────────────────────
# 主流程
# ────────────────────────────────────────────────────────────
def run(input_csv: str, output_csv: str):
    if not os.path.exists(input_csv):
        print(f"❌ 找不到輸入檔：{input_csv}")
        return

    with open(input_csv, "r", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    print(f"📥 讀入 {len(rows)} 筆候選帳號\n")

    survivors = []
    rejected = 0
    for i, row in enumerate(rows, 1):
        uname = row.get("username", f"row{i}")
        try:
            row["_engagement_rate"] = compute_engagement_rate(row)
            ok, reason = passes_hard_filters(row)
            if not ok:
                rejected += 1
                print(f"  ✗ {uname:<22} 淘汰：{reason}")
                continue

            fit = classify_brand_fit(row)
            row["_fit_score"] = fit["score"]
            row["_fit_tags"] = "、".join(fit["tags"])
            row["_fit_reason"] = fit["reason"]
            row["_fit_method"] = fit["method"]
            # 綜合分 = 調性契合 70% + 互動率加權 30%（互動率封頂 10% 換算滿分）
            er_score = min(row["_engagement_rate"] / 10 * 100, 100)
            row["_total"] = round(fit["score"] * 0.7 + er_score * 0.3, 1)
            survivors.append(row)
            print(f"  ✓ {uname:<22} 契合{fit['score']:>3} 互動{row['_engagement_rate']:>5}% [{fit['method']}] {row['_fit_reason']}")

            if fit["method"] == "AI":
                time.sleep(random.uniform(0.5, 1.5))  # 請求禮儀，鐵則 2.3
        except Exception as e:
            rejected += 1
            print(f"  ✗ {uname:<22} 例外跳過：{e}")  # 明確錯誤，鐵則 2.4
            continue

    survivors.sort(key=lambda r: r["_total"], reverse=True)
    top = survivors[:TOP_N]

    out_fields = ["rank", "username", "followers", "_engagement_rate",
                  "_fit_score", "_total", "_fit_tags", "_fit_reason",
                  "_fit_method", "profile_url"]
    with open(output_csv, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=out_fields, extrasaction="ignore")
        w.writeheader()
        for rank, r in enumerate(top, 1):
            r["rank"] = rank
            w.writerow(r)

    print(f"\n{'='*50}")
    print(f"✅ 完成：{len(rows)} 筆進 → {len(survivors)} 筆通過篩選 → 輸出前 {len(top)} 名")
    print(f"   淘汰 {rejected} 筆")
    print(f"   結果：{output_csv}")
    print(f"   分類方式：{'AI' if _SETTINGS.get('ANTHROPIC_API_KEY') else '關鍵字（未設定 API key）'}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="創作者雷達 v1.0 — IG/Threads 創作者篩選")
    ap.add_argument("-i", "--input", default="candidates.csv", help="輸入 CSV")
    ap.add_argument("-o", "--output", default="shortlist.csv", help="輸出 CSV")
    args = ap.parse_args()
    run(args.input, args.output)
