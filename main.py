<!-- ==== Cashback Finance – Chat Widget (links, Du, mit E-Mail-Autofill) ==== -->
<div id="cbf-root" style="--cbf-bg:#0ea5e9; --cbf-accent:#10b981; --cbf-text:#0f172a; --cbf-muted:#6b7280; --cbf-surface:#ffffff; --cbf-bubble:#f8fafc; --cbf-user:#e6fffa; max-width:760px;margin:24px auto;font-family:Inter,system-ui,Segoe UI,Roboto,Arial;color:var(--cbf-text);">
  <div style="display:flex;align-items:center;gap:12px;background:linear-gradient(135deg,var(--cbf-bg),var(--cbf-accent));padding:14px 16px;border-radius:16px 16px 0 0;color:#fff;">
    <div style="width:36px;height:36px;border-radius:50%;background:rgba(255,255,255,.2);display:flex;align-items:center;justify-content:center;font-weight:800;letter-spacing:.5px;">CBF</div>
    <div style="flex:1;">
      <div style="font-weight:700;line-height:1;">Cashback Finance – KI-Chat</div>
      <div style="font-size:12px;opacity:.9;">Kurz. Klar. Lösungsorientiert. (Datenschutz: Keine lokale PII-Speicherung. Kontakt nur mit Einwilligung.)</div>
    </div>
  </div>

  <div id="cbf-chat" style="border:1px solid #e5e7eb;border-top:0;border-bottom:0;background:var(--cbf-surface);max-height:520px;min-height:320px;overflow:auto;padding:16px;scroll-behavior:smooth;"></div>

  <form id="cbf-form" style="border:1px solid #e5e7eb;border-top:0;background:#fff;padding:12px;border-radius:0 0 16px 16px;position:relative;">
    <div style="display:flex;gap:10px;align-items:flex-end;flex-wrap:wrap;">
      <textarea id="cbf-input" placeholder="Deine Frage (z. B. Baufinanzierung, Versicherung, Kommunikation …)"
        style="flex:1;min-height:64px;max-height:160px;padding:10px 12px;border:1px solid #e5e7eb;border-radius:12px;resize:vertical;font-family:inherit;"></textarea>
      <button type="submit" id="cbf-send" style="padding:10px 14px;border:0;border-radius:12px;background:var(--cbf-accent);color:#fff;font-weight:700;cursor:pointer;display:flex;align-items:center;gap:8px;">
        <span>Senden</span><span aria-hidden="true" style="display:inline-block;transform:translateY(1px)">➤</span>
      </button>
    </div>

    <div style="display:flex;gap:10px;align-items:center;margin-top:10px;font-size:13px;color:var(--cbf-muted);flex-wrap:wrap;">
      <label style="display:flex;align-items:center;gap:6px;cursor:pointer;">
        <input type="checkbox" id="cbf-lead"> Ich möchte kontaktiert werden
      </label>
      <input type="email" id="cbf-email" placeholder="E-Mail (nur bei Kontaktwunsch)"
        style="flex:1;min-width:220px;padding:8px 10px;border:1px solid #e5e7eb;border-radius:10px;">
      <div style="margin-left:auto;display:flex;gap:8px;align-items:center;">
        <span style="width:8px;height:8px;border-radius:50%;background:var(--cbf-accent);display:inline-block;"></span>
        <span style="font-size:12px;color:var(--cbf-muted);">Online</span>
      </div>
    </div>
  </form>
</div>

