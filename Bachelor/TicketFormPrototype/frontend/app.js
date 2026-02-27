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
    

    // 1) Ask backend if we need followups
    const followRes = await fetch("http://127.0.0.1:5000/api/ai/followups", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: userId, title, description })
    });

    const followData = await followRes.json();
    if (!followRes.ok) throw new Error(followData.error || "Failed followups");

    if (!followData.needs_followup) {
      // 2) No followups -> submit normally
      const res = await fetch("http://127.0.0.1:5000/api/tickets", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: userId, title, description })
      });

      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Failed to submit ticket");

      alert(`Ticket sendt!\nTid brukt: ${data.time_to_submit_ms} s`);
    } else {
      // 3) Followups -> collect answers -> finalize
      const answers = {};
      for (const q of followData.questions) {
        let answer = "";
        if (q.type === "multiple_choice" && Array.isArray(q.choices) && q.choices.length) {
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

      alert(`Ticket sendt (AI forbedret)!\nTid brukt: ${finData.time_to_submit_ms} s\nData sendt til Tabell: ${finData.log_table}`);
      console.log("AI final:", finData.final);
    }

    // Reset AFTER successful submission (both paths)
    confirmed = false;
    userId = null;
    document.getElementById("submitBtn").disabled = true;
    document.getElementById("userStatus").textContent = "Bekreft bruker for å starte ny timer.";
   

  } catch (err) {
    console.error(err);
    alert("Noe gikk galt: " + err.message);
  }
}

