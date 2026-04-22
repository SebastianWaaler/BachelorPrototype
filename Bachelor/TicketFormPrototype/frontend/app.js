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
      if (inquiry.value && inquiry.value !== "-- Velg --") clearFieldError("inquiry");
    });
  }
  if (shortDesc) {
    shortDesc.addEventListener("input", () => {
      if (shortDesc.value.trim()) clearFieldError("Description");
    });
  }
  if (longDesc) {
    longDesc.addEventListener("input", () => {
      if (longDesc.value.trim()) clearFieldError("LongDescription");
    });
  }
}

function showSuccessMessage(tableNumber) {
  const existing = document.getElementById("successMessage");
  if (existing) existing.remove();

  const msg = document.createElement("p");
  msg.id = "successMessage";
  msg.textContent = `✓ Ticket sendt! Logget i tabell ${tableNumber}.`;
  msg.style.cssText = "color: green; font-weight: bold; margin-top: 16px;";

  // Append at the bottom of the form container
  const container = document.querySelector(".container");
  if (container) container.appendChild(msg);
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

function askQuestion(questionText, type, choices) {
  if (type === "multiple_choice" && Array.isArray(choices) && choices.length > 0) {
    return prompt(`${questionText}\n\nValg:\n- ${choices.join("\n- ")}`) || "";
  }
  return prompt(questionText) || "";
}

async function submitForm() {
  const submitBtn = document.getElementById("submitBtn");

  try {
    if (!confirmed || !userId) {
      alert("Bekreft bruker først.");
      return;
    }

    if (!validateForm()) return;

    submitBtn.classList.add("loading");
    submitBtn.disabled = true;

    const inquiry = document.getElementById("inquiry").value;
    const shortDesc = document.getElementById("Description").value.trim();
    const longDesc = document.getElementById("LongDescription").value.trim();

    const title = shortDesc;
    const description =
`Kategori: ${inquiry}
Kort beskrivelse: ${shortDesc}

Detaljert beskrivelse:
${longDesc}`;

    // Step 1: Start the AI conversation with title + description
    let chatRes = await fetch("http://127.0.0.1:5000/api/ai/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: userId, title, description })
    });

    let chatData = await chatRes.json();
    if (!chatRes.ok) throw new Error(chatData.error || "Failed to start AI chat");

    // Step 2: Loop — show each question until AI says done
    while (!chatData.done) {
      const answer = askQuestion(chatData.question, chatData.type, chatData.choices);

      chatRes = await fetch("http://127.0.0.1:5000/api/ai/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: userId, prior_answer: answer })
      });

      chatData = await chatRes.json();
      if (!chatRes.ok) throw new Error(chatData.error || "Failed AI chat turn");
    }

    // Step 3: AI is satisfied — finalize and submit
    const finRes = await fetch("http://127.0.0.1:5000/api/ai/finalize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: userId })
    });

    const finData = await finRes.json();
    if (!finRes.ok) throw new Error(finData.error || "Failed to finalize ticket");
    alert(`Ticket sendt!\nTid brukt: ${finData.time_to_submit_ms} ms\nLogget i tabell: ${finData.log_table}`);

    console.log("AI final:", finData.final);

    // Reset form
    confirmed = false;
    userId = null;
    submitBtn.disabled = true;
    submitBtn.classList.remove("loading");
    document.getElementById("userStatus").textContent = "Bekreft bruker for å starte ny timer.";
    document.getElementById("inquiry").value = "-- Velg --";
    document.getElementById("Description").value = "";
    document.getElementById("LongDescription").value = "";
    clearAllErrors();

    // Show success message at the bottom of the page instead of alert
    showSuccessMessage(finData.log_table);

  } catch (err) {
    console.error(err);
    submitBtn.classList.remove("loading");
    submitBtn.disabled = false;
    alert("Noe gikk galt: " + err.message);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  attachLiveValidation();
});

if (typeof module !== 'undefined') {
  module.exports = { parseUserId, validateForm };
}