// 搜尋索引，依月份切檔（search/2026-11.json），避免一年後單一檔案太大。
import type { APIRoute } from "astro";
import { CATEGORIES, loadEditions } from "../../lib/data";

export function getStaticPaths() {
  const months = new Set(loadEditions().map((e) => e.edition_id.slice(0, 7)));
  return [...months].map((month) => ({ params: { month } }));
}

export const GET: APIRoute = ({ params }) => {
  const rows = [];
  for (const ed of loadEditions()) {
    if (ed.edition_id.slice(0, 7) !== params.month) continue;
    for (const e of ed.events) {
      rows.push({
        ed: ed.edition_id,
        cat: CATEGORIES.find((c) => c.id === e.category)?.id ?? e.category,
        imp: e.importance,
        t: e.title_zh,
        o: e.original_title && e.original_title !== e.title_zh ? e.original_title : undefined,
        s: e.summary_zh || undefined,
        at: e.published_at ?? ed.published_at,
        src: e.sources[0]?.name,
        url: e.sources[0]?.url,
        n: e.sources.length,
      });
    }
  }
  return new Response(JSON.stringify(rows), { headers: { "Content-Type": "application/json" } });
};
