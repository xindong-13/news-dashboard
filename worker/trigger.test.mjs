// 執行：node --test "worker/*.test.mjs"
import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";

import worker from "./trigger.mjs";

const ENV = {
  GITHUB_TOKEN: "gh-secret",
  LINE_CHANNEL_ACCESS_TOKEN: "line-secret",
  LINE_USER_ID: "U1",
  HEALTHCHECKS_URL: "https://hc-ping.com/abc",
  RETRY_MS: 0,
};

const realFetch = globalThis.fetch;
let calls;
let answers; // 網址片段 → (請求) => Response；後加的優先

beforeEach(() => {
  calls = [];
  answers = [
    ["/dispatches", () => new Response("{}", { status: 200 })],
    ["workflows/edition.yml", () => Response.json({ state: "active" })],
    ["github.io", () => new Response("", { status: 200 })],
    ["raw.githubusercontent", () => new Response("", { status: 404 })],
    ["api.line.me", () => Response.json({})],
    ["hc-ping.com", () => new Response("OK")],
  ];
  globalThis.fetch = async (url, init = {}) => {
    const call = { url: String(url), ...init, body: init.body };
    calls.push(call);
    // 較長的片段先比對，/dispatches 才不會被 workflows/edition.yml 搶走
    const hit = [...answers].reverse().sort((a, b) => Number(b[0] === "/dispatches") - Number(a[0] === "/dispatches"));
    for (const [part, fn] of hit) if (call.url.includes(part)) return fn(call);
    throw new Error(`沒有預期的請求：${call.url}`);
  };
});
afterEach(() => {
  globalThis.fetch = realFetch;
});

const answer = (part, fn) => answers.push([part, fn]);
const status = (code) => () => new Response("", { status: code });
const to = (part) => calls.filter((c) => c.url.includes(part));
const run = (taipeiTime, env = ENV) =>
  worker.scheduled({ scheduledTime: Date.parse(`${taipeiTime}:00+08:00`), cron: "" }, env);
const lineText = () => JSON.parse(to("api.line.me").at(-1).body).messages[0].text;

for (const [time, target] of [
  ["2026-11-24T11:50", "2026-11-24-noon"],
  ["2026-11-24T12:05", "2026-11-24-noon"],
  ["2026-11-24T20:50", "2026-11-24-night"],
  ["2026-11-24T21:05", "2026-11-24-night"],
  ["2026-12-31T20:50", "2026-12-31-night"],
]) {
  test(`${time} 觸發 ${target}`, async () => {
    await run(time);
    assert.equal(calls.length, 1);
    assert.match(calls[0].url, /news-dashboard\/actions\/workflows\/edition\.yml\/dispatches$/);
    assert.equal(calls[0].method, "POST");
    assert.equal(calls[0].headers.authorization, "Bearer gh-secret");
    assert.ok(calls[0].headers["user-agent"]);
    assert.deepEqual(JSON.parse(calls[0].body), { ref: "main", inputs: { target } });
  });
}

test("觸發遇到 5xx 或斷線會重試", async () => {
  let n = 0;
  answer("/dispatches", () => {
    n += 1;
    if (n === 1) throw new TypeError("network");
    return new Response(null, { status: n === 2 ? 502 : 204 });
  });
  await run("2026-11-24T11:50");
  assert.equal(to("/dispatches").length, 3);
});

test("觸發被拒絕（權杖過期）：不重試、不通知，交給 12:30 的檢查", async () => {
  answer("/dispatches", status(401));
  await assert.rejects(run("2026-11-24T11:50"), /401/);
  assert.equal(calls.length, 1);
});

test("已正常出刊：不傳 LINE，只回報檢查有跑", async () => {
  await run("2026-11-24T12:30");
  assert.match(to("github.io")[0].url, /\/2026-11-24-noon\.html\?/);
  assert.equal(to("api.line.me").length, 0);
  assert.deepEqual(to("hc-ping.com").map((c) => c.url), ["https://hc-ping.com/abc"]);
});

test("漏刊：傳一則 LINE", async () => {
  answer("github.io", status(404));
  await run("2026-11-24T21:30");
  assert.equal(to("api.line.me").length, 1);
  assert.match(lineText(), /11\/24 晚報還沒出現/);
  assert.equal(to("hc-ping.com")[0].url, "https://hc-ping.com/abc");
});

