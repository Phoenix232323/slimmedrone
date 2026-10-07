// Gedeelde hulpfuncties voor alle pagina's.

const $ = (selector) => document.querySelector(selector);

// Elke API-aanroep stuurt deze header mee. Andere websites kunnen dat niet,
// zo voorkomt de server dat een vreemde site namens jou iets doet (CSRF).
const API_HEADERS = { "X-SlimmeDrone": "1" };

async function api(path, { method = "GET", json, form } = {}) {
  const options = { method, headers: { ...API_HEADERS } };
  if (json !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(json);
  } else if (form) {
    options.body = form;
  }
  const res = await fetch(path, options);
  if (res.status === 401) {
    location.href = "/login";
    throw new Error("Niet ingelogd");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.fout || `Fout ${res.status}`);
  return data;
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
  for (const child of children) {
    if (child !== null && child !== undefined) node.append(child);
  }
  return node;
}

function formatTime(seconds) {
  return new Date(seconds * 1000).toLocaleTimeString("nl-NL");
}

// De livestream (MJPEG) opnieuw starten als de verbinding wegvalt.
function keepStreamAlive(img) {
  img.addEventListener("error", () => {
    setTimeout(() => { img.src = `/video_feed?t=${Date.now()}`; }, 2000);
  });
}

document.querySelectorAll('img[src^="/video_feed"]').forEach(keepStreamAlive);
