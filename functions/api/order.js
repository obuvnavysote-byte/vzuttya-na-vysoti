// Оформлення замовлення «Взуття на Висоті» (Cloudflare Pages Function).
// Секрети задаються в Cloudflare: Settings -> Variables and Secrets
//   TG_TOKEN    — токен Telegram-бота від @BotFather
//   TG_CHAT_ID  — ваш Id з @userinfobot
//   MONO_TOKEN  — токен інтернет-еквайрингу monobank (необовʼязково: без нього
//                 замовлення просто приходить у Telegram, а оплату ви надсилаєте самі)
//   CHAST_STORE_ID — Store ID «Покупки частинами» monobank
//   CHAST_SECRET   — секретний ключ (signKey) «Покупки частинами» monobank

const PREPAY = 200;
const PARTS = [3];
const CHAST_API = "https://u2.monobank.com.ua";
const PAY_LABEL = {
  full: "повна оплата онлайн",
  prepay: `післяплата, передоплата ${PREPAY} ₴`,
  parts: "оплата частинами monobank",
};

export async function onRequestPost({ request, env }) {
  const url = new URL(request.url);
  if (url.searchParams.get("hook") === "mono") return monoHook(request, env);
  if (url.searchParams.get("hook") === "chast") return chastHook(request, env, url);
  if (url.searchParams.get("admin")) return chastAdminAction(request, env, url);

  let body;
  try { body = await request.json(); } catch { return json({ error: "Некоректні дані замовлення." }, 400); }

  const name = clean(body.name, 80);
  const phone = clean(body.phone, 30);
  const city = clean(body.city, 160);
  const pay = PAY_LABEL[body.pay] ? body.pay : "prepay";
  if (!name || phone.replace(/\D/g, "").length < 10 || !city) {
    return json({ error: "Вкажіть імʼя, телефон і місто з відділенням Нової Пошти." }, 400);
  }
  const items = Array.isArray(body.items) ? body.items.slice(0, 20) : [];
  if (!items.length) return json({ error: "Кошик порожній." }, 400);

  // Ціни й наявність беремо з опублікованого каталогу, а не з браузера покупця.
  const page = await (await env.ASSETS.fetch(new URL("/", url))).text();
  const lines = [];
  const goods = [];
  let total = 0;
  for (const it of items) {
    const p = findProduct(page, String(it.id));
    const size = parseInt(it.size, 10);
    const qty = Math.min(Math.max(parseInt(it.qty, 10) || 1, 1), 5);
    if (!p) return json({ error: "Однієї з моделей уже немає в каталозі. Оновіть сторінку." }, 409);
    const z = (p.z || []).find((z) => z[0] === size);
    if (!z || !z[2]) return json({ error: `Розміру ${size} моделі «${p.n}» уже немає в наявності. Приберіть її з кошика.` }, 409);
    total += p.p * qty;
    for (let k = 0; k < qty; k++) goods.push({ name: `${[p.n, p.c].filter(Boolean).join(", ")}, р. ${size}`.slice(0, 120), count: 1, sum: p.p });
    lines.push(`• ${[p.n, p.c].filter(Boolean).join(", ")}, р. ${size}, ${qty} шт., ${money(p.p * qty)} (арт. ${p.a})`);
  }

  const order = orderNo();
  const toPay = pay === "full" ? total : pay === "prepay" ? Math.min(PREPAY, total) : 0;

  let payUrl = "";
  if (toPay > 0 && env.MONO_TOKEN) {
    try {
      const r = await fetch("https://api.monobank.ua/api/merchant/invoice/create", {
        method: "POST",
        headers: { "X-Token": env.MONO_TOKEN, "Content-Type": "application/json" },
        body: JSON.stringify({
          amount: Math.round(toPay * 100),
          ccy: 980,
          merchantPaymInfo: {
            reference: order,
            destination: pay === "full" ? `Оплата замовлення ${order}` : `Передоплата за замовлення ${order}`,
          },
          redirectUrl: `${url.origin}/?order=${order}`,
          webHookUrl: `${url.origin}/api/order?hook=mono`,
          validity: 3600,
        }),
      });
      const d = await r.json().catch(() => ({}));
      if (r.ok && d.pageUrl) payUrl = d.pageUrl;
    } catch (_) { /* якщо monobank недоступний, замовлення все одно приходить у Telegram */ }
  }

  // Покупка частинами: створюємо заявку, клієнт підтверджує її в застосунку monobank.
  let chast = null;
  if (pay === "parts" && env.CHAST_STORE_ID && env.CHAST_SECRET) {
    const r = await chastCall(env, "/api/order/create", {
      store_order_id: order,
      client_phone: intlPhone(phone),
      total_sum: Math.round(total * 100) / 100,
      invoice: { number: order, date: kyivDate(), source: "INTERNET" },
      available_programs: [{ available_parts_count: PARTS }],
      products: goods,
      result_callback: `${url.origin}/api/order?hook=chast&order=${order}`,
    });
    if (r.ok && r.data.order_id) {
      chast = r.data.order_id;
    } else {
      await telegram(env, [
        `⚠️ Не вдалося створити заявку на покупку частинами ${order}`,
        `Відповідь monobank: ${r.data.message || r.status}`,
        "", ...lines, "", `Разом: ${money(total)}`, `👤 ${name}`, `📞 ${phone}`, `📦 ${city}`,
      ].join("\n"));
      return json({ error: "monobank не прийняв заявку на покупку частинами. Перевірте, що вказано номер телефону, до якого привʼязаний monobank, і що ліміту достатньо. Або оберіть іншу оплату." }, 409);
    }
  }

  const status = payUrl ? `⏳ очікує оплати ${money(toPay)}`
    : toPay > 0 ? `💳 треба надіслати посилання на оплату ${money(toPay)}`
    : chast ? `📲 заявку надіслано клієнту в застосунок monobank, чекаємо підтвердження\nКерувати заявкою: ${await adminLink(env, url, chast, order)}`
    : "📞 звʼязатися щодо оплати частинами";
  const text = [
    `🛍 Нове замовлення ${order}`,
    "",
    ...lines,
    "",
    `Разом: ${money(total)}`,
    `Оплата: ${PAY_LABEL[pay]}`,
    `Статус: ${status}`,
    "",
    `👤 ${name}`,
    `📞 ${phone}`,
    `📦 ${city}`,
  ].join("\n");
  const sent = await telegram(env, text);
  if (!sent && !payUrl) {
    return json({ error: "Не вдалося надіслати замовлення. Напишіть нам, будь ласка, у Viber або Telegram." }, 502);
  }

  const message = toPay > 0
    ? "Ми перевіримо наявність і надішлемо вам посилання на оплату найближчим часом."
    : chast
      ? `Ми надіслали заявку в monobank. Відкрийте застосунок monobank і підтвердьте покупку частинами (${PARTS[0]} платежі по ${money(Math.ceil(total / PARTS[0]))}).`
      : "Ми звʼяжемося з вами найближчим часом і допоможемо оформити покупку частинами.";
  return json({ ok: true, order, payUrl, message });
}

