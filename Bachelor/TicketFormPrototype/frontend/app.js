let userId = null;
let confirmed = false;

function parseUserId(username) {
  // Accept "user3", "User3", "USER3"
  const m = username.trim().toLowerCase().match(/^user(\d{1,2})$/);
  if (!m) return null;
  const id = Number(m[1]);
  if (id < 1 || id > 99) return null;
  return id;
}

async function confirmUser() {
  const username = document.getElementById("username").value;
  const status = document.getElementById("userStatus");
  const submitBtn = document.getElementById("submitBtn");

  const id = parseUserId(username);
  if (!id) {
    status.textContent = "Ugyldig brukernavn. Bruk format: user1 - user99";
    status.style.color = "red";
    return;
  }

  try {
    // Start server-side timer (draft) only after confirm
    const res = await fetch("http://127.0.0.1:5000/api/draft/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: id })
    });

    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed to start draft");

    userId = id;
    confirmed = true;

    status.textContent = `Bekreftet: user${userId}. Timer startet.`;
    status.style.color = "green";
    submitBtn.disabled = false;

    console.log("Draft started for user_id:", userId, data);

  } catch (err) {
    console.error(err);
    status.textContent = "Kunne ikke starte timer: " + err.message;
    status.style.color = "red";
  }
}

async function submitForm() {
  try {
    if (!confirmed || !userId) {
      alert("Bekreft bruker først.");
      return;
    }

    const inquiry = document.getElementById("inquiry").value;
    const desc = document.getElementById("Description").value.trim();

    if (!inquiry || inquiry === "-- Velg --") {
      alert("Velg hva henvendelsen gjelder.");
      return;
    }
    if (!desc) {
      alert("Skriv en kort beskrivelse.");
      return;
    }

    const title = inquiry;
    const description = `Kategori: ${inquiry}\n\n${desc}`;

    const res = await fetch("http://127.0.0.1:5000/api/tickets", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: userId, title, description })
    });

    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed to submit ticket");

    alert(`Ticket sendt!\nTid brukt: ${data.time_to_submit_ms} ms`);

    // Optional: lock again until reconfirm (forces new timer next time)
    confirmed = false;
    userId = null;
    document.getElementById("submitBtn").disabled = true;
    document.getElementById("userStatus").textContent = "Bekreft bruker for å starte ny timer.";

  } catch (err) {
    console.error(err);
    alert("Noe gikk galt: " + err.message);
  }
}
