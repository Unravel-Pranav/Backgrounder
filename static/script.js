const form = document.getElementById("searchForm");
const submitBtn = document.getElementById("submitBtn");
const btnText = submitBtn.querySelector(".btn-text");
const btnLoader = submitBtn.querySelector(".btn-loader");
const errorBox = document.getElementById("errorBox");
const loadingSkeleton = document.getElementById("loadingSkeleton");
const reportSection = document.getElementById("report");

// File upload display
const resumeInput = document.getElementById("resume");
const fileDisplay = document.getElementById("fileDisplay");
const fileText = fileDisplay.querySelector(".file-upload-text");

// Photo upload display
const photoInput = document.getElementById("photo");
const photoDisplay = document.getElementById("photoDisplay");
const photoText = photoDisplay.querySelector(".file-upload-text");

photoInput.addEventListener("change", () => {
  if (photoInput.files.length > 0) {
    photoText.textContent = photoInput.files[0].name;
    photoDisplay.classList.add("has-file");
  } else {
    photoText.textContent = "Upload photo or paste URL below";
    photoDisplay.classList.remove("has-file");
  }
});

resumeInput.addEventListener("change", () => {
  if (resumeInput.files.length > 0) {
    fileText.textContent = resumeInput.files[0].name;
    fileDisplay.classList.add("has-file");
  } else {
    fileText.textContent = "Choose file or drag here";
    fileDisplay.classList.remove("has-file");
  }
});

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  await runCheck();
});

async function runCheck() {
  errorBox.hidden = true;
  reportSection.hidden = true;
  loadingSkeleton.hidden = false;
  setLoading(true);

  // Reset activity feed
  const feed = document.getElementById("activityFeed");
  const progressBar = document.getElementById("progressFill");
  const progressText = document.getElementById("progressText");
  feed.innerHTML = "";
  progressBar.style.width = "0%";
  progressText.textContent = "Starting...";
  loadingSkeleton.hidden = false;

  const fd = new FormData();
  fd.append("name", val("name"));
  if (val("company")) fd.append("company", val("company"));
  if (val("title")) fd.append("title", val("title"));
  if (val("location")) fd.append("location", val("location"));
  if (val("linkedin_url")) fd.append("linkedin_url", val("linkedin_url"));
  if (val("provider")) fd.append("provider", val("provider"));
  if (val("photo_url")) fd.append("photo_url", val("photo_url"));
  const resumeFile = document.getElementById("resume").files[0];
  if (resumeFile) fd.append("resume", resumeFile);
  const photoFile = document.getElementById("photo").files[0];
  if (photoFile) fd.append("photo", photoFile);

  try {
    const resp = await fetch("/api/v1/check", { method: "POST", body: fd });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      throw new Error(err.detail || `HTTP ${resp.status}`);
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // Parse SSE events from buffer
      const parts = buffer.split("\n\n");
      buffer = parts.pop(); // keep incomplete part

      for (const part of parts) {
        if (!part.trim()) continue;
        const lines = part.split("\n");
        let eventType = "message";
        let eventData = "";
        for (const line of lines) {
          if (line.startsWith("event: ")) eventType = line.slice(7);
          else if (line.startsWith("data: ")) eventData = line.slice(6);
        }
        if (!eventData) continue;

        try {
          const data = JSON.parse(eventData);
          if (eventType === "status") handleStatus(data, feed, progressBar, progressText);
          else if (eventType === "result") {
            loadingSkeleton.hidden = true;
            renderReport(data);
          }
        } catch (e) {
          console.warn("SSE parse error:", e);
        }
      }
    }
  } catch (err) {
    loadingSkeleton.hidden = true;
    errorBox.textContent = `Error: ${err.message}`;
    errorBox.hidden = false;
  } finally {
    setLoading(false);
  }
}

