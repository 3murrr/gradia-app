(() => {
  const $ = (id) => document.getElementById(id);
  const PASS_MARK = 60;

  // the token lives in memory only (never localStorage)
  let token = null;

  function showView(name) {
    $("loginView").hidden = name !== "login";
    $("resultView").hidden = name !== "result";
  }

  function setMsg(text, kind) {
    const el = $("msg");
    el.textContent = text || "";
    el.className = kind || "error";
    el.hidden = !text;
  }

  async function readJson(res) {
    try { return await res.json(); } catch { return null; }
  }

  function errorText(body, fallback) {
    const d = body && body.detail;
    if (typeof d === "string") return d;
    if (d && typeof d.message === "string") {
      return d.release_at ? d.message + " (" + d.release_at + ")" : d.message;
    }
    if (Array.isArray(d)) return "Please check the entered data.";
    return fallback;
  }

  function cell(text, className) {
    const td = document.createElement("td");
    td.textContent = text;
    if (className) td.className = className;
    return td;
  }

  function render(data) {
    $("studentName").textContent = data.name;
    $("studentCode").textContent = "ID: " + data.student_id;
    $("gpa").textContent = Number(data.gpa).toFixed(2);

    const tbody = $("courses");
    tbody.replaceChildren();
    for (const c of data.courses) {
      const passed = c.grade >= PASS_MARK;
      const tr = document.createElement("tr");
      tr.append(
        cell(c.name),
        cell(String(c.grade)),
        cell(passed ? "Pass" : "Fail", passed ? "pass" : "fail")
      );
      tbody.append(tr);
    }
    showView("result");
  }

  function logout() {
    token = null;
    $("loginForm").reset();
    $("courses").replaceChildren();
    showView("login");
  }

  $("loginForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    setMsg("");
    const btn = $("loginBtn");
    btn.disabled = true;

    const studentId = $("studentId").value.trim();
    const nationalId = $("nationalId").value.trim();

    try {
      const loginRes = await fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ student_id: studentId, national_id: nationalId }),
      });
      const loginBody = await readJson(loginRes);
      if (!loginRes.ok) {
        setMsg(
          loginRes.status === 401
            ? "Invalid student ID or national ID."
            : errorText(loginBody, "Login failed. Please try again."),
          "error"
        );
        return;
      }
      token = loginBody.access_token;

      const resultRes = await fetch("/api/results/" + encodeURIComponent(studentId), {
        headers: { Authorization: "Bearer " + token },
      });
      const resultBody = await readJson(resultRes);

      if (resultRes.status === 403) {
        setMsg(errorText(resultBody, "Results are not available."), "info");
        token = null;
        return;
      }
      if (!resultRes.ok) {
        setMsg(errorText(resultBody, "Could not load the result."), "error");
        token = null;
        return;
      }
      render(resultBody);
    } catch {
      setMsg("Network error. Please try again.", "error");
    } finally {
      $("nationalId").value = "";
      btn.disabled = false;
    }
  });

  $("logoutBtn").addEventListener("click", logout);
})();
