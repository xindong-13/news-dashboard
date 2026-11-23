// 準時觸發出刊＋漏刊通知（Cloudflare Worker，免費方案）。
// 設定步驟見 README「3-2. 準時出刊與漏刊通知」。整個檔案貼進 Cloudflare 後台即可，不需要安裝套件。
//
// 排程（Cloudflare 用 UTC，台北＝UTC+8）：
//   50 3,12 * * *   台北 11:50、20:50  觸發出刊
//   5 4,13 * * *    台北 12:05、21:05  再觸發一次（已經出過的話，出刊流程自己會略過）
//   30 4,13 * * *   台北 12:30、21:30  檢查網站上有沒有這一刊，沒有就傳一次 LINE
// 其他時間被叫到時只做「設定檢查」，不會出刊。
//
// 金鑰放 Cloudflare 的 Secret，不要寫在這裡（repo 是公開的）：
//   GITHUB_TOKEN               fine-grained token，只給這個 repo 的 Actions: Read and write
//   LINE_CHANNEL_ACCESS_TOKEN、LINE_USER_ID
//   HEALTHCHECKS_URL           Healthchecks.io 的 ping 網址（選用）：檢查沒跑或 LINE 傳不出去時寄 email

const REPO = "xindong-13/news-dashboard";
const BRANCH = "main";
const SITE = "https://xindong-13.github.io/news-dashboard/";
const ACTIONS_URL = `https://github.com/${REPO}/actions/workflows/edition.yml`;
const WORKFLOW_API = `https://api.github.com/repos/${REPO}/actions/workflows/edition.yml`;

// 台北時間 → [刊別, 動作]
const SCHEDULE = {
  "11:50": ["noon", "trigger"],
  "12:05": ["noon", "trigger"],
  "12:30": ["noon", "check"],
  "20:50": ["night", "trigger"],
  "21:05": ["night", "trigger"],
  "21:30": ["night", "check"],
};

const ATTEMPTS = 3;
const RETRY_MS = 10_000;

/** 台北的日期與時分。台灣沒有日光節約時間，直接加 8 小時。 */
function taipei(ms) {
  const iso = new Date(ms + 8 * 3600_000).toISOString();
  return { date: iso.slice(0, 10), clock: iso.slice(11, 16) };
}

function title(target) {
  const [, m, d, kind] = target.match(/^\d{4}-(\d\d)-(\d\d)-(noon|night)$/);
  return `${Number(m)}/${Number(d)} ${kind === "noon" ? "午報" : "晚報"}`;
}

/** 連線失敗、逾時或 5xx 會重試；其他狀態碼直接回傳。全部失敗才丟例外。 */
async function request(url, init, env) {
  let error;
  for (let i = 0; i < ATTEMPTS; i++) {
    if (i > 0) await new Promise((r) => setTimeout(r, env.RETRY_MS ?? RETRY_MS));
    try {
      const res = await fetch(url, { ...init, signal: AbortSignal.timeout(20_000) });
      if (res.status < 500) return res;
      error = new Error(`HTTP ${res.status}`);
    } catch (e) {
      error = e;
    }
  }
  throw error;
}

function github(path, env, body) {
  return request(
    WORKFLOW_API + path,
    {
      method: body ? "POST" : "GET",
      headers: {
        accept: "application/vnd.github+json",
        authorization: `Bearer ${env.GITHUB_TOKEN}`,
        "user-agent": "edition-trigger",
        "x-github-api-version": "2022-11-28",
      },
      body: body && JSON.stringify(body),
    },
    env,
  );
}

/** 用固定字串算出固定的 UUID：同一刊的通知就算送好幾次，LINE 也只會發一則。 */
async function retryKey(text) {
  const hash = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text)));
  hash[6] = (hash[6] & 0x0f) | 0x40;
  hash[8] = (hash[8] & 0x3f) | 0x80;
  const hex = [...hash.slice(0, 16)].map((b) => b.toString(16).padStart(2, "0")).join("");
  return hex.replace(/^(.{8})(.{4})(.{4})(.{4})(.{12})$/, "$1-$2-$3-$4-$5");
}

async function sendLine(text, key, env) {
  const res = await request(
    "https://api.line.me/v2/bot/message/push",
    {
      method: "POST",
      headers: {
        authorization: `Bearer ${env.LINE_CHANNEL_ACCESS_TOKEN}`,
        "content-type": "application/json",
        "x-line-retry-key": await retryKey(key),
      },
      body: JSON.stringify({ to: env.LINE_USER_ID, messages: [{ type: "text", text }] }),
    },
    env,
  );
  // 409＝同一則已經送過了
  if (res.status !== 200 && res.status !== 409) throw new Error(`LINE 傳送失敗：HTTP ${res.status}`);
}