<script>
  const BACKEND_URL = "https://cashbackfinance-ki.onrender.com";
  const chat = document.getElementById("cbf-chat");
  const form = document.getElementById("cbf-form");
  const input = document.getElementById("cbf-input");
  const lead = document.getElementById("cbf-lead");
  const email = document.getElementById("cbf-email");
  const sendBtn = document.getElementById("cbf-send");
  const convo = [];

  function bubble(role, text) {
    const row = document.createElement("div");
    row.style.display = "flex"; row.style.gap = "10px"; row.style.margin = "10px 0";
    row.style.alignItems = "flex-start";
    row.style.justifyContent = role === "user" ? "flex-end" : "flex-start";

    const b = document.createElement("div");
    b.style.maxWidth = "80%"; b.style.padding = "10px 12px"; b.style.borderRadius = "12px";
    b.style.whiteSpace = "pre-wrap"; b.style.textAlign = "left"; b.style.lineHeight = "1.4";
    b.style.border = "1px solid #e5e7eb";
    b.style.background = role === "user" ? "var(--cbf-user)" : "var(--cbf-bubble)";
    b.textContent = text;

    if (role === "ai") {
      const av = document.createElement("div");
      av.style.width = "28px"; av.style.height = "28px"; av.style.borderRadius = "50%";
      av.style.background = "linear-gradient(135deg,var(--cbf-bg),var(--cbf-accent))";
      av.style.display = "flex"; av.style.alignItems = "center"; av.style.justifyContent = "center";
      av.style.color = "#fff"; av.style.fontSize = "12px"; av.style.fontWeight = "700"; av.textContent = "AI";
      row.appendChild(av);
    }
    row.appendChild(b);
    chat.appendChild(row);
    chat.scrollTop = chat.scrollHeight;
  }

  function typingRow() {
    const row = document.createElement("div");
    row.style.display = "flex"; row.style.gap = "10px"; row.style.margin = "10px 0";
    const av = document.createElement("div");
    av.style.width = "28px"; av.style.height = "28px"; av.style.borderRadius = "50%";
    av.style.background = "linear-gradient(135deg,var(--cbf-bg),var(--cbf-accent))";
    av.style.display = "flex"; av.style.alignItems = "center"; av.style.justifyContent = "center";
    av.style.color = "#fff"; av.style.fontSize = "12px"; av.style.fontWeight = "700"; av.textContent = "AI";
    const dots = document.createElement("div");
    dots.style.background = "var(--cbf-bubble)"; dots.style.border = "1px solid #e5e7eb";
    dots.style.borderRadius = "12px"; dots.style.padding = "10px 12px"; dots.style.maxWidth = "80%";
    dots.style.textAlign = "left"; dots.style.lineHeight = "1.4";
    dots.innerHTML = '<span class="cbf-dots" style="display:inline-block;letter-spacing:2px;">●●●</span>';
    row.appendChild(av); row.appendChild(dots); chat.appendChild(row); chat.scrollTop = chat.scrollHeight;
    let i = 0; const intv = setInterval(()=>{ const el = dots.querySelector(".cbf-dots"); if(!el){clearInterval(intv);return;}
      el.textContent = ["●","●●","●●●"][i%3]; i++; }, 350);
    row._interval = intv; return row;
  }

  const EMAIL_RE = /[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/i;
  function maybeAutoFillEmailFrom(text){
    const m = text.match(EMAIL_RE);
    if(m && !email.value){
      email.value = m[0];
      const n = document.createElement("div");
      n.textContent = "E-Mail erkannt. Bitte das Einwilligungs-Häkchen setzen, damit ich deine Angaben sicher speichern darf.";
      n.style.fontSize="12px"; n.style.color="#6b7280"; n.style.marginTop="6px";
      form.appendChild(n);
    }
  }

  async function sendMessage(text) {
    bubble("user", text);
    maybeAutoFillEmailFrom(text); // E-Mail aus Chat automatisch übernehmen
    convo.push({ role: "user", content: text });
    const t = typingRow(); sendBtn.disabled = true;

    const payload = { messages: convo, lead_opt_in: !!lead.checked, email: email.value || null };

    try {
      const res = await fetch(`${BACKEND_URL}/chat`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      if (!res.ok) { const err = await res.json().catch(()=>({})); throw new Error(err.detail || `HTTP ${res.status}`); }
      const data = await res.json();
      clearInterval(t._interval); t.remove();
      const reply = data?.message?.content || "(keine Antwort)";
      convo.push({ role: "assistant", content: reply });
      bubble("ai", reply);
    } catch (e) {
      clearInterval(t._interval); t.remove(); bubble("ai", "Fehler: " + e.message);
    } finally { sendBtn.disabled = false; }
  }

  form.addEventListener("submit", (e) => { e.preventDefault(); const text = input.value.trim(); if (!text) return; input.value=""; sendMessage(text); });
  bubble("ai","Hallo! Ich bin die KI von Cashback Finance. Wobei darf ich dir helfen? 😊");
</script>
<!-- ==== /Widget ==== -->