// monobank повідомляє про зміну статусу. Статус перепитуємо в банку з нашим токеном,
// тому підробити «оплачено» неможливо.
async function monoHook(request, env) {
  if (!env.MONO_TOKEN) return new Response("ok");
  const body = await request.json().catch(() => ({}));
  if (!body.invoiceId) return new Response("ok");
  const r = await fetch(`https://api.monobank.ua/api/merchant/invoice/status?invoiceId=${encodeURIComponent(body.invoiceId)}`, {
    headers: { "X-Token": env.MONO_TOKEN },
  });
  const d = await r.json().catch(() => ({}));
  if (d.status === "success") {
    await telegram(env, `✅ Оплачено замовлення ${d.reference || ""}: ${money((d.amount || 0) / 100)}`);
  } else if (d.status === "failure") {
    await telegram(env, `❌ Оплата не пройшла: замовлення ${d.reference || ""}`);
  }
  return new Response("ok");
}

// ---------- Покупка частинами monobank ----------

async function chastCall(env, path, payload) {
  const body = JSON.stringify(payload);
  try {
    const r = await fetch(CHAST_API + path, {
      method: "POST",
      headers: { "store-id": env.CHAST_STORE_ID, signature: await sign(body, env.CHAST_SECRET), "Content-Type": "application/json" },
      body,
    });
    const data = await r.json().catch(() => ({}));
    return { ok: r.ok && data.state !== "FAIL", status: r.status, data };
  } catch (e) {
    return { ok: false, status: 0, data: { message: "monobank недоступний" } };
  }
}