/** 回報給 Healthchecks.io。失敗只記錄，不影響其他動作。 */
async function ping(env, problem) {
  if (!env.HEALTHCHECKS_URL) return;
  try {
    const url = env.HEALTHCHECKS_URL.trim().replace(/\/$/, "") + (problem ? "/fail" : "");
    const res = await request(url, { method: "POST", body: problem ?? "OK" }, env);
    if (!res.ok) console.error(`Healthchecks 回報失敗：HTTP ${res.status}`);
  } catch (e) {
    console.error(`Healthchecks 回報失敗：${e.message}`);
  }
}

async function trigger(target, env) {
  const res = await github("/dispatches", env, { ref: BRANCH, inputs: { target } });
  if (!res.ok) throw new Error(`觸發 ${target} 失敗：HTTP ${res.status} ${await res.text()}`);
  console.log(`已觸發 ${target}`);
}

/** true＝有、false＝沒有（404）、null＝連不上，無法判斷。 */
async function exists(url, env) {
  try {
    const res = await request(`${url}?check=${Date.now()}`, {}, env); // 加參數避開快取
    return res.status === 200 ? true : res.status === 404 ? false : null;
  } catch {
    return null;
  }
}

/** 猜漏刊的原因，寫進通知裡。 */
async function likelyCause(inRepo, env) {
  if (inRepo) return "內容已經產生，但網站沒有更新成功。";
  try {
    const res = await github("", env);
    if (res.status === 401) return "GitHub 權杖失效或過期了，要換一把新的。";
    if (res.ok && (await res.json()).state !== "active") return "「出刊」被停用了，要到 Actions 重新啟用。";
  } catch {
    return "連不上 GitHub，可能是 GitHub 當機。";
  }
  return "可能還在跑，或執行失敗了。";
}

async function check(target, env) {
  const onSite = await exists(`${SITE}${target}.html`, env);
  if (onSite) {
    console.log(`${target} 已經在網站上`);
    return ping(env);
  }
  const inRepo = await exists(
    `https://raw.githubusercontent.com/${REPO}/${BRANCH}/data/editions/${target}.json`,
    env,
  );
  // 網站連不上、內容也不確定缺不缺：不能確定是漏刊，不傳 LINE，改讓 Healthchecks 寄信
  if (onSite === null && inRepo !== false) return ping(env, `連不上網站，無法確認 ${target} 有沒有出刊`);

  const text = [
    `⚠️ ${title(target)}還沒出現`,
    "",
    await likelyCause(inRepo, env),
    "",
    `查看：${ACTIONS_URL}`,
  ].join("\n");
  await sendLine(text, `missed:${target}`, env);
  console.log(`${target} 漏刊，已通知`);
  return ping(env);
}

/** 設定檢查：不會出刊。用不存在的分支觸發，權限正確時 GitHub 會回 422。 */
async function selfTest(date, hour, env) {
  const probe = await github("/dispatches", env, { ref: "__permission-check__" });
  const workflow = await github("", env);
  const state = workflow.ok ? (await workflow.json()).state : `HTTP ${workflow.status}`;
  const token =
    probe.status === 422 ? "✅ GitHub 權杖可用" : `❌ GitHub 權杖有問題（HTTP ${probe.status}）`;
  const edition = state === "active" ? "✅ 「出刊」已啟用" : `⚠️ 「出刊」目前是 ${state}`;
  await sendLine(
    ["🔧 出刊觸發設定檢查", "", token, edition, "✅ LINE 可用", "", "看到這則就可以刪掉測試用的排程了。"].join("\n"),
    `self-test:${date}T${hour}:${token}:${edition}`, // 一小時內結果相同只傳一次
    env,
  );
  return ping(env);
}

export default {
  async scheduled(controller, env) {
    const { date, clock } = taipei(controller.scheduledTime);
    const [kind, action] = SCHEDULE[clock] ?? [null, "self-test"];
    const target = `${date}-${kind}`;
    try {
      if (action === "trigger") await trigger(target, env);
      else if (action === "check") await check(target, env);
      else await selfTest(date, clock.slice(0, 2), env);
    } catch (e) {
      // 觸發失敗不在這裡通知：12:30／21:30 的檢查會發現漏刊，這樣每刊只會收到一次
      if (action !== "trigger") await ping(env, `${action} ${kind ? target : ""} 失敗：${e.message}`);
      throw e; // 讓 Cloudflare 的紀錄顯示失敗
    }
  },
};
