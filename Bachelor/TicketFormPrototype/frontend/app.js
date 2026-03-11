let userId = null;
let confirmed = false;

function parseUserId(username) {
  const m = username.trim().toLowerCase().match(/^user(\d{1,2})$/);
  if (!m) return null;
  const id = Number(m[1]);
  if (id < 1 || id > 99) return null;
  return id;
}

function setFieldError(fieldId, message) {
  const field = document.getElementById(fieldId);
  const error = document.getElementById(`${fieldId}Error`);

  if (field) field.classList.add("input-error");
  if (error) error.textContent = message;
}

function clearFieldError(fieldId) {
  const field = document.getElementById(fieldId);
  const error = document.getElementById(`${fieldId}Error`);

  if (field) field.classList.remove("input-error");
  if (error) error.textContent = "";
}

function clearAllErrors() {
  clearFieldError("inquiry");
  clearFieldError("Description");
  clearFieldError("LongDescription");
}

function validateForm() {
  clearAllErrors();
  let isValid = true;

  const inquiry = document.getElementById("inquiry").value;
  const shortDesc = document.getElementById("Description").value.trim();
  const longDesc = document.getElementById("LongDescription").value.trim();

  if (!inquiry || inquiry === "-- Velg --") {
    setFieldError("inquiry", "Velg hva henvendelsen gjelder.");
    isValid = false;
  }

  if (!shortDesc) {
    setFieldError("Description", "Kort beskrivelse er påkrevd.");
    isValid = false;
  }

  if (!longDesc) {
    setFieldError("LongDescription", "Detaljert beskrivelse er påkrevd.");
    isValid = false;
  }

  return isValid;
}

function attachLiveValidation() {
  const inquiry = document.getElementById("inquiry");
  const shortDesc = document.getElementById("Description");
  const longDesc = document.getElementById("LongDescription");

  if (inquiry) {
    inquiry.addEventListener("change", () => {
      if (inquiry.value && inquiry.value !== "-- Velg --") {
        clearFieldError("inquiry");
      }
    });
  }

  if (shortDesc) {
    shortDesc.addEventListener("input", () => {
      if (shortDesc.value.trim()) {
        clearFieldError("Description");
      }
    });
  }

  if (longDesc) {
    longDesc.addEventListener("input", () => {
      if (longDesc.value.trim()) {
        clearFieldError("LongDescription");
      }
    });
  }
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
    const tableSelect = document.getElementById("tableSelect");
    const tableChoice = Number(tableSelect ? tableSelect.value : 1);

    const res = await fetch("http://127.0.0.1:5000/api/draft/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: id, table: tableChoice })
    });

    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed to start draft");

    userId = id;
    confirmed = true;

    status.textContent = `Bekreftet: user${userId}. Timer startet.`;
    status.style.color = "green";
    submitBtn.disabled = false;
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

    if (!validateForm()) {
      return;
    }

    const inquiry = document.getElementById("inquiry").value;
    const shortDesc = document.getElementById("Description").value.trim();
    const longDesc = document.getElementById("LongDescription").value.trim();

    const title = shortDesc;

    const description =
`Kategori: ${inquiry}
Kort beskrivelse: ${shortDesc}

Detaljert beskrivelse:
${longDesc}`;

    const followRes = await fetch("http://127.0.0.1:5000/api/ai/followups", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: userId, title, description })
    });

    const followData = await followRes.json();
    if (!followRes.ok) throw new Error(followData.error || "Failed followups");

    if (!followData.needs_followup) {
      const res = await fetch("http://127.0.0.1:5000/api/tickets", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: userId, title, description })
      });

      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Failed to submit ticket");

      alert(`Ticket sendt!\nTid brukt: ${data.time_to_submit_ms} ms`);
    } else {
      const answers = {};

      for (const q of followData.questions) {
        let answer = "";

        if (q.type === "multiple_choice" && q.choices?.length) {
          answer = prompt(`${q.question}\nValg:\n- ${q.choices.join("\n- ")}`) || "";
        } else {
          answer = prompt(q.question) || "";
        }

        answers[q.id] = answer.trim();
      }

      const finRes = await fetch("http://127.0.0.1:5000/api/ai/finalize", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: userId, answers })
      });

      const finData = await finRes.json();
      if (!finRes.ok) throw new Error(finData.error || "Failed finalize");

      alert(
        `Ticket sendt (AI forbedret)!\nTid brukt: ${finData.time_to_submit_ms} ms\nLogget i tabell: ${finData.log_table}`
      );
    }

    confirmed = false;
    userId = null;

    document.getElementById("submitBtn").disabled = true;
    document.getElementById("userStatus").textContent =
      "Bekreft bruker for å starte ny timer.";

    document.getElementById("inquiry").value = "-- Velg --";
    document.getElementById("Description").value = "";
    document.getElementById("LongDescription").value = "";

    clearAllErrors();
  } catch (err) {
    console.error(err);
    alert("Noe gikk galt: " + err.message);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  attachLiveValidation();
});