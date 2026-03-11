let userId = null;
let confirmed = false;

function parseUserId(username) {
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

    console.log("Draft started for user_id:", userId, data);

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

    alert(`Ticket sendt (AI forbedret)!\nTid brukt: ${finData.time_to_submit_ms} s\nData sendt til Tabell: ${finData.log_table}`);
    console.log("AI final:", finData.final);

    // Reset form
    confirmed = false;
    userId = null;
    document.getElementById("submitBtn").disabled = true;
    document.getElementById("userStatus").textContent = "Bekreft bruker for å starte ny timer.";

  } catch (err) {
    console.error(err);
    alert("Noe gikk galt: " + err.message);
  }
}