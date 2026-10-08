// Gezichtenpagina: personen toevoegen (camera of upload) en verwijderen.

const nameInput = $("#faceName");
const photoInput = $("#photoInput");
const resultBox = $("#result");

function showResult(text, state) {
  resultBox.hidden = false;
  resultBox.textContent = text;
  resultBox.className = `result ${state || ""}`;
}

function requireName() {
  const name = nameInput.value.trim();
  if (!name) {
    showResult("Vul eerst een naam in.", "bad");
    nameInput.focus();
  }
  return name;
}

// Foto van de persoon, of de eerste letter als er (nog) geen foto is.
function avatar(name) {
  const box = el("span", { class: "avatar", "aria-hidden": "true" });
  const img = el("img", { src: `/api/faces/${encodeURIComponent(name)}/foto?t=${Date.now()}`, alt: "" });
  img.addEventListener("error", () => { box.textContent = name.slice(0, 1).toUpperCase(); });
  box.append(img);
  return box;
}

async function loadPeople() {
  const { personen } = await api("/api/faces");
  const list = $("#people");
  list.replaceChildren(...personen.map((p) => el("li", {},
    avatar(p.naam),
    el("b", {}, p.naam),
    el("small", {}, p.fotos === 1 ? "1 foto" : `${p.fotos} foto's`),
    el("button", { class: "btn small danger", type: "button", "aria-label": `${p.naam} verwijderen`,
      onclick: () => removePerson(p.naam) }, icon("trash"), "Verwijderen"),
  )));
  $("#peopleCount").textContent = personen.length;
  $("#peopleEmpty").hidden = personen.length > 0;
}

async function removePerson(name) {
  if (!confirm(`Alle foto's van ${name} verwijderen?`)) return;
  try {
    await api(`/api/faces/${encodeURIComponent(name)}`, { method: "DELETE" });
    showResult(`${name} is verwijderd.`, "ok");
  } catch (err) {
    showResult(err.message, "bad");
  }
  loadPeople();
}

$("#captureBtn").addEventListener("click", async () => {
  const name = requireName();
  if (!name) return;
  try {
    await api("/api/faces/capture", { method: "POST", json: { naam: name } });
    showResult(`Gezicht van ${name} opgeslagen. Neem er gerust nog een paar vanuit andere hoeken.`, "ok");
  } catch (err) {
    showResult(err.message, "bad");
  }
  loadPeople();
});

function updatePhotoCount() {
  const n = photoInput.files.length;
  $("#photoCount").textContent = n ? (n === 1 ? "1 foto gekozen" : `${n} foto's gekozen`)
    : "JPG of PNG, één persoon per foto";
}

photoInput.addEventListener("change", updatePhotoCount);

const dropzone = $("#dropzone");
dropzone.addEventListener("dragover", (event) => { event.preventDefault(); dropzone.classList.add("over"); });
dropzone.addEventListener("dragleave", () => dropzone.classList.remove("over"));
dropzone.addEventListener("drop", (event) => {
  event.preventDefault();
  dropzone.classList.remove("over");
  photoInput.files = event.dataTransfer.files;
  updatePhotoCount();
});

$("#uploadBtn").addEventListener("click", async () => {
  const name = requireName();
  if (!name) return;
  if (!photoInput.files.length) {
    showResult("Kies eerst een of meer foto's.", "bad");
    return;
  }
  const form = new FormData();
  form.append("naam", name);
  for (const file of photoInput.files) form.append("fotos", file);
  showResult("Bezig met verwerken...");
  try {
    const r = await api("/api/faces", { method: "POST", form });
    const lines = r.resultaten.map((x) => `${x.ok ? "✓" : "✗"} ${x.bestand}${x.ok ? "" : ` - ${x.fout}`}`);
    const good = r.resultaten.filter((x) => x.ok).length;
    showResult(`${good} van ${r.resultaten.length} foto's toegevoegd voor ${r.naam}.\n${lines.join("\n")}`,
      good ? "ok" : "bad");
    photoInput.value = "";
    updatePhotoCount();
  } catch (err) {
    showResult(err.message, "bad");
  }
  loadPeople();
});

loadPeople();
// Eén keer de status ophalen: voor de bovenbalk en om te zien of de server een instellingenpagina heeft.
api("/api/status").then(leesStatus).catch(() => {});
