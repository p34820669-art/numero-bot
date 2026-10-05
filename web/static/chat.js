(() => {
  const $ = (id) => document.getElementById(id);
  const feed = $("feed"), input = $("input"), composer = $("composer");
  const IMAGES = { loader: "/static/loader.svg" };

  // Подарочная ссылка (?gift=КОД) открывается как отдельный человек: свой id на вкладку.
  const giftCode = new URLSearchParams(location.search).get("gift");
  const store = giftCode ? sessionStorage : localStorage;
  let uid = null;
  try { uid = store.getItem("numero_uid"); } catch (e) {}
  if (!uid) {
    uid = "web-" + Math.random().toString(36).slice(2, 10);
    try { store.setItem("numero_uid", uid); } catch (e) {}
  }

  let lastId = 0, typingEl = null, started = false;

  const api = async (path, opts) => {
    const r = await fetch(path, opts);
    if (!r.ok) throw new Error(path + " " + r.status);
    return r.json();
  };
  const post = (path, body) =>
    api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

  const escapeHtml = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  // Telegram-HTML: из всех тегов оставляем только <b> и <i>
  const fmt = (s) => escapeHtml(s).replace(/&lt;(\/?)(b|i)&gt;/g, "<$1$2>");
  const hhmm = (ts) => new Date(ts * 1000).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });

  const toast = (text) => {
    const t = $("toast");
    t.textContent = text; t.hidden = false;
    clearTimeout(toast.h);
    toast.h = setTimeout(() => (t.hidden = true), 3200);
  };

  // Автопрокрутка вниз, пока пользователь сам не листает историю наверх.
  let stick = true;
  feed.addEventListener("scroll", () => {
    stick = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 80;
  });
  const scrollDown = () => { feed.scrollTop = feed.scrollHeight; };
  const scrollIfStuck = () => { if (stick) scrollDown(); };

  function renderKeyboard(buttons) {
    const kb = document.createElement("div");
    kb.className = "kb";
    buttons.forEach((row) => {
      const r = document.createElement("div");
      r.className = "kb__row";
      row.forEach((b) => {
        let el;
        if (b.kind === "url") {
          el = document.createElement("a");
          el.href = b.url; el.target = "_blank"; el.rel = "noopener";
          el.textContent = b.text + " ↗";
        } else {
          el = document.createElement("button");
          el.type = "button";
          el.textContent = b.text;
          if (b.kind === "cb") {
            el.className = "cb";
            el.addEventListener("click", () => {
              kb.classList.add("kb--used");
              el.classList.add("picked");
              send("cb", b.data, b.text);
            });
          } else if (b.kind === "pay") {
            el.className = "pay";
            el.addEventListener("click", () => openPay(Number(b.data), b.amount));
          } else if (b.kind === "share") {
            el.addEventListener("click", () => shareText(b.data));
          }
        }
        r.appendChild(el);
      });
      kb.appendChild(r);
    });
    return kb;
  }

  function addMessage(m) {
    if (m.role === "ctl") {  // команда сервера: стереть сообщения после id
      feed.querySelectorAll(".msg").forEach((el) => {
        if (Number(el.dataset.id) > m.truncate_after) el.remove();
      });
      return;
    }
    const wrap = document.createElement("div");
    wrap.className = "msg msg--" + (m.role === "user" ? "user" : "bot");
    wrap.dataset.id = m.id;
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    if (m.image && IMAGES[m.image]) {
      const img = document.createElement("img");
      img.src = IMAGES[m.image]; img.alt = "";
      img.addEventListener("load", scrollIfStuck);
      bubble.appendChild(img);
    }
    const body = document.createElement("div");
    body.innerHTML = fmt(m.text || "");
    bubble.appendChild(body);
    const time = document.createElement("div");
    time.className = "time";
    time.textContent = hhmm(m.ts);
    bubble.appendChild(time);
    wrap.appendChild(bubble);
    if (m.buttons && m.buttons.length) wrap.appendChild(renderKeyboard(m.buttons));
    feed.insertBefore(wrap, typingEl);
  }

  function setTyping(on) {
    if (on && !typingEl) {
      typingEl = document.createElement("div");
      typingEl.className = "typing";
      typingEl.innerHTML = "<i></i><i></i><i></i>";
      feed.appendChild(typingEl);
      scrollIfStuck();
    } else if (!on && typingEl) {
      typingEl.remove(); typingEl = null;
    }
  }

  let polling = false;
  async function poll() {
    if (polling) return;
    polling = true;
    try {
      const data = await api(`/api/poll?uid=${encodeURIComponent(uid)}&after=${lastId}`);
      if (data.messages.length) {
        data.messages.forEach((m) => { addMessage(m); lastId = m.id; });
        scrollIfStuck();
      }
      setTyping(data.busy);
      if (!started) {
        started = true;
        if (lastId === 0 && !data.busy) send("start", giftCode ? "gift_" + giftCode : "", "");
      }
    } catch (e) { /* сервер ещё не поднялся: попробуем снова */ }
    finally { polling = false; }
  }

  function send(kind, data, label) {
    stick = true;
    post("/api/event", { uid, kind, data, label }).then(poll).catch(() => toast("Нет связи с сервером"));
    setTyping(true);
  }

  composer.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    send("text", text, "");
  });

  // ---- имитация оплаты ----
  let payOrder = null;
  function openPay(orderId, amount) {
    payOrder = orderId;
    $("payInfo").textContent = `Заказ № ${orderId}. Сумма: ${amount} ₽.`;
    $("payOk").textContent = `Оплатить ${amount} ₽`;
    $("payModal").hidden = false;
    $("payOk").focus();
  }
  async function closePay(ok) {
    const id = payOrder;
    $("payModal").hidden = true;
    payOrder = null;
    if (id == null) return;
    setTyping(true);
    try { await post("/api/pay", { uid, order_id: id, ok }); poll(); }
    catch (e) { toast("Не удалось отправить результат оплаты"); }
  }
  $("payOk").addEventListener("click", () => closePay(true));
  $("payCancel").addEventListener("click", () => closePay(false));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("payModal").hidden) closePay(false); });

  // ---- «отправить контакту» ----
  async function shareText(text) {
    try { await navigator.clipboard.writeText(text); }
    catch (e) {
      const ta = document.createElement("textarea");
      ta.value = text; document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy"); } catch (e2) {}
      ta.remove();
    }
    toast("Текст скопирован. В Telegram здесь откроется выбор контакта.");
  }

  // ---- панель разработчика ----
  $("devToggle").addEventListener("click", () => $("dev").classList.toggle("open"));
  document.querySelectorAll("[data-shift]").forEach((b) =>
    b.addEventListener("click", async () => {
      await post("/api/dev/shift", { days: Number(b.dataset.shift) });
      toast(`Время бота сдвинуто на ${b.dataset.shift} дн.`);
      refreshDev();
    }));
  $("resetMe").addEventListener("click", async () => {
    await fetch(`/api/dev/reset?uid=${encodeURIComponent(uid)}`, { method: "POST" });
    location.reload();
  });
  $("newUser").addEventListener("click", () => {
    try { store.removeItem("numero_uid"); } catch (e) {}
    location.reload();
  });

  const listInto = (ul, items, render) => {
    ul.innerHTML = "";
    if (!items.length) { ul.innerHTML = '<li class="dev__empty">пусто</li>'; return; }
    items.forEach((it) => { const li = document.createElement("li"); render(li, it); ul.appendChild(li); });
  };

  async function refreshDev() {
    try {
      const d = await api(`/api/dev?uid=${encodeURIComponent(uid)}`);
      $("devTime").textContent = `${d.bot_time} (${d.tz})` + (d.shift_days ? `, сдвиг ${d.shift_days} дн.` : "");
      $("devUser").textContent =
        `uid: ${uid}\nсостояние: ${d.user.state}\n` +
        `дата рождения: ${d.user.birthdate || "не введена"}\nпауза «3 минуты»: ${d.loading_seconds} сек`;
      listInto($("devMail"), d.emails, (li, e) => {
        li.innerHTML = `${escapeHtml(e.to)}` +
          (e.file ? `<br><a href="/outbox/${encodeURIComponent(e.file)}" target="_blank" rel="noopener">${escapeHtml(e.file)}</a>` : "") +
          (e.body ? `<br><small>${escapeHtml(e.body)}</small>` : "");
      });
      listInto($("devGifts"), d.gifts, (li, g) => {
        li.innerHTML = `${escapeHtml(g.gift)}, ${g.status === "new" ? "не открыт" : "получен"}<br>` +
          `<a href="${escapeHtml(g.link)}" target="_blank" rel="noopener">${escapeHtml(g.link)}</a>`;
      });
      listInto($("devOrders"), d.orders, (li, o) => {
        li.textContent = `№${o.id} ${o.product}, ${o.amount} ₽, ${o.status}`;
      });
      listInto($("devSubs"), d.subs, (li, s) => {
        li.textContent = `${s.kind === "monthly" ? "ежемесячная" : "ежедневная"}, ${s.active ? "активна" : "закрыта"}: ${s.time}, ${s.start} → ${s.end}, последняя: ${s.last_sent || "нет"}`;
      });
    } catch (e) { /* ignore */ }
  }

  poll();
  refreshDev();
  setInterval(poll, 700);
  setInterval(refreshDev, 2000);
})();
