# 個人每日新聞儀表板

開發中，完整說明會在 M7 補上。規格見 [SPEC.md](SPEC.md)，設計決策見 [DECISIONS.md](DECISIONS.md)。

## 目前可以跑的

```bash
uv sync
uv run python scripts/check_sources.py --samples 3   # 新聞來源健康檢查
uv run python -m pipeline.main --edition noon        # 產生一刊（目前一律是降級版）
uv run pytest
```