function handleStatus(data, feed, bar, text) {
  const { step, label, state, detail, completed, total, tasks, task_id } = data;

  if (step === "search_start" && tasks) {
    // Render initial task list
    text.textContent = label;
    feed.innerHTML = "";
    for (const t of tasks) {
      const el = document.createElement("div");
      el.className = "feed-item running";
      el.id = `feed-${t.id.replace(/[:.]/g, "-")}`;
      el.innerHTML = `<span class="feed-icon"><span class="feed-spinner"></span></span><span class="feed-label">${escapeHtml(t.label)}</span><span class="feed-status">searching...</span>`;
      feed.appendChild(el);
    }
  } else if (step === "task_done" && task_id) {
    const pct = total ? Math.round((completed / total) * 90) : 0; // save 10% for AI
    bar.style.width = pct + "%";
    text.textContent = `${completed}/${total} sources checked`;

    const elId = `feed-${task_id.replace(/[:.]/g, "-")}`;
    const el = document.getElementById(elId);
    if (el) {
      el.className = `feed-item ${state}`;
      const statusEl = el.querySelector(".feed-status");
      const iconEl = el.querySelector(".feed-icon");
      if (state === "done") {
        iconEl.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#34d399" stroke-width="3"><polyline points="20 6 9 17 4 12"/></svg>`;
        statusEl.textContent = detail || "done";
        statusEl.style.color = "#34d399";
      } else {
        iconEl.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#f87171" stroke-width="3"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`;
        statusEl.textContent = "failed";
        statusEl.style.color = "#f87171";
      }
    }
  } else if (step === "resume_parse") {
    if (state === "running") {
      const el = document.createElement("div");
      el.className = "feed-item running";
      el.id = "feed-resume-parse";
      el.innerHTML = `<span class="feed-icon"><span class="feed-spinner"></span></span><span class="feed-label">Parsing resume</span><span class="feed-status">extracting...</span>`;
      feed.prepend(el);
    } else {
      const el = document.getElementById("feed-resume-parse");
      if (el) {
        el.className = `feed-item ${state}`;
        el.querySelector(".feed-icon").innerHTML = state === "done"
          ? `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#34d399" stroke-width="3"><polyline points="20 6 9 17 4 12"/></svg>`
          : `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#f87171" stroke-width="3"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`;
        const s = el.querySelector(".feed-status");
        s.textContent = detail || label;
        s.style.color = state === "done" ? "#34d399" : "#f87171";
      }
    }
  } else if (step === "photo_upload") {
    if (state === "running") {
      const el = document.createElement("div");
      el.className = "feed-item running";
      el.id = "feed-photo-upload";
      el.innerHTML = `<span class="feed-icon"><span class="feed-spinner"></span></span><span class="feed-label">Uploading photo</span><span class="feed-status">uploading...</span>`;
      feed.prepend(el);
    } else {
      const el = document.getElementById("feed-photo-upload");
      if (el) {
        el.className = `feed-item ${state}`;
        el.querySelector(".feed-icon").innerHTML = state === "done"
          ? `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#34d399" stroke-width="3"><polyline points="20 6 9 17 4 12"/></svg>`
          : `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#f87171" stroke-width="3"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`;
        const s = el.querySelector(".feed-status");
        s.textContent = detail || label;
        s.style.color = state === "done" ? "#34d399" : "#f87171";
      }
    }
  } else if (step === "analyzing") {
    bar.style.width = "92%";
    text.textContent = "AI generating report...";
    const el = document.createElement("div");
    el.className = "feed-item running";
    el.id = "feed-ai-analyze";
    el.innerHTML = `<span class="feed-icon"><span class="feed-spinner"></span></span><span class="feed-label">AI Analysis</span><span class="feed-status">generating report...</span>`;
    feed.appendChild(el);
  }
}