// monobank надсилає сюди зміну статусу заявки. Перевіряємо підпис.
async function chastHook(request, env, url) {
  if (!env.CHAST_SECRET) return new Response("ok");
  const raw = await request.text();
  const sig = request.headers.get("signature") || "";
  if (sig !== (await sign(raw, env.CHAST_SECRET))) return new Response("bad signature", { status: 401 });
  let d = {};
  try { d = JSON.parse(raw); } catch { return new Response("ok"); }
  const order = clean(url.searchParams.get("order"), 20);
  if (d.order_sub_state === "WAITING_FOR_STORE_CONFIRM") {
    await telegram(env, `✅ Клієнт підтвердив покупку частинами ${order}.\nКоли відправите замовлення, підтвердьте видачу товару (тоді monobank перерахує гроші):\n${await adminLink(env, url, d.order_id, order)}`);
  } else if (d.state === "FAIL") {
    await telegram(env, `❌ Покупку частинами ${order} не оформлено (${d.order_sub_state || "відмова"}).`);
  } else if (d.state === "SUCCESS") {
    await telegram(env, `💰 Покупку частинами ${order} завершено, monobank перерахує кошти.`);
  }
  return new Response("ok");
}

// Сторінка для власниці магазину: підтвердити видачу або скасувати заявку.
export async function onRequestGet({ request, env }) {
  const url = new URL(request.url);
  const id = url.searchParams.get("admin");
  if (!id) return json({ ok: true });
  if (!(await adminOk(env, url))) return page("Посилання недійсне.");
  const order = esc(url.searchParams.get("order") || "");
  const st = await chastCall(env, "/api/order/state", { order_id: id });
  const state = st.data.state ? `${st.data.state} / ${st.data.order_sub_state || ""}` : "невідомо";
  return page(`
    <h1>Покупка частинами ${order}</h1>
    <p>Статус у monobank: <b>${esc(state)}</b></p>
    <p>Підтверджуйте видачу, коли замовлення відправлено клієнту. Після цього з клієнта спишеться перший платіж, а monobank перерахує вам кошти.</p>
    <form method="post"><input type="hidden" name="action" value="confirm"><button class="ok">Підтвердити видачу товару</button></form>
    <form method="post" onsubmit="return confirm('Скасувати заявку? Клієнт не зможе нею скористатися.')"><input type="hidden" name="action" value="reject"><button class="no">Скасувати заявку (немає в наявності)</button></form>`);
}

