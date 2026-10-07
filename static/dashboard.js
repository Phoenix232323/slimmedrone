// Dashboard: status bijwerken, zoomen, meldingen en de chat met Jarvis.

const video = $("#video");
let lastAlertId = 0;
let firstStatus = true;

// -- status (elke seconde) -------------------------------------------------

async function refreshStatus() {
  try {
    renderStatus(await api(`/api/status?na=${lastAlertId}`));
  } catch (err) {
    setPill("#pillCamera", "Geen verbinding", "bad");
  }
  setTimeout(refreshStatus, 1000);
}

function setPill(selector, text, state) {
  const pill = $(selector);
  pill.querySelector("b").textContent = text;
  pill.classList.toggle("ok", state === "ok");
  pill.classList.toggle("bad", state === "bad");
}

function renderStatus(s) {
  setPill("#pillCamera", s.camera.verbonden ? "Camera" : "Camera los", s.camera.verbonden ? "ok" : "bad");
  $("#pillCamera").title = s.camera.verbonden ? `Bron: ${s.camera.bron}` : s.camera.fout;
  setPill("#pillFps", `${s.fps.toFixed(1)} fps`);
  setPill("#pillAi", s.ai === "claude" ? "Claude" : "Lokaal", s.ai === "claude" ? "ok" : null);
  $("#detectorInfo").textContent = `Objectherkenning: ${s.objectherkenning}`;

  // Objecten: klik erop om in te zoomen. Niet verversen terwijl de muis
  // erboven hangt, anders verdwijnt de knop net onder je klik.
  const objects = $("#objects");
  if (!objects.matches(":hover")) objects.replaceChildren(...s.objecten.map((o) => el("li", {},
    el("button", {
      title: `Zoom in op ${o.naam}`,
      onclick: () => zoom({ actie: "object", label: o.label, naam: o.naam, kader: o.kader }),
    }, o.naam, el("small", {}, `${Math.round(o.zekerheid * 100)}%`)),
  )));
  $("#objectCount").textContent = s.objecten.length;
  $("#objectsEmpty").hidden = s.objecten.length > 0;

  const faces = $("#faces");
  faces.replaceChildren(...s.gezichten.map((f) => el("li", { class: f.naam ? "" : "unknown" },
    f.naam || "Onbekend", f.naam ? el("small", {}, f.overeenkomst.toFixed(2)) : null)));
  $("#faceCount").textContent = s.gezichten.length;
  $("#facesEmpty").hidden = s.gezichten.length > 0;

  const zoomState = $("#zoomState");
  zoomState.textContent = s.zoom.actief ? `Zoom ${s.zoom.factor}x · ${s.zoom.naam}` : "Volledig beeld";
  zoomState.classList.toggle("active", s.zoom.actief);
  $("#zoomOut").disabled = !s.zoom.actief;

  for (const alert of s.meldingen) {
    addAlert(alert, !firstStatus);
    lastAlertId = Math.max(lastAlertId, alert.id);
  }
  firstStatus = false;
}

function addAlert(alert, showToast) {
  const list = $("#alerts");
  const photo = alert.foto ? el("img", { src: alert.foto, alt: "" }) : null;
  list.prepend(el("li", { class: alert.soort }, photo,
    el("div", { class: "text" }, alert.bericht, el("time", {}, formatTime(alert.tijd)))));
  while (list.children.length > 50) list.lastChild.remove();
  $("#alertsEmpty").hidden = true;
  if (showToast) toast(alert);
}

function toast(alert) {
  const node = el("div", { class: `toast ${alert.soort}` },
    alert.foto ? el("img", { src: alert.foto, alt: "" }) : null, alert.bericht);
  $("#toasts").append(node);
  setTimeout(() => node.remove(), 6000);
}

// -- zoomen ---------------------------------------------------------------

async function zoom(body) {
  try {
    await api("/api/zoom", { method: "POST", json: body });
  } catch (err) {
    console.warn(err);
  }
}

$("#zoomOut").addEventListener("click", () => zoom({ actie: "uit" }));