function renderReport(data) {
  // Mark AI step done
  const aiEl = document.getElementById("feed-ai-analyze");
  if (aiEl) {
    aiEl.className = "feed-item done";
    aiEl.querySelector(".feed-icon").innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#34d399" stroke-width="3"><polyline points="20 6 9 17 4 12"/></svg>`;
    const s = aiEl.querySelector(".feed-status");
    s.textContent = "report ready";
    s.style.color = "#34d399";
  }
  const bar = document.getElementById("progressFill");
  const text = document.getElementById("progressText");
  bar.style.width = "100%";
  text.textContent = "Complete";

  // Person Header
  const initials = getInitials(data.name);
  document.getElementById("personAvatar").textContent = initials;
  document.getElementById("personName").textContent = data.name;

  const profile = data.linkedin_profile;
  document.getElementById("personHeadline").textContent = profile?.headline || "";

  const locEl = document.getElementById("personLocation");
  if (profile?.location) {
    locEl.querySelector("span").textContent = profile.location;
    locEl.hidden = false;
  } else {
    locEl.hidden = true;
  }

  const liLink = document.getElementById("personLinkedin");
  if (profile?.url) { liLink.href = profile.url; liLink.hidden = false; }
  else { liLink.hidden = true; }

  const confNote = document.getElementById("confidenceNote");
  if (data.confidence_note) { confNote.textContent = data.confidence_note; confNote.hidden = false; }
  else { confNote.hidden = true; }

  // Verdict
  const verdictCard = document.getElementById("verdictCard");
  const v = data.verdict;
  if (v && v.rating) {
    const rating = v.rating.toLowerCase();
    verdictCard.className = `card verdict-card fade-in rating-${rating}`;
    const score = Math.max(0, Math.min(100, v.score || 0));
    const arc = document.getElementById("verdictArc");
    const offset = 314 - (score / 100) * 314;
    arc.style.strokeDashoffset = offset;
    const scoreColor = score >= 80 ? "#34d399" : score >= 50 ? "#fbbf24" : "#f87171";
    arc.style.stroke = scoreColor;
    document.getElementById("verdictScoreNum").textContent = score;
    document.getElementById("verdictScoreNum").style.color = scoreColor;

    const badge = document.getElementById("verdictBadge");
    badge.className = `verdict-badge ${rating}`;
    badge.textContent = { clean: "Clean", caution: "Caution", red_flags: "Red Flags" }[rating] || rating;
    document.getElementById("verdictSummary").textContent = v.summary || "";

    _renderList("verdictRedFlags", "verdictRedFlagsList", v.red_flags);
    _renderList("verdictGreenFlags", "verdictGreenFlagsList", v.green_flags);

    const rvSection = document.getElementById("verdictResumeVs");
    const rvList = document.getElementById("verdictResumeVsList");
    if (v.resume_vs_online?.length) {
      rvList.innerHTML = v.resume_vs_online.map((item) => {
        let cls = "";
        if (item.startsWith("VERIFIED")) cls = 'style="color:#34d399"';
        else if (item.startsWith("CONTRADICTED")) cls = 'style="color:#f87171"';
        else if (item.startsWith("UNVERIFIED")) cls = 'style="color:#fbbf24"';
        return `<li ${cls}>${escapeHtml(item)}</li>`;
      }).join("");
      rvSection.hidden = false;
    } else { rvSection.hidden = true; }

    _renderList("verdictRecs", "verdictRecsList", v.recommendations);
    verdictCard.hidden = false;
  } else { verdictCard.hidden = true; }

  // Summary
  document.getElementById("summaryText").textContent = data.summary || "No summary available.";

  // Professional Background
  const bgCard = document.getElementById("bgCard");
  if (data.professional_background) {
    document.getElementById("bgText").innerHTML = data.professional_background.split("\n").filter(p => p.trim()).map(p => `<p>${escapeHtml(p)}</p>`).join("");
    bgCard.hidden = false;
  } else { bgCard.hidden = true; }

  // Resume Data
  const resumeCard = document.getElementById("resumeCard");
  const rd = data.resume_data;
  if (rd) {
    let html = "";
    if (rd.title) html += _rf("Title", escapeHtml(rd.title));
    if (rd.company) html += _rf("Company", escapeHtml(rd.company));
    if (rd.email) html += _rf("Email", escapeHtml(rd.email));
    if (rd.location) html += _rf("Location", escapeHtml(rd.location));
    if (rd.linkedin_url) html += _rf("LinkedIn", `<a href="${escapeAttr(rd.linkedin_url)}" target="_blank" style="color:var(--accent-light);text-decoration:none">${escapeHtml(rd.linkedin_url)}</a>`);
    if (rd.github_url) html += _rf("GitHub", `<a href="${escapeAttr(rd.github_url)}" target="_blank" style="color:var(--accent-light);text-decoration:none">${escapeHtml(rd.github_url)}</a>`);
    if (rd.skills?.length) html += `<div class="resume-field full-width"><div class="resume-field-label">Skills</div><div class="resume-skills-tags">${rd.skills.map(s => `<span class="resume-skill">${escapeHtml(s)}</span>`).join("")}</div></div>`;
    if (rd.experience?.length) html += `<div class="resume-field full-width"><div class="resume-field-label">Experience</div>${rd.experience.map(e => `<div class="resume-exp-item"><div class="resume-exp-title">${escapeHtml(e.title||"")}</div><div class="resume-exp-company">${escapeHtml(e.company||"")}</div>${e.duration?`<div class="resume-exp-duration">${escapeHtml(e.duration)}</div>`:""}</div>`).join("")}</div>`;
    if (rd.key_search_terms?.length) html += `<div class="resume-field full-width"><div class="resume-field-label">Key Identifiers</div><div class="resume-search-terms">${rd.key_search_terms.map(t => `<span class="search-term">${escapeHtml(t)}</span>`).join("")}</div></div>`;
    document.getElementById("resumeContent").innerHTML = html;
    resumeCard.hidden = false;
  } else { resumeCard.hidden = true; }

  // Identity Verification
  const idCard = document.getElementById("identityCard");
  const iv = data.identity_verification;
  if (iv && iv.confidence) {
    const conf = iv.confidence.toLowerCase();
    idCard.className = `card identity-card fade-in confidence-${conf}`;
    const badge = document.getElementById("identityBadge");
    badge.className = `identity-badge ${conf}`;
    badge.innerHTML = `<span class="dot"></span>${escapeHtml(conf)} confidence`;
    document.getElementById("identityWarning").hidden = !iv.multiple_people_detected;
    document.getElementById("identityReasoning").textContent = iv.reasoning || "";

    const xrefSection = document.getElementById("xrefSection");
    if (iv.cross_reference_notes?.length) {
      document.getElementById("xrefList").innerHTML = iv.cross_reference_notes.map(n => `<li>${escapeHtml(n)}</li>`).join("");
      xrefSection.hidden = false;
    } else { xrefSection.hidden = true; }

    const pfSection = document.getElementById("profilesFoundSection");
    if (iv.profiles_found?.length) {
      document.getElementById("profilesFoundList").innerHTML = iv.profiles_found.map(p => `<div class="profile-found-item"><span class="profile-source-badge ${escapeAttr((p.source||"").toLowerCase())}">${escapeHtml(p.source||"")}</span><div class="profile-found-info"><div class="profile-found-name">${escapeHtml(p.name||"")}</div><div class="profile-found-desc">${escapeHtml(p.description||"")}</div></div></div>`).join("");
      pfSection.hidden = false;
    } else { pfSection.hidden = true; }
    idCard.hidden = false;
  } else { idCard.hidden = true; }

  // Key Highlights
  _renderListCard("highlightsCard", "highlightsList", data.key_highlights);

  // Experience
  const expCard = document.getElementById("experienceCard");
  if (profile?.experience?.length) {
    document.getElementById("experienceTimeline").innerHTML = profile.experience.map(exp => `<div class="timeline-item"><div class="timeline-title">${escapeHtml(exp.title||"")}</div><div class="timeline-company">${escapeHtml(exp.company||"")}</div>${exp.duration?`<div class="timeline-duration">${escapeHtml(exp.duration)}</div>`:""}${exp.description?`<div class="timeline-desc">${escapeHtml(exp.description)}</div>`:""}</div>`).join("");
    expCard.hidden = false;
  } else { expCard.hidden = true; }

  // Education
  const eduCard = document.getElementById("educationCard");
  if (profile?.education?.length) {
    document.getElementById("educationList").innerHTML = profile.education.map(edu => `<div class="edu-item"><div class="edu-icon"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 10v6M2 10l10-5 10 5-10 5z"/><path d="M6 12v5c0 1.66 3.13 3 7 3s7-1.34 7-3v-5"/></svg></div><div><div class="edu-school">${escapeHtml(edu.school||"")}</div><div class="edu-degree">${escapeHtml([edu.degree,edu.field].filter(Boolean).join(" - "))}</div></div></div>`).join("");
    eduCard.hidden = false;
  } else { eduCard.hidden = true; }

  // Skills
  const skillsCard = document.getElementById("skillsCard");
  if (profile?.skills?.length) {
    document.getElementById("skillsTags").innerHTML = profile.skills.map(s => `<span class="skill-tag">${escapeHtml(s)}</span>`).join("");
    skillsCard.hidden = false;
  } else { skillsCard.hidden = true; }

  // Photo Matches
  const photoCard = document.getElementById("photoCard");
  if (data.photo_matches?.length) {
    document.getElementById("photoList").innerHTML = data.photo_matches.map(pm => `
      <a class="photo-match-item" href="${escapeAttr(pm.url)}" target="_blank" rel="noopener">
        ${pm.thumbnail ? `<img class="photo-match-thumb" src="${escapeAttr(pm.thumbnail)}" alt="" loading="lazy" onerror="this.style.display='none'"/>` : ""}
        <div class="photo-match-info">
          <div class="photo-match-title">${escapeHtml(pm.title)}</div>
          <div class="photo-match-source">${escapeHtml(pm.source || pm.url)}</div>
        </div>
        ${pm.platform ? `<span class="photo-match-platform">${escapeHtml(pm.platform)}</span>` : ""}
      </a>`).join("");
    photoCard.hidden = false;
  } else {
    photoCard.hidden = true;
  }

  // Company Verification
  const companyCard = document.getElementById("companyCard");
  if (data.company_checks?.length) {
    document.getElementById("companyList").innerHTML = data.company_checks.map(cc => `<div class="company-check-item"><div class="company-status ${cc.verified?"verified":"unverified"}">${cc.verified?"&#10003;":"&#10007;"}</div><div><div class="company-name">${escapeHtml(cc.name)}</div><div class="company-desc">${escapeHtml(cc.description)}</div>${cc.evidence_url?`<a class="company-link" href="${escapeAttr(cc.evidence_url)}" target="_blank">${escapeHtml(cc.evidence_url)}</a>`:""}</div></div>`).join("");
    companyCard.hidden = false;
  } else { companyCard.hidden = true; }

  // Social Media
  const socialCard = document.getElementById("socialCard");
  if (data.social_profiles?.length) {
    document.getElementById("socialList").innerHTML = data.social_profiles.map(sp => `<a class="social-item" href="${escapeAttr(sp.url)}" target="_blank" rel="noopener"><span class="social-platform-badge">${escapeHtml(sp.platform)}</span><div class="social-info">${sp.username?`<div class="social-username">@${escapeHtml(sp.username)}</div>`:""}<div class="social-snippet">${escapeHtml(sp.snippet)}</div></div></a>`).join("");
    socialCard.hidden = false;
  } else { socialCard.hidden = true; }

  // GitHub
  const ghCard = document.getElementById("githubCard");
  if (data.github_profiles?.length) {
    document.getElementById("githubList").innerHTML = data.github_profiles.map(gh => `<div class="gh-profile"><div class="gh-header"><div class="gh-avatar">${escapeHtml(getInitials(gh.name||gh.username))}</div><div><div class="gh-name"><a href="${escapeAttr(gh.url)}" target="_blank">${escapeHtml(gh.name||gh.username)}</a></div><div class="gh-username">@${escapeHtml(gh.username)}</div></div></div>${gh.bio?`<div class="gh-bio">${escapeHtml(gh.bio)}</div>`:""}<div class="gh-meta">${gh.company?`<span class="gh-meta-item">${escapeHtml(gh.company)}</span>`:""}<span class="gh-meta-item"><strong>${gh.public_repos}</strong>&nbsp;repos</span><span class="gh-meta-item"><strong>${gh.followers}</strong>&nbsp;followers</span></div>${gh.top_repos?.length?`<div class="gh-repos">${gh.top_repos.map(r=>`<a class="gh-repo" href="${escapeAttr(r.url)}" target="_blank"><span class="gh-repo-name">${escapeHtml(r.name)}</span><span class="gh-repo-desc">${escapeHtml(r.description)}</span>${r.language?`<span class="gh-repo-lang">${escapeHtml(r.language)}</span>`:""}</a>`).join("")}</div>`:""}</div>`).join("");
    ghCard.hidden = false;
  } else { ghCard.hidden = true; }

  // News
  const newsCard = document.getElementById("newsCard");
  if (data.news_mentions?.length) {
    document.getElementById("newsList").innerHTML = data.news_mentions.map(n => `<a class="news-item" href="${escapeAttr(n.url)}" target="_blank" rel="noopener"><div class="news-title">${escapeHtml(n.title)}</div><div class="news-snippet">${escapeHtml(n.snippet)}</div><div class="news-source">${escapeHtml(n.source)}</div></a>`).join("");
    newsCard.hidden = false;
  } else { newsCard.hidden = true; }

  // Reference Contacts
  try {
    const refsCard = document.getElementById("referencesCard");
    if (data.reference_contacts && data.reference_contacts.length > 0) {
      const refsHtml = data.reference_contacts.map(function(rc) {
        const ini = rc.name ? rc.name.split(" ").map(function(w){return w[0]}).slice(0,2).join("").toUpperCase() : "?";
        let catCl = "department";
        if (rc.category && rc.category.indexOf("HR") !== -1) catCl = "hr";
        else if (rc.category && rc.category.indexOf("Manage") !== -1) catCl = "management";
        else if (rc.category && rc.category.indexOf("Colleague") !== -1) catCl = "colleague";
        return '<a class="ref-item" href="' + escapeAttr(rc.linkedin_url || "#") + '" target="_blank" rel="noopener">' +
          '<div class="ref-avatar">' + ini + '</div>' +
          '<div class="ref-info">' +
            '<div class="ref-name">' + escapeHtml(rc.name) + '</div>' +
            '<div class="ref-title">' + escapeHtml(rc.title) + '</div>' +
            '<div class="ref-company">' + escapeHtml(rc.company) + '</div>' +
          '</div>' +
          '<span class="ref-category ' + catCl + '">' + escapeHtml(rc.category) + '</span>' +
        '</a>';
      }).join("");
      document.getElementById("referencesList").innerHTML = refsHtml;
      refsCard.hidden = false;
    } else {
      refsCard.hidden = true;
    }
  } catch(e) { console.error("References render error:", e); }

  // Footer
  document.getElementById("sourcesList").textContent = (data.sources_used || []).join(", ") || "None";
  document.getElementById("providerUsed").textContent = data.provider_used || "Unknown";
  document.getElementById("generatedAt").textContent = data.generated_at ? new Date(data.generated_at).toLocaleString() : "-";

  reportSection.hidden = false;
  // Smooth scroll after a tiny delay so DOM renders
  setTimeout(() => reportSection.scrollIntoView({ behavior: "smooth", block: "start" }), 100);
}

