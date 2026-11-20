// 對應 pipeline/models.py。改那邊的時候要同步改這裡。

export type Category = "taiwan" | "world" | "finance" | "esports";
export type EditionType = "noon" | "night";
export type Language = "zh-TW" | "zh-CN" | "en" | "ko";
export type EsportsTag =
  | "transfer"
  | "retirement"
  | "tournament"
  | "result"
  | "controversy"
  | "roster"
  | "other";
export type League = "lck" | "lpl" | "international" | "other";

export interface SourceRef {
  name: string;
  url: string;
  published_at: string | null;
  language: Language;
}

export interface NewsEvent {
  id: string;
  category: Category;
  subcategory: string | null;
  importance: number; // 1～5
  esports_tags: EsportsTag[];
  league: League | null;
  region_tags: string[];
  title_zh: string;
  summary_zh: string;
  why_it_matters: string | null;
  original_title: string | null;
  published_at: string | null;
  ai_processed: boolean;
  sources: SourceRef[];
}

export interface MarketQuote {
  symbol: string;
  name: string;
  price: number | null;
  change: number | null;
  change_pct: number | null;
  currency: string | null;
  unit: string | null;
  decimals: number;
  as_of: string | null;
  date_only: boolean;
  source: string | null;
}

export interface ProviderUsage {
  provider: string;
  model: string;
  calls: number;
  input_tokens: number;
  output_tokens: number;
}

export interface LLMUsage {
  provider: string | null;
  model: string | null;
  input_tokens: number;
  output_tokens: number;
  calls: number;
  degraded: boolean;
  breakdown: ProviderUsage[];
  errors: string[];
}

export interface Edition {
  edition_id: string;
  edition_type: EditionType;
  published_at: string;
  window: { from: string; to: string };
  briefing: string[];
  headlines: string[];
  markets: MarketQuote[];
  events: NewsEvent[];
  llm_usage: LLMUsage;
  source_errors: { source_id: string; error: string }[];
}

export interface EditionIndexEntry {
  edition_id: string;
  edition_type: EditionType;
  published_at: string;
  event_count: number;
  degraded: boolean;
}