// Klik in het beeld: zoom 2x in rond dat punt. Het beeld staat in
// "object-fit: contain", dus we houden rekening met zwarte randen.
video.addEventListener("click", (event) => {
  const rect = video.getBoundingClientRect();
  const natW = video.naturalWidth || 16, natH = video.naturalHeight || 9;
  const scale = Math.min(rect.width / natW, rect.height / natH);
  const shownW = natW * scale, shownH = natH * scale;
  const x = (event.clientX - rect.left - (rect.width - shownW) / 2) / shownW;
  const y = (event.clientY - rect.top - (rect.height - shownH) / 2) / shownH;
  if (x >= 0 && x <= 1 && y >= 0 && y <= 1) zoom({ actie: "punt", x, y });
});

$("#fullscreen").addEventListener("click", () => {
  if (document.fullscreenElement) document.exitFullscreen();
  else $("#videoWrap").requestFullscreen?.();
});

// -- chat met Jarvis -------------------------------------------------------

const messages = $("#messages");
const input = $("#chatInput");
let busy = false;

function addMessage(kind, text, actions = [], source = null) {
  const meta = (actions.length || source)
    ? el("div", { class: "meta" },
        ...actions.map((a) => el("span", {}, a)),
        source ? el("span", { class: "source" }, source === "claude" ? "via Claude" : "lokaal") : null)
    : null;
  const node = el("div", { class: `msg ${kind}` }, el("p", {}, text), meta);
  messages.append(node);
  messages.scrollTop = messages.scrollHeight;
  return node;
}

async function sendChat(text) {
  text = text.trim();
  if (!text || busy) return;
  busy = true;
  $("#sendBtn").disabled = true;
  input.value = "";
  addMessage("user", text);
  const typing = el("div", { class: "msg jarvis typing" }, el("i"), el("i"), el("i"));
  messages.append(typing);
  messages.scrollTop = messages.scrollHeight;
  $(".orb").classList.add("thinking");
  try {
    const r = await api("/api/chat", { method: "POST", json: { bericht: text } });
    typing.remove();
    addMessage("jarvis", r.antwoord, r.acties, r.bron);
    speak(r.antwoord);
  } catch (err) {
    typing.remove();
    addMessage("error", `Er ging iets mis: ${err.message}`);
  } finally {
    busy = false;
    $("#sendBtn").disabled = false;
    $(".orb").classList.remove("thinking");
    input.focus();
  }
}

$("#chatForm").addEventListener("submit", (event) => {
  event.preventDefault();
  sendChat(input.value);
});

$("#suggestions").addEventListener("click", (event) => {
  if (event.target.tagName === "BUTTON") sendChat(event.target.textContent);
});

$("#chatReset").addEventListener("click", async () => {
  await api("/api/chat/reset", { method: "POST", json: {} }).catch(() => {});
  messages.replaceChildren();
  addMessage("jarvis", "Nieuw gesprek. Waarmee kan ik helpen?");
});

// -- voorlezen (tekst naar spraak) ---------------------------------------

const speakToggle = $("#speakToggle");
let speakOn = false;
try { speakOn = localStorage.getItem("jarvis-spreekt") === "1"; } catch {}
speakToggle.setAttribute("aria-pressed", String(speakOn));

speakToggle.addEventListener("click", () => {
  speakOn = !speakOn;
  speakToggle.setAttribute("aria-pressed", String(speakOn));
  try { localStorage.setItem("jarvis-spreekt", speakOn ? "1" : "0"); } catch {}
  if (!speakOn) window.speechSynthesis?.cancel();
});

function speak(text) {
  if (!speakOn || !window.speechSynthesis) return;
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = "nl-NL";
  const voice = speechSynthesis.getVoices().find((v) => v.lang.startsWith("nl"));
  if (voice) utterance.voice = voice;
  speechSynthesis.cancel();
  speechSynthesis.speak(utterance);
}

// -- inspreken (spraak naar tekst) ---------------------------------------
// Browsers staan de microfoon alleen toe via https of op localhost.

const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
if (Recognition && window.isSecureContext) {
  const micBtn = $("#micBtn");
  micBtn.hidden = false;
  const recognizer = new Recognition();
  recognizer.lang = "nl-NL";
  recognizer.interimResults = false;
  recognizer.addEventListener("result", (event) => sendChat(event.results[0][0].transcript));
  recognizer.addEventListener("end", () => micBtn.classList.remove("listening"));
  micBtn.addEventListener("click", () => {
    micBtn.classList.add("listening");
    recognizer.start();
  });
}

refreshStatus();
