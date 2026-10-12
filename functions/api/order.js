// Оформлення замовлення «Взуття на Висоті» (Cloudflare Pages Function).
// Секрети задаються в Cloudflare: Settings -> Variables and Secrets
//   TG_TOKEN    — токен Telegram-бота від @BotFather
//   TG_CHAT_ID  — ваш Id з @userinfobot
//   MONO_TOKEN  — токен інтернет-еквайрингу monobank

const PREPAY = 200;
const PAY_LABEL = {
  full: "повна оплата онлайн",
  prepay: `післяплата, передоплата ${PREPAY} ₴`,
  parts: "оплата частинами monobank",
};

export async function onRequestPost({ request, env }) {
  const url = new URL(request.url);
  if (url.searchParams.get("hook") === "mono") return monoHook(request, env);

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
  let total = 0;
  for (const it of items) {
    const p = findProduct(page, String(it.id));
    const size = parseInt(it.size, 10);
    const qty = Math.min(Math.max(parseInt(it.qty, 10) || 1, 1), 5);
    if (!p) return json({ error: "Однієї з моделей уже немає в каталозі. Оновіть сторінку." }, 409);
    const z = (p.z || []).find((z) => z[0] === size);
    if (!z || !z[2]) return json({ error: `Розміру ${size} моделі «${p.n}» уже немає в наявності. Приберіть її з кошика.` }, 409);
    total += p.p * qty;
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

  const status = payUrl ? `⏳ очікує оплати ${money(toPay)}` : toPay > 0 ? `💳 треба надіслати посилання на оплату ${money(toPay)}` : "📞 звʼязатися щодо оплати частинами";
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
    return json({ error: "Не вдалося надіслати замовлення. Напишіть нам, будь ласка, у Viber або Telegram."
