# 創作者雷達 Creator Radar v1.0

把一批「已合法取得」的 IG/Threads 創作者候選資料，自動算互動率、依指標篩選、
用 AI 判斷品牌調性，產出排序好的合作名單。

## ⚠️ 先讀這段：這工具做什麼、不做什麼

**做**：拿到候選帳號資料「之後」的篩選與判斷自動化。
**不做**：不爬 IG/Threads。Meta 禁止未授權爬取，帳號封鎖與法律風險由你自負。

輸入資料（`candidates.csv`）請自行透過合法管道取得：
- 官方/授權的第三方 creator discovery 平台 API（Modash、Phyllo、HypeAuditor 等，付費）
- 手動整理

## 安裝

只用 Python 標準函式庫，**不需 pip install 任何套件**。需要 Python 3.9+。

```bash
# 1. 進資料夾
cd creator-radar

# 2.（可選）啟用 AI 調性分類：複製設定範本並填入 Claude API key
cp settings.json.example settings.json
# 編輯 settings.json，填入 sk-ant-... 開頭的金鑰
# 金鑰申請：https://console.anthropic.com/
# 不填也能跑，會自動改用關鍵字評分（免費但較粗略）
```

## 使用

```bash
python3 creator_radar.py -i candidates.csv -o shortlist.csv
```

## 輸入 CSV 欄位

必要：`username`, `followers`
建議：`avg_likes`, `avg_comments`, `days_since_last_post`, `bio`, `recent_captions`, `profile_url`
若已有現成的 `engagement_rate` 欄位（%），會優先採用，不再自行計算。

缺欄位不會當掉——算不出互動率的帳號會被歸類為「資料不足」淘汰。

## 篩選邏輯（兩層）

1. **硬指標**：粉絲數區間、互動率下限、近期是否活躍。參數都在 `creator_radar.py` 最上方，附註解可自行調整。
2. **AI 調性分類**：把 bio 和近期貼文丟給 Claude，判斷與品牌調性契合度（0-100 分）。無 API key 時降級為關鍵字評分。

**綜合分 = 調性契合 70% + 互動率 30%**，由高到低排序輸出前 30 名。

## 針對不同客戶調整

改 `creator_radar.py` 頂部的 `BRAND_PROFILE` 那段文字，描述客戶品牌的調性，AI 就會依新標準判斷。這是每接一個新品牌唯一需要改的地方。
