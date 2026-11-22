// 產生「加到主畫面」用的圖示（public/icons/*.png）。改了圖示設計再跑一次：
//   node scripts/make-icons.mjs
// 圖案跟 public/favicon.svg 一樣（藍底、四條白線）。
import sharp from "sharp";
import { mkdirSync } from "node:fs";

const lines = '<path d="M8 9h11v2H8zm0 5h16v2H8zm0 5h16v2H8zm0 5h10v2H8z" fill="#fff"/>';
// 一般圖示：圓角
const rounded = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="7" fill="#1f6feb"/>${lines}</svg>`;
// 滿版圖示（Android 自適應圖示、iPhone）：系統會自己裁成圓形或圓角，圖案要縮在中間 60% 的安全範圍內
const fullBleed = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" fill="#1f6feb"/><g transform="translate(16 16) scale(0.72) translate(-16 -17.5)">${lines}</g></svg>`;

mkdirSync("public/icons", { recursive: true });
const jobs = [
  [rounded, 192, "icon-192.png"],
  [rounded, 512, "icon-512.png"],
  [fullBleed, 512, "maskable-512.png"],
  [fullBleed, 180, "apple-touch-icon.png"],
];
for (const [svg, size, name] of jobs) {
  await sharp(Buffer.from(svg), { density: (72 * size) / 32 }).resize(size, size).png().toFile(`public/icons/${name}`);
  console.log(`public/icons/${name}`);
}
