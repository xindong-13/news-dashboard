// 建置時讀 ../data 裡的 JSON。網站本身沒有後端，所有頁面都在建置時產生。
import fs from "node:fs";
import path from "node:path";
import type { Category, Edition, EditionIndexEntry, League, NewsEvent } from "../types";

const DATA_DIR = process.env.DATA_DIR
  ? path.resolve(process.env.DATA_DIR)
  : path.resolve(process.cwd(), "..", "data");

let cache: Edition[] | null = null;

/** 全部的刊，新的在前。 */
export function loadEditions(): Edition[] {
  if (cache) return cache;
  const dir = path.join(DATA_DIR, "editions");
  const files = fs.existsSync(dir) ? fs.readdirSync(dir).filter((f) => f.endsWith(".json")) : [];
  cache = files
    .map((f) => JSON.parse(fs.readFileSync(path.join(dir, f), "utf-8")) as Edition)
    .sort((a, b) => b.published_at.localeCompare(a.published_at));
  return cache;
}

export function indexEntries(): EditionIndexEntry[] {
  return loadEditions().map((e) => ({
    edition_id: e.edition_id,
    edition_type: e.edition_type,
    published_at: e.published_at,
    event_count: e.events.length,
    degraded: e.llm_usage.degraded,
  }));
}

// ---------------------------------------------------------------------------
// 顯示用的名稱
// ---------------------------------------------------------------------------

export const CATEGORIES: { id: Category; label: string }[] = [
  { id: "taiwan", label: "台灣" },
  { id: "world", label: "國際" },
  { id: "finance", label: "財經" },
  { id: "esports", label: "電競" },
  { id: "threads", label: "Threads 熱議" },
];

export const SUBCATEGORY_LABELS: Record<string, string> = {
  politics: "政治",
  society: "社會",
  cross_strait: "兩岸",
  conflict: "衝突",
  disaster: "災害",
  tech: "科技",
  tw_stock: "台股",
  us_stock: "美股",
  macro: "總經",
  forex: "匯市",
  industry: "產業",
  crypto: "加密貨幣",
  lol: "LoL",
  valorant: "Valorant",
  cs2: "CS2",
  other: "其他",
};

export const ESPORTS_TAG_LABELS: Record<string, string> = {
  transfer: "轉會",
  roster: "陣容",
  retirement: "退役",
  tournament: "大賽",
  result: "賽果",
  controversy: "爭議",
  other: "其他",
};

export const LEAGUE_LABELS: Record<League, string> = {
  lck: "LCK",
  lpl: "LPL",
  international: "國際賽",
  other: "其他賽區",
};

const LEAGUE_ORDER: Record<string, number> = { lck: 0, lpl: 1, international: 2, other: 3 };

// ---------------------------------------------------------------------------
// 排序
// ---------------------------------------------------------------------------

function timeOf(e: NewsEvent): number {
  return e.published_at ? Date.parse(e.published_at) : 0;
}

/** 一般分類：重要度高的在前，同重要度新的在前。 */
export function sortEvents(events: NewsEvent[]): NewsEvent[] {
  return [...events].sort((a, b) => b.importance - a.importance || timeOf(b) - timeOf(a));
}

/** 電競：LCK → LPL → 國際賽 → 其他（含 Valorant、CS2），組內依重要度（DECISIONS.md）。 */
export function sortEsports(events: NewsEvent[]): NewsEvent[] {
  const rank = (e: NewsEvent) =>
    e.subcategory === "lol" || e.subcategory == null ? (LEAGUE_ORDER[e.league ?? "other"] ?? 3) : 3;
  return [...events].sort(
    (a, b) => rank(a) - rank(b) || b.importance - a.importance || timeOf(b) - timeOf(a),
  );
}

// ---------------------------------------------------------------------------
// 日期
// ---------------------------------------------------------------------------

const TZ = "Asia/Taipei";

export function fmtDate(iso: string): string {
  const d = new Date(iso);
  const md = new Intl.DateTimeFormat("zh-TW", { timeZone: TZ, month: "numeric", day: "numeric" }).format(d);
  const wd = new Intl.DateTimeFormat("zh-TW", { timeZone: TZ, weekday: "narrow" }).format(d);
  return `${md}（${wd}）`;
}

export function fmtTime(iso: string): string {
  return new Intl.DateTimeFormat("zh-TW", {
    timeZone: TZ,
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(iso));
}

export function fmtDateTime(iso: string): string {
  const d = new Date(iso);
  const md = new Intl.DateTimeFormat("zh-TW", { timeZone: TZ, month: "numeric", day: "numeric" }).format(d);
  return `${md} ${fmtTime(iso)}`;
}

/** 台北時間的 YYYY-MM-DD */
export function dayKey(iso: string): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: TZ }).format(new Date(iso));
}

export function editionLabel(type: string): string {
  return type === "noon" ? "午報" : "晚報";
}

export function editionHref(id: string): string {
  return `${id}.html`;
}
