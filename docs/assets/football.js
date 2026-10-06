"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? "—").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const safeLink = url => { try { const u = new URL(url); return u.protocol === "https:" ? esc(u.href) : "#"; } catch { return "#"; } };
  const store = { get(key) { try { return localStorage.getItem(key); } catch { return null; } }, set(key, value) { try { localStorage.setItem(key, value); } catch { /* Device storage optional. */ } } };
  let index, shared, current, view = "today", load = 0, page = 0;
  const cache = new Map();
  const fmt = value => value ? new Date(value).toLocaleString([], {weekday:"short",month:"short",day:"numeric",hour:"numeric",minute:"2-digit",timeZoneName:"short"}) : "Not available";
  const age = value => value ? Date.now() - Date.parse(value) : Infinity;
  const player = id => { const p = shared.players[id] || {id,name:`Player ${id}`,positions:["?"],status:null}; return {...p,status:current?.league.player_status?.[id] || p.status}; };
  const source = text => `<a href="${safeLink(current.league.source_url)}" target="_blank" rel="noopener">${esc(text)}</a>`;
  const playerTitle = p => `<strong>${esc(p.name)}</strong> <span class="pill">${esc(p.positions.join(" / "))}</span> <span class="muted">${esc(p.team)}</span>${p.status ? ` <span class="status">${esc(p.status)}</span>` : ""}`;
  const getTeam = () => current.league.teams.find(t => t.id === $("team").value);
  const getAnalysis = () => current.analysis.teams[$("team").value];
  const stale = () => current.analysis.stale || age(current.league.captured_at) > 86400000 || age(shared.catalog_at) > 86400000;
  const deadlines = () => {
    let local = [];
    try { local = JSON.parse(store.get("fgm.deadlines." + current.league.id) || "[]"); } catch { /* Invalid local state ignored. */ }
    return [...current.league.deadlines, ...local].filter(d => d.at && Number.isFinite(Date.parse(d.at))).sort((a,b) => Date.parse(a.at) - Date.parse(b.at));
  };
  const kickoff = p => current.league.game_times?.[p.id] ? {at:current.league.game_times?.[p.id],name:"Provider game",source_url:current.league.projection_source} : shared.games.filter(g => g.team === p.team).sort((a,b) => Date.parse(a.at)-Date.parse(b.at))[0];
  const playerNews = id => shared.news.filter(n => n.url && n.athlete_ids.includes(player(id).espn_id)).slice(0,3).map(n => `<p><a href="${safeLink(n.url)}" target="_blank" rel="noopener">${esc(n.headline)}</a> <small>${esc(fmt(n.published_at))}</small></p>`).join("");
  const alertCard = a => `<article class="card">${playerTitle(player(a.player_id))}<p><span class="pill">${a.starter ? "Starter" : "Bench / reserve"}</span>${esc(stale() ? "Old snapshot; current status and advice unavailable. Refresh before acting." : a.advice)}</p><details><summary>Sources and context</summary><small>${esc(stale() ? "Historical status; verify current roster in provider." : a.reason)} ${source("Check roster")}</small>${stale() ? "" : playerNews(a.player_id)}</details></article>`;
  function render() {
    if (!current) return;
    const team = getTeam(), analysis = getAnalysis();
    $("meta").textContent = `${current.league.name} · ${current.league.provider.toUpperCase()} · ${current.league.season} · ${current.league.collection} · roster captured ${fmt(current.league.captured_at)}`;
    $("error").hidden = !stale() && !(index.errors || []).length;
    $("error").textContent = [stale() ? "Snapshot older than 24 hours. Refresh rosters and player status before acting. Pickup recommendations paused." : "", ...(index.errors || [])].filter(Boolean).join(" · ");
    $("views").querySelectorAll("button").forEach(b => { if (b.dataset.view === view) b.setAttribute("aria-current","page"); else b.removeAttribute("aria-current"); });
    let html = "";
    if (view === "settings") {
      const s = current.league.settings;
      html = `<h2>League rules</h2><article class="card"><p>${source("Open league settings")}</p><dl><dt>Lineup slots</dt><dd>${esc(current.league.slots.join(" · "))}</dd><dt>Waiver format</dt><dd>${s.waiver_type === 2 ? "FAAB" : s.waiver_type === 0 ? "Priority" : "Confirm in provider"}</dd><dt>Drop waiver hold</dt><dd>${s.waiver_clear_days ?? "Unknown"} days</dd><dt>IR capacity</dt><dd>${s.reserve_slots ?? "Unknown"}</dd><dt>Reception points</dt><dd>${current.league.scoring.rec ?? "Not imported"}</dd></dl><details><summary>Exact scoring settings</summary><pre>${esc(JSON.stringify(current.league.scoring,null,2))}</pre></details></article><article class="card"><h3>Confirm waiver / lineup deadline</h3><p>Copy the next cutoff from league settings. Provider day code ${esc(s.waiver_day_of_week)} is not documented as a timezone-aware cutoff. Saved on this device for this league.</p><form id="deadline-form"><label>Decision<select id="deadline-title"><option>Submit waiver claims</option><option>Set lineup</option></select></label><label>Deadline (your local timezone)<input id="deadline-at" type="datetime-local" required></label><button type="submit">Save confirmed cutoff</button><button type="button" id="clear-deadlines">Clear saved cutoffs</button></form></article>`;
    } else if (view === "news") {
      const ids = new Set((team?.players || []).map(id => player(id).espn_id).filter(Boolean));
      html = `<h2>NFL news</h2><p class="muted">Shared once across leagues · captured ${fmt(shared.news_at)}. Player badges require exact ESPN athlete IDs.</p><ul class="list">${shared.news.filter(n => n.url).map(n => `<li>${n.athlete_ids.some(id => ids.has(id)) ? '<span class="pill">Your roster</span> ' : ""}<a href="${safeLink(n.url)}" target="_blank" rel="noopener">${esc(n.headline)}</a><br><small>${esc(fmt(n.published_at))}</small></li>`).join("") || "<li>No news in this snapshot.</li>"}</ul>`;
    } else if (!team || !analysis) {
      html = `<h2>Choose your roster</h2><p class="card">Select My team above. Roster IDs stay stable; owner names are never stored. Advice and availability belong to the selected league.</p>`;
    } else if (view === "roster") {
      html = `<h2>Your roster</h2><p class="muted">${team.players.length} players · ${team.reserve.length}/${current.league.settings.reserve_slots ?? "?"} IR slots used · ${source("Set lineup in provider")}</p><div class="grid">${team.players.map(id => { const p = player(id), game = kickoff(p); const slot = team.starters.indexOf(id); return `<article class="card">${playerTitle(p)}<p><span class="pill">${team.reserve.includes(id) ? "IR" : slot >= 0 ? current.league.slots.filter(s => !["BN","IR","TAXI"].includes(s))[slot] || "Starter" : "Bench"}</span>${game ? `${Date.parse(game.at) <= Date.now() ? "Game started · locked" : "Kickoff"} ${esc(fmt(game.at))}` : "Next kickoff unavailable; check bye / provider schedule."}</p></article>`; }).join("")}</div>`;
    } else if (view === "waivers") {
      const term = ($( "search")?.value || "").toLowerCase();
      const filter = $("position")?.value || "";
      const picks = stale() ? [] : analysis.waivers;
      html = `<h2>Available pickups</h2><details><summary>Availability and ranking sources</summary><p class="muted">${source("Check claim / free-agent status")} · ${current.league.availability_complete ? "Unrostered across this league only." : "Partial provider availability list; all other players excluded."} Global add counts are shared; fit and scoring use your roster. ${current.league.provider === "sleeper" ? "Provider waiver clearance is not exposed by the public roster API." : "Availability from the imported provider page; exact waiver hour needs confirmation."}</p></details><div class="row"><label>Search shortlist<input id="search" type="search" placeholder="Player name" value="${esc(term)}"></label><label>Position<select id="position"><option value="">All positions</option>${[...new Set(current.league.slots.flatMap(s => s === "FLEX" ? ["RB","WR","TE"] : s === "SUPER_FLEX" ? ["QB","RB","WR","TE"] : [s]).filter(s => !["BN","IR","TAXI"].includes(s)))].map(s => `<option ${filter === s ? "selected" : ""}>${esc(s)}</option>`).join("")}</select></label></div><p id="pick-count">${picks.length} candidates · unfilled coverage: ${esc(analysis.needs.join(" / ") || "No unfilled positions")}</p><div id="picks">${picks.map(w => `<article class="card" data-player-id="${esc(w.player_id)}">${playerTitle(player(w.player_id))}<p>${w.need_fit ? '<span class="pill">Fits roster need</span>' : ""}${w.points == null ? "Projection unavailable" : `<span class="points">${w.points.toFixed(1)} ${esc(current.league.projection_label || "pts / game from imported stats")}</span>`} · ${w.adds ? Number(w.adds).toLocaleString() + " global adds / 24h" : "Not in fetched trending list"}</p><details><summary>Why this player · sources</summary><p>${esc(w.reason)}</p><small>${source("Compare and claim")}${player(w.player_id).stats_source ? ` · <a href="${safeLink(player(w.player_id).stats_source)}">Stat source</a>` : ""}</small></details></article>`).join("") || '<p class="card">No verified candidates. Refresh data or change filters.</p>'}</div><p id="empty-filter" class="card" hidden>No matching candidates in this shortlist.</p>`;
    } else {
      const upcoming = deadlines().filter(d => Date.parse(d.at) > Date.now());
      const locks = team.starters.filter(id => id !== "0").map(id => ({p:player(id),g:kickoff(player(id))})).filter(x => x.g && Date.parse(x.g.at) > Date.now()).sort((a,b) => Date.parse(a.g.at)-Date.parse(b.g.at));
      html = `<h2>Today · ${esc(team.label || "Team " + team.id)}</h2><div class="jump-links" aria-label="Today sections"><button data-jump="lineup">Lineup</button><button data-jump="alerts">Alerts · ${analysis.alerts.length}</button><button data-jump="decisions">Deadlines</button></div><section id="lineup"><h2>Lineup checks</h2>${!stale() && analysis.lineup_moves?.length ? analysis.lineup_moves.map(m => `<article class="card"><strong>${esc(player(m.in).name)} for ${esc(player(m.out).name)}</strong><p>${esc(m.slot)} · ${m.gain == null ? "Point gain unavailable" : "+" + m.gain.toFixed(1) + " " + esc(current.league.projection_label || "imported points")} · ${esc(m.reason)}</p><small>Confirm both game locks in provider. ${source("Set lineup")}</small></article>`).join("") : '<p class="card">No verified projection-based swap. Check unavailable starters and game locks in your provider.</p>'}</section><section id="alerts"><h2>Player alerts</h2>${analysis.alerts.map(alertCard).join("") || '<p class="card">No injury or suspension status reported in this catalog snapshot. Recheck near kickoff.</p>'}</section><div class="grid"><article class="card" id="decisions"><h3>Next decisions</h3>${upcoming.length ? upcoming.map(d => `<p><strong>${esc(d.title)}</strong><br>${esc(fmt(d.at))}</p>`).join("") : '<p>Waiver cutoff needs confirmation. Open Rules, copy the cutoff from your league, then save it.</p>'}${locks.length ? `<p><strong>Next starter lock</strong><br>${esc(fmt(locks[0].g.at))} · ${esc(locks[0].p.name)}</p>` : '<p>Next starter kickoff unavailable. Check provider schedule before setting lineup.</p>'}<p>${(current.league.reminders || []).map(esc).join("<br>")}</p><p class="muted">NFL review rhythm: Tuesday claims; Thursday early game; Sunday inactive reports; Monday final check. These are suggested reviews, not league cutoffs.</p><button data-view="settings">Confirm deadlines</button> <button id="calendar">Export calendar</button></article><details class="card"><summary>Roster summary</summary><p>${analysis.alerts.length} status alerts · ${team.players.length} players</p><p>Unfilled position coverage: ${esc(analysis.needs.join(" / ") || "No unfilled positions")}</p><p>${analysis.faab_remaining == null ? "FAAB not applicable or not available" : `FAAB remaining: ${analysis.faab_remaining}`}</p><p>${source("Open your league")}</p><p class="muted">Hold / drop decisions need recovery timeline, roster value and an available replacement. No automatic drops from an injury badge.</p></details></div>`;
    }
    $("panel").innerHTML = html;
    if (view === "waivers" || view === "news") {
      (view === "waivers" && $("picks") ? $("picks") : $("panel").querySelector(".list"))?.insertAdjacentHTML("beforebegin", '<div class="pager"><button id="prev-page">Previous 5</button><span id="page-count" aria-live="polite"></span><button id="next-page">Next 5</button></div>');
      if (!$("page-count")) return;
      filterPage();
    }
  }
  async function chooseLeague() {
    const token = ++load;
    const entry = index.leagues.find(l => l.id === $("league").value);
    store.set("fgm.league.nfl",entry.id); page = 0; current = null; $("error").hidden = true;
    $("team").disabled = true; $("team").innerHTML = '<option>Loading roster…</option>'; $("connected").hidden = true; $("setup").hidden = !!entry.path; $("team-label").hidden = !entry.path;
    if (!entry.path) {
      $("meta").textContent = `${entry.provider.toUpperCase()} · connection required`;
      $("setup").innerHTML = `<h2>Connect ${esc(entry.provider.toUpperCase())}</h2><article class="card"><p>${esc(entry.name)}</p><p>Roster and scoring data not imported. This league has no recommendations yet.</p><p><a href="${safeLink(entry.source_url)}" target="_blank" rel="noopener">Open league</a> · <a href="nfl-refresh.html">Import instructions</a></p><p class="muted">Yahoo requires authorized API access or a roster export. ESPN NFL needs your league URL and local authorized access. Credentials stay off this site.</p></article>`;
      return;
    }
    $("meta").textContent = "Loading league roster…";
    try {
      const data = cache.get(entry.id) || await fetchJSON(entry.path);
      if (token !== load) return;
      cache.set(entry.id,data); if (cache.size > 2) cache.delete(cache.keys().next().value);
      current = data;
      $("team").innerHTML = '<option value="">Choose your roster</option>' + data.league.teams.map(t => `<option value="${esc(t.id)}">${esc(t.label || "Team " + t.id)} · ${t.players.length} players</option>`).join("");
      $("team").value = store.get("fgm.team." + entry.id) || data.league.my_team || "";
      $("team").disabled = false; $("team-label").hidden = $("sport").value === "wnba"; $("connected").hidden = false; render();
    } catch (e) { if (token === load) { $("error").hidden = false; $("error").textContent = `League unavailable: ${e.message}`; } }
  }
  async function fetchJSON(path) { const response = await fetch(path); if (!response.ok) throw new Error(`HTTP ${response.status}`); return response.json(); }
  $("sport").addEventListener("change", () => {
    const wnba = $("sport").value === "wnba"; store.set("fgm.sport",$("sport").value);
    $("wnba").hidden = !wnba; $("nfl").hidden = wnba; $("league-label").hidden = wnba; $("team-label").hidden = wnba || !current;
    if (wnba && !$("wnba-frame").getAttribute("src")) {
      if (!store.get("fgm_my_team")) store.set("fgm_my_team",index?.wnba?.my_team || "1");
      $("wnba-frame").src = index?.wnba?.path || "wnba.html";
      const note = $("wnba").querySelector("p");
      note.className = "notice";
      note.innerHTML = `WNBA · ${esc(index?.wnba?.name || "50-40-90 Club")} · snapshot ${esc(fmt(index?.wnba?.captured_at))}. ${age(index?.wnba?.captured_at) > 86400000 ? "Stale: refresh ESPN data before lineup or season decisions." : ""} <a href="wnba.html">Open full console</a>`;
    }
    if (!wnba) $("wnba-frame").removeAttribute("src");
  });
  $("league").addEventListener("change",chooseLeague);
  $("team").addEventListener("change", () => { store.set("fgm.team." + current.league.id,$("team").value); page = 0; render(); });
  document.addEventListener("click",event => {
    const button = event.target.closest("button"); if (!button || !current) return;
    if (button.dataset.view) { view = button.dataset.view; page = 0; render(); $("panel").scrollIntoView(); }
    if (button.dataset.jump) $(button.dataset.jump)?.scrollIntoView();
    if (button.id === "next-page" || button.id === "prev-page") {
      page += button.id === "next-page" ? 1 : -1; filterPage();
      $("panel").querySelector(".pager").scrollIntoView();
      $("page-count").setAttribute("tabindex","-1"); $("page-count").focus({preventScroll:true});
    }
    if (button.id === "clear-deadlines") { store.set("fgm.deadlines." + current.league.id,"[]"); render(); }
    if (button.id === "calendar") {
      const ds = deadlines().filter(d => Date.parse(d.at) > Date.now());
      if (!ds.length) { view = "settings"; render(); return; }
      const utc = s => new Date(s).toISOString().replace(/[-:]/g,"").replace(/\.\d{3}Z/,"Z");
      const clean = s => String(s).replace(/\\/g,"\\\\").replace(/[\r\n]/g," ").replace(/[,;]/g," ");
      const content = ["BEGIN:VCALENDAR","VERSION:2.0","PRODID:-//FantasyGM//Decision calendar//EN",...ds.flatMap((d,i) => ["BEGIN:VEVENT",`UID:${clean(current.league.id)}-${Date.parse(d.at)}-${i}@fantasygm`,`DTSTAMP:${utc(new Date())}`,`DTSTART:${utc(d.at)}`,`SUMMARY:${clean(current.league.name)}: ${clean(d.title)}`,`URL:${current.league.source_url}`,"BEGIN:VALARM","TRIGGER:-PT2H","ACTION:DISPLAY","DESCRIPTION:Fantasy decision due","END:VALARM","END:VEVENT"]),"END:VCALENDAR"].join("\r\n");
      const url = URL.createObjectURL(new Blob([content],{type:"text/calendar"})); const a = document.createElement("a"); a.href=url; a.download="fantasygm-deadlines.ics"; a.click(); setTimeout(() => URL.revokeObjectURL(url),1000);
    }
  });
  document.addEventListener("submit",event => {
    if (event.target.id !== "deadline-form") return; event.preventDefault();
    const at = new Date($("deadline-at").value); if (!Number.isFinite(at.getTime())) return;
    store.set("fgm.deadlines." + current.league.id,JSON.stringify([...deadlines().filter(d => Date.parse(d.at) > Date.now()),{title:$("deadline-title").value,at:at.toISOString(),source_url:current.league.source_url}])); view="today"; render();
  });
  function filterPage() {
    const term = ($("search")?.value || "").toLowerCase(), position = $("position")?.value || "";
    const cards = [...$("panel").querySelectorAll(view === "waivers" ? "#picks [data-player-id]" : ".list li")];
    const matches = cards.filter(card => {
      if (view !== "waivers") return true;
      const p = player(card.dataset.playerId);
      return p.name.toLowerCase().includes(term) && (!position || p.positions.includes(position));
    });
    page = Math.max(0, Math.min(page, Math.ceil(matches.length / 5) - 1));
    cards.forEach(card => { card.hidden = true; });
    matches.slice(page * 5, page * 5 + 5).forEach(card => { card.hidden = false; });
    $("prev-page").disabled = page === 0;
    $("next-page").disabled = (page + 1) * 5 >= matches.length;
    $("page-count").textContent = matches.length ? `${page * 5 + 1}–${Math.min(matches.length, page * 5 + 5)} of ${matches.length}` : "0 results";
    if ($("empty-filter")) $("empty-filter").hidden = matches.length !== 0;
    if ($("pick-count")) $("pick-count").textContent = `${matches.length} candidates · unfilled coverage: ${getAnalysis().needs.join(" / ") || "No unfilled positions"}`;
  }
  document.addEventListener("input",event => { if(event.target.id === "search") { page = 0; filterPage(); } });
  document.addEventListener("change",event => { if(event.target.id === "position") { page = 0; filterPage(); } });
  Promise.all([fetchJSON("data/football/index.json"),fetchJSON("data/football/nfl.json")]).then(([registry,sport]) => {
    index=registry; shared=sport;
    $("league").innerHTML=index.leagues.map(l => `<option value="${esc(l.id)}">${esc(l.name)}</option>`).join("");
    $("league").value=index.leagues.some(l => l.id === store.get("fgm.league.nfl")) ? store.get("fgm.league.nfl") : index.leagues[0].id;
    if(index.errors.length) { $("error").hidden=false; $("error").textContent=index.errors.join(" · "); }
    chooseLeague();
    $("sport").value=store.get("fgm.sport") || "nfl"; $("sport").dispatchEvent(new Event("change"));
  }).catch(e => {
    index = {errors:[],leagues:[{id:"nfl-setup",name:"NFL · Set up local refresh",provider:"sleeper",path:null,source_url:"https://docs.sleeper.com/"}]};
    shared = {players:{},news:[],games:[]};
    $("league").innerHTML = '<option value="nfl-setup">NFL · Set up local refresh</option>';
    chooseLeague();
    $("setup").innerHTML = `<h2>Build your NFL snapshot</h2><article class="card"><p>No local NFL snapshot available (${esc(e.message)}).</p><pre>python3 -m pipeline.football</pre><p><a href="nfl-refresh.html">Refresh / import instructions</a>. WNBA remains available in the sport selector.</p></article>`;
    $("sport").value = store.get("fgm.sport") || "nfl";
    $("sport").dispatchEvent(new Event("change"));
  });
})();