// --- Helpers ---
function val(id) { return document.getElementById(id).value.trim(); }
function setLoading(l) { submitBtn.disabled = l; btnText.hidden = l; btnLoader.hidden = !l; }
function getInitials(n) { return n ? n.split(" ").filter(Boolean).map(w=>w[0]).slice(0,2).join("").toUpperCase() : "?"; }
function escapeHtml(s) { if(!s)return""; const d=document.createElement("div"); d.textContent=s; return d.innerHTML; }
function escapeAttr(s) { return s?s.replace(/&/g,"&amp;").replace(/"/g,"&quot;").replace(/'/g,"&#39;").replace(/</g,"&lt;").replace(/>/g,"&gt;"):""; }
function _rf(label, value) { return `<div class="resume-field"><div class="resume-field-label">${label}</div><div class="resume-field-value">${value}</div></div>`; }
function _renderList(sectionId, listId, items) {
  const sec = document.getElementById(sectionId);
  const list = document.getElementById(listId);
  if (items?.length) { list.innerHTML = items.map(i=>`<li>${escapeHtml(i)}</li>`).join(""); sec.hidden = false; }
  else { sec.hidden = true; }
}
function _renderListCard(cardId, listId, items) {
  const card = document.getElementById(cardId);
  const list = document.getElementById(listId);
  if (items?.length) { list.innerHTML = items.map(i=>`<li>${escapeHtml(i)}</li>`).join(""); card.hidden = false; }
  else { card.hidden = true; }
}