test("檢查被重複執行：同一刊的 LINE 重試鍵相同，不同刊不同", async () => {
  answer("github.io", status(404));
  await run("2026-11-24T12:30");
  await run("2026-11-24T12:30");
  await run("2026-11-24T21:30");
  await run("2026-11-25T12:30");
  const keys = to("api.line.me").map((c) => c.headers["x-line-retry-key"]);
  assert.match(keys[0], /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  assert.equal(keys[0], keys[1]);
  assert.equal(new Set(keys).size, 3);
});

test("LINE 回 409（已經送過）算成功", async () => {
  answer("github.io", status(404));
  answer("api.line.me", status(409));
  await run("2026-11-24T12:30");
  assert.equal(to("hc-ping.com")[0].url, "https://hc-ping.com/abc");
});

test("LINE 傳不出去：改讓 Healthchecks 寄信", async () => {
  answer("github.io", status(404));
  answer("api.line.me", status(429));
  await assert.rejects(run("2026-11-24T12:30"), /429/);
  assert.deepEqual(to("hc-ping.com").map((c) => c.url), ["https://hc-ping.com/abc/fail"]);
});

for (const [name, setup, expected] of [
  ["權杖過期", () => answer("workflows/edition.yml", status(401)), /權杖失效/],
  ["workflow 停用", () => answer("workflows/edition.yml", () => Response.json({ state: "disabled_manually" })), /被停用/],
  ["GitHub 連不上", () => answer("workflows/edition.yml", status(503)), /連不上 GitHub/],
  ["有內容但沒部署", () => answer("raw.githubusercontent", status(200)), /網站沒有更新成功/],
  ["其他", () => {}, /可能還在跑/],
]) {
  test(`通知寫出可能原因：${name}`, async () => {
    answer("github.io", status(404));
    setup();
    await run("2026-11-24T12:30");
    assert.match(lineText(), expected);
  });
}

test("網站暫時連不上、但內容已產生：不誤報漏刊", async () => {
  answer("github.io", status(503));
  answer("raw.githubusercontent", status(200));
  await run("2026-11-24T12:30");
  assert.equal(to("api.line.me").length, 0);
  assert.equal(to("hc-ping.com")[0].url, "https://hc-ping.com/abc/fail");
});

test("網站一次失敗後恢復：不誤報", async () => {
  let n = 0;
  answer("github.io", () => new Response("", { status: n++ ? 200 : 503 }));
  await run("2026-11-24T12:30");
  assert.equal(to("api.line.me").length, 0);
  assert.equal(to("hc-ping.com")[0].url, "https://hc-ping.com/abc");
});

test("網站連不上、內容也確定不存在：算漏刊", async () => {
  answer("github.io", status(503));
  await run("2026-11-24T12:30");
  assert.equal(to("api.line.me").length, 1);
});

test("Healthchecks 沒設定或連不上都不影響通知", async () => {
  answer("github.io", status(404));
  await run("2026-11-24T12:30", { ...ENV, HEALTHCHECKS_URL: undefined });
  answer("hc-ping.com", status(500));
  await run("2026-11-24T21:30");
  assert.equal(to("api.line.me").length, 2);
});

test("其他時間只做設定檢查，不會出刊", async () => {
  answer("/dispatches", status(422));
  await run("2026-11-24T00:20");
  const bodies = to("/dispatches").map((c) => JSON.parse(c.body));
  assert.deepEqual(bodies, [{ ref: "__permission-check__" }]);
  assert.match(lineText(), /✅ GitHub 權杖可用/);
  assert.match(lineText(), /✅ 「出刊」已啟用/);
});

test("設定檢查：權杖權限不足時說清楚", async () => {
  answer("/dispatches", status(403));
  await run("2026-11-24T00:20");
  assert.match(lineText(), /❌ GitHub 權杖有問題（HTTP 403）/);
});

test("LINE 與網址裡不會出現金鑰", async () => {
  answer("github.io", status(404));
  await run("2026-11-24T12:30");
  for (const c of calls) {
    assert.ok(!c.url.includes("secret"));
    assert.ok(!String(c.body ?? "").includes("secret"));
  }
});