async function chastAdminAction(request, env, url) {
  if (!(await adminOk(env, url))) return page("Посилання недійсне.");
  const form = await request.formData().catch(() => null);
  const action = form && form.get("action");
  const id = url.searchParams.get("admin");
  const order = esc(url.searchParams.get("order") || "");
  if (action !== "confirm" && action !== "reject") return page("Невідома дія.");
  const r = await chastCall(env, action === "confirm" ? "/api/order/confirm" : "/api/order/reject", { order_id: id });
  const done = action === "confirm" ? "Видачу товару підтверджено" : "Заявку скасовано";
  if (r.ok) await telegram(env, `${action === "confirm" ? "📦" : "🚫"} ${done}: ${url.searchParams.get("order") || ""}`);
  return page(r.ok
    ? `<h1>${done}</h1><p>Замовлення ${order}.</p>`
    : `<h1>Не вийшло</h1><p>monobank відповів: ${esc(r.data.message || String(r.status))}</p><p>Можливо, клієнт ще не підтвердив заявку в застосунку.</p>`);
}

async function adminLink(env, url, id, order) {
  return `${url.origin}/api/order?admin=${encodeURIComponent(id)}&order=${encodeURIComponent(order)}&k=${await adminKey(env, id)}`;
}
async function adminKey(env, id) {
  return (await sign("admin:" + id, env.CHAST_SECRET || "")).replace(/[^A-Za-z0-9]/g, "").slice(0, 24);
}
async function adminOk(env, url) {
  const id = url.searchParams.get("admin") || "";
  return !!env.CHAST_SECRET && !!id && url.searchParams.get("k") === (await adminKey(env, id));
}

async function sign(text, secret) {
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey("raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const mac = new Uint8Array(await crypto.subtle.sign("HMAC", key, enc.encode(text)));
  let s = "";
  for (const b of mac) s += String.fromCharCode(b);
  return btoa(s);
}
function intlPhone(p) {
  let d = p.replace(/\D/g, "");
  if (d.length === 10 && d.startsWith("0")) d = "38" + d;
  return "+" + d;
}
function kyivDate() {
  return new Date(Date.now() + 3 * 3600e3).toISOString().slice(0, 10);
}
function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function page(inner) {
  return new Response(`<!doctype html><html lang="uk"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex"><title>Покупка частинами</title>
<style>body{font-family:system-ui,sans-serif;max-width:520px;margin:40px auto;padding:0 16px;color:#2B1D1A;background:#F5EFEC;line-height:1.5}h1{font-size:24px}form{margin:12px 0}button{width:100%;min-height:52px;border:0;border-radius:999px;font-size:17px;font-weight:600;color:#fff;cursor:pointer}.ok{background:#5B1A2B}.no{background:#6B5B55}</style></head><body>${inner}</body></html>`,
    { headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" } });
}

function findProduct(page, id) {
  const start = page.indexOf(`{"i":${JSON.stringify(id)},`);
  if (start < 0) return null;
  let depth = 0, inStr = false;
  for (let i = start; i < page.length; i++) {
    const ch = page[i];
    if (inStr) {
      if (ch === "\\") i++;
      else if (ch === '"') inStr = false;
    } else if (ch === '"') inStr = true;
    else if (ch === "{") depth++;
    else if (ch === "}" && --depth === 0) {
      try { return JSON.parse(page.slice(start, i + 1)); } catch { return null; }
    }
  }
  return null;
}

async function telegram(env, text) {
  if (!env.TG_TOKEN || !env.TG_CHAT_ID) return false;
  try {
    const r = await fetch(`https://api.telegram.org/bot${env.TG_TOKEN}/sendMessage`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_id: env.TG_CHAT_ID, text, disable_web_page_preview: true }),
    });
    return r.ok;
  } catch { return false; }
}

function clean(v, max) {
  return String(v || "").replace(/[\u0000-\u001f]/g, " ").trim().slice(0, max);
}
function money(n) {
  return Math.round(n).toLocaleString("uk-UA").replace(/ /g, " ") + " ₴";
}
function orderNo() {
  const d = new Date(Date.now() + 3 * 3600e3);
  const p = (x) => String(x).padStart(2, "0");
  return `VN-${p(d.getUTCDate())}${p(d.getUTCMonth() + 1)}-${Math.floor(1000 + Math.random() * 9000)}`;
}
function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json; charset=utf-8" } });
}
