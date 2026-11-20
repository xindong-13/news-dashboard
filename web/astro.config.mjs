// @ts-check
import { defineConfig } from "astro/config";

// 所有頁面都放在根目錄、用相對路徑互連（index.html、archive.html、2026-11-21-noon.html…），
// 這樣部署在 GitHub Pages 的子路徑（/repo/）或任何靜態主機都不用另外設定 base。
export default defineConfig({
  site: process.env.SITE_URL || undefined,
  build: {
    format: "file",
    inlineStylesheets: "always",
  },
  trailingSlash: "ignore",
});
