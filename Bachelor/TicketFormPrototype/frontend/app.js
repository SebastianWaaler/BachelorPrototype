console.log("JS LASTET!");

let userId = null;
let confirmed = false;

const API_BASE = "[localhost](http://localhost:5000)";

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
  console.log("confirmUser started");

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

    const res = await fetch(`${API_BASE}/api/draft/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: id, table: tableChoice })
    });

    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Failed to start draft");

    userId = id;
    confirmed = true;

    sessionStorage.setItem("userId", String(userId));
    sessionStorage.setItem("confirmed", "true");

    console.log("confirmed set", { userId, confirmed });

    status.textContent = `Bekreftet: user${userId}. Timer startet.`;
    status.style.color = "green";
    submitBtn.disabled = false;
  } catch (err) {
    console.error("Confirm error:", err);
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
  console.log("submitForm started");

  try {
    if (!confirmed || !userId) {
      alert("Bekreft bruker først.");
      return;
    }

    if (!validateForm()) return;

    const inquiry = document.getElementById("inquiry").value;
    const shortDesc = document.getElementById("Description").value.trim();
    const longDesc = document.getElementById("LongDescription").value.trim();

    const title = shortDesc;
    const description =
`Kategori: ${inquiry}
Kort beskrivelse: ${shortDesc}

Detaljert beskrivelse:
${longDesc}`;

    // Send skjema til Copilot-popup 
    if (window.sendToCopilot) {
      window.sendToCopilot({
        userId,
        kategori: inquiry,
        kortBeskrivelse: shortDesc,
        detaljertBeskrivelse: longDesc
      });
    }

    // Eksisterende OpenAI-/Flask-flyt 
    let chatRes = await fetch(`${API_BASE}/api/ai/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: userId, title, description })
    });

    let chatData = await chatRes.json();
    if (!chatRes.ok) throw new Error(chatData.error || "Failed to start AI chat");

    while (!chatData.done) {
      const answer = askQuestion(chatData.question, chatData.type, chatData.choices);

      chatRes = await fetch(`${API_BASE}/api/ai/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: userId, prior_answer: answer })
      });

      chatData = await chatRes.json();
      if (!chatRes.ok) throw new Error(chatData.error || "Failed AI chat turn");
    }

    const finRes = await fetch(`${API_BASE}/api/ai/finalize`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: userId })
    });

    const finData = await finRes.json();
    if (!finRes.ok) throw new Error(finData.error || "Failed to finalize ticket");

    alert(`Ticket sendt (AI forbedret)!\nTid brukt: ${finData.time_to_submit_ms} ms\nLogget i tabell: ${finData.log_table}`);
    console.log("AI final:", finData.final);

    confirmed = false;
    userId = null;

    sessionStorage.removeItem("userId");
    sessionStorage.removeItem("confirmed");

    document.getElementById("submitBtn").disabled = true;
    document.getElementById("userStatus").textContent = "Bekreft bruker for å starte ny timer.";
    document.getElementById("userStatus").style.color = "black";
    document.getElementById("inquiry").value = "-- Velg --";
    document.getElementById("Description").value = "";
    document.getElementById("LongDescription").value = "";
    clearAllErrors();
  } catch (err) {
    console.error("Submit error:", err);
    alert("Noe gikk galt: " + err.message);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  attachLiveValidation();

  const confirmBtn = document.getElementById("confirmBtn");
  const submitBtn = document.getElementById("submitBtn");
  const status = document.getElementById("userStatus");

  if (confirmBtn) {
    confirmBtn.addEventListener("click", async (e) => {
      e.preventDefault();
      e.stopPropagation();
      await confirmUser();
    });
  }

  if (submitBtn) {
    submitBtn.addEventListener("click", async (e) => {
      e.preventDefault();
      e.stopPropagation();
      await submitForm();
    });
  }

  const savedUserId = sessionStorage.getItem("userId");
  const savedConfirmed = sessionStorage.getItem("confirmed");

  if (savedConfirmed === "true" && savedUserId) {
    userId = Number(savedUserId);
    confirmed = true;

    submitBtn.disabled = false;
    status.textContent = `Bekreftet: user${userId}. Timer startet.`;
    status.style.color = "green";
  }
});

window.addEventListener("beforeunload", () => {
  console.log("PAGE IS RELOADING");
});


// --------------------------------------------------------------------
// Copilot Studio integrasjon 
// --------------------------------------------------------------------
import { CopilotClient } from "[unpkg.com](https://unpkg.com/@microsoft/agents-copilotstudio-client@latest/dist/index.js)";


const copilot = new CopilotClient({
  sdkKey: "https://default46cdde47b7844053be4bb08722ff13.9e.environment.api.powerplatform.com/copilotstudio/dataverse-backed/authenticated/bots/crdf2_agent/conversations?api-version=2022-03-01-preview",
  environmentId: "Default-46cdde47-b784-4053-be4b-b08722ff139e",
  schemaName: "crdf2_agent"
});

// Lag popup-komponent
const popup = document.createElement("div");
popup.id = "copilotPopup";
popup.style.cssText = `
  position: fixed;
  bottom: 80px;
  right: 20px;
  width: 380px;
  height: 500px;
  background: white;
  border-radius: 12px;
  box-shadow: 0 4px 20px rgba(0,0,0,0.25);
  display: none;
  overflow: hidden;
  z-index: 9999;
`;
document.body.appendChild(popup);

// Knapp for å åpne/lukke popup
const chatBtn = document.createElement("button");
chatBtn.textContent = "💬 Chat med Copilot";
chatBtn.style.cssText = `
  position: fixed;
  bottom: 20px;
  right: 20px;
  background-color: #0078d4;
  color: white;
  border: none;
  border-radius: 25px;
  padding: 12px 18px;
  cursor: pointer;
  font-size: 15px;
  z-index: 10000;
`;
document.body.appendChild(chatBtn);

chatBtn.onclick = () => {
  popup.style.display = popup.style.display === "none" ? "block" : "none";
};

// Start Copilot-samtale i popup
copilot.renderConversation({
  container: popup,
  welcomeMessage: "Hei! Jeg er IT-assistenten din. Hvordan kan jeg hjelpe?"
});

// Funksjon slik at skjemaet kan sende data direkte inn i Copilot-chatten
window.sendToCopilot = (formData) => {
  const msg = `Ny henvendelse:\n${JSON.stringify(formData, null, 2)}`;
  copilot.sendMessage(msg);
  popup.style.display = "block"; // åpne popup automatisk
};
