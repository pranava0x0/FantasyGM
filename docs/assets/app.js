/* ============================================================
   Fantasy GM — frontend app.
   Reads ./data/state.json (built by pipeline.refresh) and renders
   waiver targets, Team Needs cards, the transaction log, and the
   per-player detail modal.

   No build step. No dependencies. ES2020+.
   ============================================================ */

(() => {
  "use strict";

  const STATE_URL = "./data/state.json";
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  // ---------- ESPN deep links ----------
  //
  // Advice-only by design (docs/gm-console-spec.md §5.1): ESPN's write API
  // has no public implementation, so every recommendation ends in a one-tap
  // link that lands the user in ESPN's own confirm flow. We stage the move;
  // ESPN applies it.
  //
  // The player-page shape is the one already in production (the modal's
  // "View on ESPN" link) and is known-good. The team shape is ESPN's
  // conventional fantasy team URL — spec §8 Q1 flags it for a live check
  // against the authenticated league, which needs the user's session.
  const ESPN_TEAM_PAGE = (leagueId, teamId) =>
    `https://fantasy.espn.com/wbasketball/team?leagueId=${leagueId}&teamId=${teamId}`;

  // ---------- Theme ----------
  const themeKey = "fgm-theme";
  function applyTheme(t) {
    document.documentElement.setAttribute("data-theme", t);
    try { localStorage.setItem(themeKey, t); } catch (_) { /* private mode */ }
  }
  function initTheme() {
    let t = null;
    try { t = localStorage.getItem(themeKey); } catch (_) {}
    if (!t) {
      t = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    }
    applyTheme(t);
  }
  // One delegated listener for all chrome, attached at parse time.
  //
  // Deliberately NOT per-element listeners in a post-fetch init: main()
  // awaits a ~2.8 MB state.json before it could bind them, which left the
  // bottom nav and team chip inert for the whole load on a slow connection —
  // taps silently doing nothing. Delegation makes navigation work from first
  // paint; the panels it reveals fill in when the data lands.
  document.addEventListener("click", (e) => {
    if (e.target.closest("#theme-toggle")) {
      const cur = document.documentElement.getAttribute("data-theme") || "light";
      applyTheme(cur === "light" ? "dark" : "light");
    }
    const tabBtn = e.target.closest(".tab");
    if (tabBtn) {
      const panelId = tabBtn.getAttribute("aria-controls");
      if (panelId) selectTab(panelId);
    }
    if (e.target.closest("[data-sheet-close]")) closeAllSheets();

    // Bottom-nav items and the More sheet's entries both just select a panel.
    const panelBtn = e.target.closest("[data-panel]");
    if (panelBtn) {
      closeAllSheets();
      selectTab(panelBtn.getAttribute("data-panel"));
    }

    const moreBtn = e.target.closest("#bottom-nav-more");
    if (moreBtn) {
      moreBtn.setAttribute("aria-expanded", "true");
      openSheet("more-sheet", moreBtn);
    }

    const chip = e.target.closest("#myteam-chip");
    if (chip) openSheet("team-picker", chip);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeAllSheets();
  });

  // ---------- My Team ----------
  //
  // The page knows whose GM it is. Everything sharpens from there: Today
  // leads with your lineup, waivers reorder to your roster, trades lead with
  // your best partner. No accounts, no backend — a localStorage key and an
  // optional ?team= override for sharing a link.
  //
  // League-wide stays one tap away (header chip → "Browse all teams"): the
  // symmetric view is a feature (it's the commissioner's view), not a
  // casualty of personalization.
  const MY_TEAM_KEY = "fgm_my_team";
  let MY_TEAM_ID = null;

  function readStoredTeamId() {
    try {
      const raw = localStorage.getItem(MY_TEAM_KEY);
      const n = Number(raw);
      return raw != null && Number.isFinite(n) ? n : null;
    } catch (_) { return null; }   // private mode
  }

  function setMyTeam(teamId, { persist = true } = {}) {
    MY_TEAM_ID = teamId == null ? null : Number(teamId);
    if (persist) {
      try {
        if (MY_TEAM_ID == null) localStorage.removeItem(MY_TEAM_KEY);
        else localStorage.setItem(MY_TEAM_KEY, String(MY_TEAM_ID));
      } catch (_) { /* private mode — selection lasts the session */ }
    }
    renderMyTeamChip();
  }

  function myTeam() {
    return MY_TEAM_ID == null ? null : TEAM_BY_ID.get(MY_TEAM_ID) || null;
  }

  // `?team=NUT` (abbrev, case-insensitive) or `?team=6` (id) overrides the
  // stored pick without clobbering it — so a shared link shows the sender's
  // team without hijacking the recipient's saved choice.
  function initMyTeam(teams) {
    const param = new URLSearchParams(location.search).get("team");
    if (param) {
      const wanted = param.trim().toLowerCase();
      const match = (teams || []).find(
        (t) => String(t.abbrev).toLowerCase() === wanted || String(t.team_id) === wanted,
      );
      if (match) { setMyTeam(match.team_id, { persist: false }); return; }
      toast(`No team "${param}" in this league.`);
    }
    const stored = readStoredTeamId();
    // A stored id from a prior season may no longer exist — verify before trusting.
    setMyTeam((teams || []).some((t) => t.team_id === stored) ? stored : null, { persist: false });
  }

  function renderMyTeamChip() {
    const label = $("#myteam-chip-label");
    const chip = $("#myteam-chip");
    if (!label || !chip) return;
    const t = myTeam();
    label.textContent = t ? t.abbrev : "Pick your team";
    chip.classList.toggle("myteam-chip--set", !!t);
    chip.setAttribute("aria-label", t ? `Your team: ${t.name}. Change team.` : "Choose your team");
  }

  // ---------- Tabs ----------
  function selectTab(panelId) {
    $$(".tab").forEach((b) => {
      b.setAttribute("aria-selected", b.getAttribute("aria-controls") === panelId ? "true" : "false");
    });
    $$(".tab-panel").forEach((p) => {
      if (p.id === panelId) p.removeAttribute("hidden");
      else p.setAttribute("hidden", "");
    });
    syncBottomNav(panelId);
    // Persist in the URL so a refresh keeps your tab and you can deep-link.
    const slug = panelId.replace("section-", "");
    if (location.hash.replace(/^#/, "") !== slug) {
      history.replaceState(null, "", `#${slug}`);
    }
    // Reset scroll so a tab change feels like a navigation, not a partial swap.
    window.scrollTo({ top: 0, behavior: "instant" });
  }
  function initTabsFromHash() {
    const slug = (location.hash || "").replace(/^#/, "").trim();
    const valid = $$(".tab-panel").map((p) => p.id.replace("section-", ""));
    if (slug && valid.includes(slug)) selectTab(`section-${slug}`);
  }
  window.addEventListener("hashchange", initTabsFromHash);

  // ---------- Sheets (team picker, More menu) ----------
  //
  // One primitive for both. On mobile these render as bottom sheets (CSS);
  // on desktop as centered dialogs. `lastSheetTrigger` restores focus on
  // close so keyboard users don't get dumped at the top of the document.
  let lastSheetTrigger = null;

  function openSheet(id, trigger) {
    const sheet = document.getElementById(id);
    if (!sheet) return;
    lastSheetTrigger = trigger || document.activeElement;
    sheet.removeAttribute("hidden");
    sheet.setAttribute("aria-hidden", "false");
    document.body.classList.add("modal-open");
    const focusable = sheet.querySelector("button:not([data-sheet-close]), [href]");
    if (focusable) focusable.focus();
  }

  function closeSheet(id) {
    const sheet = document.getElementById(id);
    if (!sheet) return;
    sheet.setAttribute("hidden", "");
    sheet.setAttribute("aria-hidden", "true");
    // Don't release the scroll lock while the player modal is still up.
    const modal = $("#player-modal");
    if (!modal || modal.hasAttribute("hidden")) document.body.classList.remove("modal-open");
    const moreBtn = $("#bottom-nav-more");
    if (moreBtn) moreBtn.setAttribute("aria-expanded", "false");
    if (lastSheetTrigger && document.contains(lastSheetTrigger)) lastSheetTrigger.focus();
    lastSheetTrigger = null;
  }

  function closeAllSheets() {
    ["more-sheet", "team-picker"].forEach(closeSheet);
  }

  // ---------- Toast ----------
  let toastTimer = null;
  function toast(msg) {
    const el = $("#toast");
    if (!el) return;
    el.textContent = msg;
    el.classList.add("visible");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove("visible"), 3000);
  }

  // ---------- Formatting helpers ----------
  function fmtPoints(n) {
    if (n == null || Number.isNaN(n)) return "—";
    return Number(n).toFixed(1);
  }
  function fmtPct(n) {
    if (n == null || Number.isNaN(n)) return "—";
    return `${Number(n).toFixed(1)}%`;
  }
  function fmtPctChange(n) {
    if (n == null || Number.isNaN(n)) return null;
    let v = Number(n);
    // Collapse values that round to zero so we never render "-0.0%".
    if (Math.abs(v) < 0.05) v = 0;
    const sign = v > 0 ? "+" : "";
    return `${sign}${v.toFixed(1)}%`;
  }
  function fmtDate(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "—";
    return d.toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric", timeZone: "UTC" });
  }
  // WNBA scoring periods are daily, so a game's date is the capture date
  // (= the current/latest period) shifted by the period delta. Anchored on
  // META.scoring_period_id / META.captured_at; returns a UTC Date or null.
  function periodToDate(period) {
    const anchorPeriod = Number(META.scoring_period_id);
    if (!anchorPeriod || !META.captured_at) return null;
    const base = new Date(META.captured_at);
    if (Number.isNaN(base.getTime())) return null;
    const d = new Date(Date.UTC(base.getUTCFullYear(), base.getUTCMonth(), base.getUTCDate()));
    d.setUTCDate(d.getUTCDate() + (period - anchorPeriod));
    return d;
  }
  // Compact "M/D" for the pill caption; falls back to "" when undatable.
  function shortGameDate(period) {
    const d = periodToDate(period);
    return d ? `${d.getUTCMonth() + 1}/${d.getUTCDate()}` : "";
  }
  // "Mon Jun 16" for the hover tooltip; falls back to the period number.
  function longGameDate(period) {
    const d = periodToDate(period);
    if (!d) return `Period ${period}`;
    return d.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" });
  }
  function fmtTime(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "—";
    const now = new Date();
    const sameDay = d.toDateString() === now.toDateString();
    if (sameDay) {
      return d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
    }
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric" }) + " " +
           d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  }

  // ---- Shared trade result helpers ----
  function fmtDelta(n) { return `${n >= 0 ? "+" : ""}${n.toFixed(1)}`; }
  function tradeDeltaClass(n) {
    return n > 0.05 ? "trade-result-delta--pos" : n < -0.05 ? "trade-result-delta--neg" : "trade-result-delta--zero";
  }
  function tradeVerdictLabel(dPpg) {
    if (dPpg > 0.5)  return { cls: "trade-result-verdict--win",  text: "Wins trade" };
    if (dPpg < -0.5) return { cls: "trade-result-verdict--lose", text: "Loses trade" };
    return { cls: "trade-result-verdict--even", text: "Even" };
  }
  function tradeSideHtml(side) {
    const { team, before, after, dWeek, dNext, dPpg, given, received } = side;
    const verdict   = tradeVerdictLabel(dPpg);
    const sideClass = verdict.cls.replace("trade-result-verdict--", "trade-result-side--");

    const movementHtml = [
      ...given.map(e =>
        `<div class="trade-result-player-row">
          <span class="trade-result-tag trade-result-tag--out">OUT</span>
          <button class="player-name-btn" data-player-id="${e.player.player_id}">${e.player.name}</button>
          <span class="trade-result-ppg">${fmtPoints(e.projected_per_game || 0)}/g</span>
        </div>`),
      ...received.map(e =>
        `<div class="trade-result-player-row">
          <span class="trade-result-tag trade-result-tag--in">IN</span>
          <button class="player-name-btn" data-player-id="${e.player.player_id}">${e.player.name}</button>
          <span class="trade-result-ppg">${fmtPoints(e.projected_per_game || 0)}/g</span>
        </div>`),
    ].join("");

    const receivedIds = new Set(received.map(e => e.player.player_id));
    const lineupHtml  = after.activeSlots.map(({ entry: e, sim_slot }) => {
      const isNew = receivedIds.has(e.player.player_id);
      return `<div class="trade-result-lineup-row${isNew ? " trade-result-lineup-row--new" : ""}">
        <span class="trade-result-slot">${sim_slot}</span>
        <button class="player-name-btn" data-player-id="${e.player.player_id}">${e.player.name}</button>
        <span class="trade-result-ppg">${fmtPoints(e.projected_per_game || 0)}/g</span>
        ${isNew ? '<span class="trade-result-tag trade-result-tag--in">NEW</span>' : ""}
      </div>`;
    }).join("");

    const newBench  = after.bench.filter(e => receivedIds.has(e.player.player_id));
    const benchHtml = newBench.length
      ? `<div class="trade-result-bench-sep">Bench (new)</div>` +
        newBench.map(e =>
          `<div class="trade-result-lineup-row trade-result-lineup-row--new">
            <span class="trade-result-slot">BE</span>
            <button class="player-name-btn" data-player-id="${e.player.player_id}">${e.player.name}</button>
            <span class="trade-result-ppg">${fmtPoints(e.projected_per_game || 0)}/g</span>
            <span class="trade-result-tag trade-result-tag--in">NEW</span>
          </div>`).join("")
      : "";

    return `<div class="trade-result-side ${sideClass}">
      <div class="trade-result-team-row">
        <span class="trade-result-team">${team.abbrev}</span>
        <span class="trade-result-verdict ${verdict.cls}">${verdict.text}</span>
      </div>
      <div class="trade-result-movement">${movementHtml}</div>
      <div class="trade-result-stats">
        <div class="trade-result-stat-row">
          <span class="trade-result-stat-label">This week</span>
          <span class="trade-result-stat-before">${fmtPoints(before.thisWeek)}</span>
          <span class="trade-result-stat-sep">→</span>
          <span class="trade-result-stat-after">${fmtPoints(after.thisWeek)}</span>
          <span class="trade-result-delta ${tradeDeltaClass(dWeek)}">${fmtDelta(dWeek)}</span>
        </div>
        ${(before.nextWeek > 0.5 || after.nextWeek > 0.5) ? `<div class="trade-result-stat-row">
          <span class="trade-result-stat-label">Next week</span>
          <span class="trade-result-stat-before">${fmtPoints(before.nextWeek)}</span>
          <span class="trade-result-stat-sep">→</span>
          <span class="trade-result-stat-after">${fmtPoints(after.nextWeek)}</span>
          <span class="trade-result-delta ${tradeDeltaClass(dNext)}">${fmtDelta(dNext)}</span>
        </div>` : ""}
        <div class="trade-result-stat-row">
          <span class="trade-result-stat-label">Proj/game</span>
          <span class="trade-result-stat-before">${fmtPoints(before.ppg)}/g</span>
          <span class="trade-result-stat-sep">→</span>
          <span class="trade-result-stat-after">${fmtPoints(after.ppg)}/g</span>
          <span class="trade-result-delta ${tradeDeltaClass(dPpg)}">${fmtDelta(dPpg)}/g</span>
        </div>
      </div>
      <div class="trade-result-ros-note">Proj/game × remaining schedule = rest-of-season impact</div>
      <div class="trade-result-lineup">
        <div class="trade-result-lineup-h">Simulated starting lineup</div>
        ${lineupHtml}${benchHtml}
      </div>
    </div>`;
  }

  function el(tag, opts = {}) {
    const e = document.createElement(tag);
    if (opts.className) e.className = opts.className;
    if (opts.text != null) e.textContent = String(opts.text);
    if (opts.html != null) e.innerHTML = opts.html;
    if (opts.attrs) for (const [k, v] of Object.entries(opts.attrs)) e.setAttribute(k, v);
    if (opts.children) opts.children.forEach((c) => c && e.appendChild(c));
    return e;
  }

  // Returns true when a YYYY-MM-DD summary date is older than 7 days.
  function _isSummaryStale(dateStr) {
    try {
      const d = new Date(dateStr + "T00:00:00Z");
      return (Date.now() - d.getTime()) > 7 * 24 * 60 * 60 * 1000;
    } catch (_) { return false; }
  }

  // Render any player name as a click-to-open <button> that drives the
  // detail modal. `className` keeps callers in charge of layout (e.g.
  // `waiver-name`, `roster-name`); the button just adds the trigger.
  // Listener is attached directly (not delegated) so we can stopPropagation
  // and prevent the parent team-card from toggling when a name is clicked.
  function playerNameBtn(playerId, name, className) {
    const btn = el("button", {
      className: `player-name-btn ${className || ""}`.trim(),
      text: name,
      attrs: {
        type: "button",
        "data-player-id": String(playerId),
        "aria-haspopup": "dialog",
      },
    });
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      lastFocusedTrigger = btn;
      openPlayerModal(String(playerId));
    });
    return btn;
  }

  // ---------- Render: meta strip ----------
  function renderMeta(meta) {
    $("#league-name").textContent = meta.league_name || "Fantasy GM";
    document.title = `${meta.league_name || "Fantasy GM"} · Fantasy GM`;
    $("#meta-period").textContent = meta.scoring_period_id ?? "—";
    $("#meta-matchup").textContent = meta.matchup_period_id ?? "—";
    $("#meta-season").textContent = meta.season_id ?? "—";
    $("#meta-teams").textContent = meta.team_count ?? "—";
    $("#meta-captured").textContent = fmtDate(meta.captured_at);
    const weekEl = $("#meta-week");
    if (weekEl) {
      if (meta.week_start_period != null && meta.week_end_period != null) {
        weekEl.textContent = `P${meta.week_start_period}–${meta.week_end_period}`;
      } else {
        weekEl.textContent = "—";
      }
    }
  }

  // Render `items` into `ulEl` (via `renderItemFn`), showing only the first
  // `pageSize` up front with a "Show N more" li that reveals the rest on
  // click — lets the player modal hold a full season of history without
  // dumping it all on open.
  function renderExpandableList(ulEl, items, pageSize, renderItemFn) {
    ulEl.replaceChildren();
    items.slice(0, pageSize).forEach((item) => ulEl.appendChild(renderItemFn(item)));
    if (items.length > pageSize) {
      const remaining = items.length - pageSize;
      const moreLi = el("li", { className: "player-modal-show-more" });
      const btn = el("button", {
        className: "player-modal-show-more-btn",
        text: `Show ${remaining} more`,
        attrs: { type: "button" },
      });
      btn.addEventListener("click", () => {
        items.slice(pageSize).forEach((item) => ulEl.insertBefore(renderItemFn(item), moreLi));
        moreLi.remove();
      });
      moreLi.appendChild(btn);
      ulEl.appendChild(moreLi);
    }
  }

  // ---------- Render: waiver targets ----------
  function bucketPill(bucket) {
    return el("span", {
      className: `pill outline bucket-${bucket}`,
      text: bucket,
    });
  }

  function gamesPill(games) {
    if (games == null) return null;
    const n = Number(games);
    let label, cls;
    if (n <= 0) { label = "BYE"; cls = "games-bye"; }
    else if (n === 1) { label = "1g · Tough"; cls = "games-low"; }
    else if (n === 2) { label = "2g · Light"; cls = "games-low"; }
    else if (n === 3) { label = "3g"; cls = "games-mid"; }
    else { label = `${n}g · Heavy`; cls = "games-heavy"; }
    return el("span", { className: `pill games-pill ${cls}`, text: label });
  }

  function weekBlock(games, pts, label) {
    const gamesNum = games != null ? games : 0;
    const ptsNum = pts != null ? pts : 0;
    let gamesCls = "games-mid";
    if (gamesNum <= 0) gamesCls = "games-bye";
    else if (gamesNum <= 2) gamesCls = "games-low";
    else if (gamesNum >= 4) gamesCls = "games-heavy";
    return el("div", {
      className: "week-block",
      children: [
        el("span", { className: "week-pts", text: fmtPoints(ptsNum) }),
        el("span", { className: `week-meta ${gamesCls}`, text: `${gamesNum}g · ${label}` }),
      ],
    });
  }

  // The headline number, team-scoped: what she adds to *your* optimal lineup.
  // A zero here is the most useful thing on the card — it means "she's good,
  // but not for you", which a raw projection actively hides.
  function netGainBlock(t) {
    const gain = t.net_gain_this_week || 0;
    const helps = gain > 0.05;
    const block = el("div", { className: "waiver-net" });
    block.appendChild(el("span", {
      className: `waiver-net-num ${helps ? "is-pos" : "is-zero"}`,
      text: helps ? `▲ +${fmtPoints(gain)}` : "—",
    }));
    block.appendChild(el("span", {
      className: "waiver-net-label",
      text: helps ? "net this wk" : "no lineup gain",
    }));
    // Always show the raw projection the net gain is derived from. The whole
    // point of this block is that the two numbers differ — hiding the input
    // would just replace one unexplained number with another.
    block.appendChild(el("span", {
      className: "waiver-net-sub",
      text: `${fmtPoints(t.projected_points_this_week)} proj`
        + (helps && t.net_gain_next_week != null && t.net_gain_next_week > 0.05
            ? ` · +${fmtPoints(t.net_gain_next_week)} next`
            : ""),
    }));
    return block;
  }

  // "ADD her · DROP K. Martin · bid $3–7" — the whole claim in one line.
  // A claim is a pair in a full-roster league, so naming only the add is
  // half an answer.
  function claimLine(t) {
    const drop = t.drop_candidate;
    const bid = t.bid_guidance;
    if (!drop && !bid) return null;

    const line = el("div", { className: "waiver-claim" });
    if (drop) {
      line.appendChild(el("span", { className: "waiver-claim-verb", text: "Drop" }));
      line.appendChild(playerNameBtn(drop.player_id, drop.player_name, "waiver-claim-name"));
      if (drop.injury_status && drop.injury_status !== "ACTIVE") {
        line.appendChild(el("span", { className: "own-neg injury-pill", text: drop.injury_status.replace(/_/g, " ") }));
      }
      line.appendChild(el("span", {
        className: "waiver-claim-cost",
        text: drop.net_loss > 0.05 ? `costs ${fmtPoints(drop.net_loss)}` : "costs nothing",
      }));
      // Spec §3.A2: never propose dropping a cornerstone silently. The pick
      // is still right on this week's points — the warning is about the rest
      // of the season, which weekly net-loss can't see.
      if (drop.is_core) {
        line.appendChild(el("span", {
          className: "pill core-pill",
          attrs: { title: "Top 6 on your roster by season rate. She may cost nothing this week only because her slate is light — dropping her is a rest-of-season downgrade." },
          text: "Core player",
        }));
      }
    }
    if (bid) {
      const bidEl = el("span", { className: "waiver-bid" });
      bidEl.appendChild(el("span", {
        className: "waiver-bid-amt",
        text: bid.suggested_lo === bid.suggested_hi ? `$${bid.suggested_lo}` : `$${bid.suggested_lo}–${bid.suggested_hi}`,
      }));
      // The band's provenance, always. Without the n this reads like a market
      // rate; with it, it reads as what it is — this league's own prices.
      bidEl.setAttribute(
        "title",
        `This league's ${bid.sample_n} executed claims: median $${bid.league_median}, high $${bid.league_max}, ` +
        `${bid.free_claims} went for $0. You have $${bid.faab_remaining ?? "?"}.`,
      );
      bidEl.appendChild(el("span", {
        className: "waiver-bid-ctx",
        text: `med $${bid.league_median} · hi $${bid.league_max} · n=${bid.sample_n}`,
      }));
      line.appendChild(bidEl);
    }
    return line;
  }

  function waiverCard(t, idx, fitBucket) {
    const p = t.player;
    const sub = el("span", { className: "waiver-sub" });
    sub.appendChild(bucketPill(p.bucket));
    if (p.team) sub.appendChild(el("span", { className: "pill team-mono", text: p.team }));
    if (t.projected_per_game != null) {
      sub.appendChild(el("span", { text: `${fmtPoints(t.projected_per_game)}/g` }));
    }
    if (t.percent_owned != null) {
      const pc = fmtPctChange(t.percent_change);
      sub.appendChild(el("span", { text: `${fmtPct(t.percent_owned)} owned` }));
      if (pc != null) {
        const cls = (t.percent_change || 0) > 0 ? "own-pos" : (t.percent_change || 0) < 0 ? "own-neg" : "";
        sub.appendChild(el("span", { className: `own-change ${cls}`.trim(), text: pc }));
      }
    }
    if (p.injury_status && p.injury_status !== "ACTIVE") {
      sub.appendChild(el("span", { className: "own-neg", text: p.injury_status }));
    }

    const nameLine = el("span", { className: "waiver-name" });
    nameLine.appendChild(playerNameBtn(p.player_id, p.name, "waiver-name-text"));
    // `fitBucket` is a needs bucket ("G" or "FC"), the player's is a position
    // ("G"/"F"/"C") — so FC has to match F or C. Comparing them directly
    // meant the pill silently never rendered for a frontcourt need.
    if (fitBucket && (fitBucket === "FC" ? (p.bucket === "F" || p.bucket === "C") : p.bucket === fitBucket)) {
      nameLine.appendChild(el("span", { className: "fit-pill", text: `Fits ${fitBucket === "FC" ? "F/C" : fitBucket}` }));
    }
    if (t.promoted_for_need) {
      nameLine.appendChild(el("span", { className: "fit-pill need", text: "Need" }));
    }
    if (t.injury_signal === "returning") {
      nameLine.appendChild(el("span", {
        className: "fit-pill returning",
        attrs: { title: "Missed 50%+ of team’s recent games — potential return from injury. Score boosted +15%." },
        text: "Returning",
      }));
    }

    const thisWkPts = t.projected_points_this_week ?? t.projected_points_next_period ?? 0;
    const nextWkPts = t.projected_points_next_week ?? 0;

    // Games this week. Load-bearing, not decoration: it's the usual reason a
    // high-rate player shows no lineup gain. Without it the card reads
    // "24.6/g · no lineup gain", which looks like a bug rather than "she only
    // plays twice this week".
    const gp = gamesPill(t.games_this_week);
    if (gp) sub.appendChild(gp);

    // Waivers 2.0 chips (spec §3.A4): why this player, why now. Rendered
    // before the projections so the *reason* precedes the arithmetic.
    (t.tags || []).forEach((tag) => {
      sub.appendChild(el("span", {
        className: `pill intent-pill intent-${tag}`,
        attrs: {
          title: tag === "streamer"
            ? "Heavy slate this week, ≤2 games next — the value is the schedule. Claim, start, then drop."
            : "Her rate holds and so does the slate. Worth the roster spot.",
        },
        text: tag === "streamer" ? "Streamer" : "Anchor",
      }));
    });
    if (t.plays_tonight) {
      sub.appendChild(el("span", { className: "pill tonight-pill", text: "Plays tonight" }));
    }

    const children = [
      el("span", { className: "waiver-rank", text: String(idx + 1) }),
      el("div", {
        className: "waiver-body",
        children: [nameLine, sub],
      }),
    ];

    // Team-scoped: the decision number is net gain over your current lineup,
    // not the raw projection (spec §3.A1). "31.4 proj" doesn't tell you
    // whether she'd even play for you; "+9.2 net" does.
    if (t.net_gain_this_week != null) {
      children.push(netGainBlock(t));
    } else {
      children.push(el("div", {
        className: "waiver-schedule",
        children: [
          weekBlock(t.games_this_week, thisWkPts, "this wk"),
          weekBlock(t.games_next_week, nextWkPts, "next wk"),
        ],
      }));
    }

    // The claim sheet: add/drop pairing + what to bid (spec §3.A2-A3). Only
    // when she'd actually improve the lineup — proposing a drop and a bid for
    // a player who never cracks the nine is advice to spend budget and a
    // roster spot for zero points.
    const claim = claimLine(t);
    if (claim) children.push(claim);

    // League-wide: no roster to measure against, so answer "who does she help
    // most?" instead of showing a net gain we can't compute (spec §3.A1).
    if ((t.best_fit || []).length) {
      const fit = el("div", { className: "waiver-fit" });
      fit.appendChild(el("span", { className: "waiver-fit-label", text: "Best fit" }));
      t.best_fit.forEach((f) => {
        fit.appendChild(el("span", {
          className: "waiver-fit-team",
          text: `${f.team_abbrev} +${fmtPoints(f.net_gain)}`,
        }));
      });
      children.push(fit);
    }

    // AI "why pick them up" take, spanning the full card width below the
    // top row. Clamped to keep the list scannable; full text in the modal.
    if (t.ai_summary) {
      const summaryDate = t.ai_summary_date || null;
      const isStale = summaryDate && _isSummaryStale(summaryDate);
      children.push(el("div", {
        className: "waiver-summary" + (isStale ? " waiver-summary--stale" : ""),
        children: [
          el("span", { className: "waiver-summary-badge", text: "AI" }),
          el("span", { className: "waiver-summary-text", text: t.ai_summary }),
          ...(summaryDate ? [el("span", { className: "waiver-summary-date", text: summaryDate })] : []),
        ],
      }));
    }

    return el("li", { className: "waiver-card", children });
  }

  // Scopes to My Team when one is picked (spec §6): the per-team list is
  // already reordered for your needs and carries the net-gain / drop / bid
  // fields. Falls back to the league-wide list otherwise — that view stays a
  // feature, not a casualty.
  function renderWaivers(state) {
    const list = $("#waiver-list");
    const sub = $("#waivers-sub");
    list.replaceChildren();

    const team = myTeam();
    const scoped = team
      ? (state.waiver_targets_by_team || []).find((r) => r.team_id === team.team_id)
      : null;
    const targets = scoped ? scoped.targets : (state.waiver_targets_overall || []);

    if (sub) {
      sub.textContent = team
        ? `Ranked by what each add gives ${team.abbrev}'s optimal lineup this week — not raw projection. ` +
          `A target with no gain is good but redundant for this roster. Bids are priced off the league's own claim history.`
        : "Ranked by projected points next week (per-game projection × games next week). " +
          "Pick your team in the header to see net gain over your own lineup, plus the drop and bid each claim implies.";
    }

    if (!targets.length) {
      list.appendChild(el("li", { className: "empty", text: "No waiver targets — pipeline hasn't run yet." }));
      return;
    }
    targets.forEach((t, i) => list.appendChild(waiverCard(t, i, team ? team.needs.top_need_bucket : null)));
  }

  // ---------- Render: team needs ----------
  function bucketRow(label, proj, gap, isTopNeed) {
    const cls = gap > 0 ? "pos" : gap < 0 ? "neg" : "zero";
    const sign = gap > 0 ? "+" : "";
    const max = Math.max(40, Math.abs(proj) * 1.2, 1);
    const pct = Math.min(100, (Math.max(0, proj) / max) * 100);
    const fill = el("span", { className: `bucket-fill ${isTopNeed ? "top-need" : ""}` });
    fill.style.width = `${pct}%`;
    return el("div", {
      className: "bucket-row",
      children: [
        bucketPill(label),
        el("div", { className: "bucket-bar", children: [fill] }),
        el("span", { className: `bucket-gap ${cls}`, text: `${sign}${gap.toFixed(1)}` }),
      ],
    });
  }

  // Frontcourt bar uses a dual-color fill so the F-vs-C split inside FC
  // stays readable without adding a third row. CSS handles via inline style.
  function frontcourtRow(forward_proj, center_proj, gap, isTopNeed) {
    const total = forward_proj + center_proj;
    const cls = gap > 0 ? "pos" : gap < 0 ? "neg" : "zero";
    const sign = gap > 0 ? "+" : "";
    const pill = el("span", { className: "pill outline bucket-F", text: "F/C" });
    const max = Math.max(40, Math.abs(total) * 1.2, 1);
    const pct = Math.min(100, (Math.max(0, total) / max) * 100);
    const fSplit = total > 0 ? Math.round((forward_proj / total) * 100) : 0;
    const fill = el("span", { className: `bucket-fill ${isTopNeed ? "top-need" : ""}` });
    fill.style.width = `${pct}%`;
    // F portion in amber, C in magenta — narrow stripe inside the bar.
    fill.style.background = isTopNeed
      ? "var(--gap-weak)"
      : `linear-gradient(to right, var(--bucket-forward) 0 ${fSplit}%, var(--bucket-center) ${fSplit}% 100%)`;
    return el("div", {
      className: "bucket-row",
      children: [
        pill,
        el("div", { className: "bucket-bar", children: [fill] }),
        el("span", { className: `bucket-gap ${cls}`, text: `${sign}${gap.toFixed(1)}` }),
      ],
    });
  }

  // Fills the 9 active WNBA fantasy slots (2 G, 3 F, 1 F/C, 3 UTIL) from a
  // roster, ranking candidates within each slot by `scoreFn` descending.
  // Shared by the trade calculator's post-trade simulation and the Team
  // Needs tab's next-week start/sit recommendation.
  function optimalLineupSlots(rosterEntries, scoreFn) {
    const ranked = [...rosterEntries].sort((a, b) => scoreFn(b) - scoreFn(a));
    const assigned = new Set();
    const activeSlots = [];

    function fill(slotLabel, n, test) {
      let filled = 0;
      for (const e of ranked) {
        if (filled >= n) break;
        const pid = e.player.player_id;
        if (!assigned.has(pid) && (e.projected_per_game || 0) > 0 && test(e)) {
          assigned.add(pid);
          activeSlots.push({ entry: e, sim_slot: slotLabel });
          filled++;
        }
      }
    }

    fill("G",    2, e => e.player.bucket === "G");
    fill("F",    3, e => e.player.bucket === "F");
    fill("F/C",  1, e => e.player.bucket === "F" || e.player.bucket === "C");
    fill("UTIL", 3, () => true);

    const bench = ranked.filter(e => !assigned.has(e.player.player_id));
    return { activeSlots, bench };
  }

  // Mirrors pipeline/analyze.py's _is_out() — statuses ESPN uses for
  // confirmed-unavailable players. DTD/QUESTIONABLE stay eligible (may play).
  const CONFIRMED_OUT_STATUSES = new Set(["OUT", "INJURY_RESERVE", "IR", "IR_LT_ACTIVE", "SUSPENDED"]);

  // Recommended starters for *next* week — ranks by projected_points_next_week
  // (per-game rate × next week's game count) rather than the flat per-game
  // rate, so a player with a bye/light slate next week correctly drops
  // behind a lower-ppg player who has more games. Confirmed-unavailable
  // players are excluded from the candidate pool entirely so they never
  // show as a recommended starter. Returns a Set of player_ids that should
  // start.
  function recommendedStartersNextWeek(rosterEntries) {
    const eligible = rosterEntries.filter((e) => !CONFIRMED_OUT_STATUSES.has(e.player.injury_status));
    const { activeSlots } = optimalLineupSlots(eligible, (e) => e.projected_points_next_week || 0);
    return new Set(activeSlots.map(({ entry }) => entry.player.player_id));
  }

  // ---------- Lineup panel (the "reset my lineup" surface) ----------
  //
  // Renders `team.lineup_check` from the pipeline: a minimal swap list and
  // the one scalar that says whether to bother — points left on the bench.
  // Empty state is a *win* state, not a scold (vocabulary discipline: this
  // page never says "lazy lineup" or "mistakes").

  // "7:00" — the tip-off, in the reader's own timezone.
  function fmtTipoff(iso) {
    if (!iso) return null;
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return null;
    return d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  }

  function lineupMoveRow(m, idx) {
    const row = el("li", { className: "lineup-move" });
    row.appendChild(el("span", { className: "lineup-move-rank", text: String(idx + 1) }));

    const body = el("div", { className: "lineup-move-body" });
    const inLine = el("span", { className: "lineup-move-line" });
    inLine.appendChild(el("span", { className: "lineup-move-verb", text: "Start" }));
    inLine.appendChild(playerNameBtn(m.player_in_id, m.player_in_name, "lineup-move-name"));

    const tip = fmtTipoff(m.player_in_game_time);
    const meta = [m.slot_label, tip ? `plays ${tip}` : null].filter(Boolean).join(" · ");
    inLine.appendChild(el("span", { className: "lineup-move-meta", text: `(${meta})` }));
    body.appendChild(inLine);

    // A "start" move fills an empty slot — there is no one to bench, and
    // saying "over nobody" would be nonsense.
    if (m.action === "swap" && m.player_out_id != null) {
      const outLine = el("span", { className: "lineup-move-line lineup-move-line--out" });
      outLine.appendChild(el("span", { className: "lineup-move-verb", text: "over" }));
      outLine.appendChild(playerNameBtn(m.player_out_id, m.player_out_name, "lineup-move-name"));
      outLine.appendChild(el("span", { className: "lineup-move-reason", text: `(${m.player_out_reason})` }));
      body.appendChild(outLine);
    } else {
      body.appendChild(el("span", {
        className: "lineup-move-line lineup-move-line--out",
        text: "into an open slot",
      }));
    }
    row.appendChild(body);
    row.appendChild(el("span", { className: "lineup-move-gain", text: `+${fmtPoints(m.gain_pts)}` }));
    return row;
  }

  // `horizon` picks which check to show: "tonight" falls back to the week
  // when nobody plays today (the pipeline sends tonight: null on an off day).
  function lineupPanel(team, { horizon = "tonight", compact = false } = {}) {
    const lc = team.lineup_check;
    // Older snapshots predate lineup_check. Render nothing rather than an
    // empty shell — the panel returns null and callers skip it.
    if (!lc || !lc.week) return null;

    const check = (horizon === "tonight" && lc.tonight) ? lc.tonight : lc.week;
    const isTonight = check.horizon === "tonight";
    const when = isTonight ? "tonight" : "this week";
    const hasMoves = check.status === "moves_available" && check.moves.length > 0;

    const panel = el("section", {
      className: `lineup-panel ${hasMoves ? "lineup-panel--moves" : "lineup-panel--set"}${compact ? " lineup-panel--compact" : ""}`,
      attrs: { "aria-label": `Lineup check for ${team.abbrev}` },
    });

    const head = el("div", { className: "lineup-panel-head" });
    head.appendChild(el("span", { className: "lineup-panel-icon", attrs: { "aria-hidden": "true" }, text: hasMoves ? "⚠" : "✓" }));
    if (hasMoves) {
      const n = check.moves.length;
      head.appendChild(el("span", {
        className: "lineup-panel-title",
        text: `${n} move${n === 1 ? "" : "s"} available ${when}`,
      }));
      head.appendChild(el("span", {
        className: "lineup-panel-headline",
        text: `${fmtPoints(check.points_left_on_bench)} pts on your bench`,
      }));
    } else {
      head.appendChild(el("span", { className: "lineup-panel-title", text: `Lineup set — optimal for ${when}` }));
    }
    panel.appendChild(head);

    if (hasMoves) {
      const list = el("ol", { className: "lineup-move-list" });
      check.moves.forEach((m, i) => list.appendChild(lineupMoveRow(m, i)));
      panel.appendChild(list);
    }

    const foot = el("div", { className: "lineup-panel-foot" });
    if (hasMoves) {
      foot.appendChild(el("a", {
        className: "lineup-apply-btn",
        text: "Apply on ESPN ↗",
        attrs: {
          href: ESPN_TEAM_PAGE(META.league_id, team.team_id),
          target: "_blank",
          rel: "noopener",
        },
      }));
    }
    // Say what the numbers are measured against — a projection with no stated
    // basis is just a vibe. Locked players are called out because their
    // absence from the moves list is otherwise unexplained.
    const notes = [];
    if (isTonight) notes.push(`Period ${check.computed_for_period}`);
    else notes.push(`Week P${lc.week_start_period}–${lc.week_end_period}`);
    if (check.locked_player_ids.length) {
      notes.push(`${check.locked_player_ids.length} already locked`);
    }
    foot.appendChild(el("span", { className: "lineup-panel-note", text: notes.join(" · ") }));
    panel.appendChild(foot);
    return panel;
  }

  function rosterTable(team) {
    const wrap = el("div", { className: "roster-table" });
    const nextWeekStarters = recommendedStartersNextWeek(team.roster || []);
    // Group by slot label so active slots come first (G, F, F/C, UTIL),
    // then bench. Preserves the slot order in pipeline/positions.py.
    const groups = new Map();
    (team.roster || []).forEach((r) => {
      const key = r.lineup_slot_label || "?";
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(r);
    });
    const ORDER = ["G", "F", "F/C", "UTIL", "BE", "G/F", "?"];
    const seenOrder = ORDER.filter((k) => groups.has(k))
                            .concat([...groups.keys()].filter((k) => !ORDER.includes(k)));
    seenOrder.forEach((slot) => {
      const rows = groups.get(slot) || [];
      // Sort active slots by projected points desc; bench too.
      rows.sort((a, b) => (b.projected_points_this_week || 0) - (a.projected_points_this_week || 0));
      rows.forEach((r) => wrap.appendChild(rosterRow(slot, r, nextWeekStarters.has(r.player.player_id))));
    });
    if (!seenOrder.length) wrap.appendChild(el("p", { className: "muted-cell", text: "Roster empty." }));
    return wrap;
  }

  function rosterRow(slotLabel, r, startsNextWeek) {
    const p = r.player;
    const slotChip = el("span", { className: `pill slot-chip ${r.is_active ? "slot-active" : "slot-bench"}`, text: slotLabel });
    const nameSpan = el("span", { className: "roster-name" });
    nameSpan.appendChild(playerNameBtn(p.player_id, p.name, "roster-name-text"));
    const sub = el("span", { className: "roster-sub" });
    sub.appendChild(bucketPill(p.bucket));
    if (p.team) sub.appendChild(el("span", { className: "pill team-mono", text: p.team }));
    const gp = gamesPill(r.games_this_week);
    if (gp) sub.appendChild(gp);
    if (p.injury_status && p.injury_status !== "ACTIVE") {
      sub.appendChild(el("span", { className: "own-neg", text: p.injury_status }));
    }
    sub.appendChild(el("span", {
      className: `pill nextwk-pill ${startsNextWeek ? "nextwk-start" : "nextwk-sit"}`,
      text: startsNextWeek ? "Start · next wk" : "Sit · next wk",
    }));
    const proj = r.projected_points_this_week != null
      ? r.projected_points_this_week
      : r.projected_points;
    return el("div", {
      className: `roster-row ${r.is_active ? "" : "is-bench"}`.trim(),
      children: [
        slotChip,
        el("div", { className: "roster-body", children: [nameSpan, sub] }),
        el("span", { className: "roster-pts", text: fmtPoints(proj) }),
      ],
    });
  }

  function teamTransactionsBlock(team, teamLookupByAbbr, txnsById) {
    const ids = team.recent_transaction_ids || [];
    if (!ids.length) return el("p", { className: "muted-cell", text: "No transactions yet this period." });
    const teamByIdMap = teamLookupByAbbr || new Map();
    const list = el("ul", { className: "txn-list compact" });
    ids.slice(0, 8).forEach((id) => {
      const tx = txnsById.get(String(id));
      if (!tx) return;
      list.appendChild(renderTxnCard(tx, teamByIdMap));
    });
    if (!list.children.length) {
      return el("p", { className: "muted-cell", text: "No matching transactions in the recent feed." });
    }
    return list;
  }

  // Renders a single txn card. Shared between the per-team pop-out and the
  // global Transactions tab.
  function renderTxnCard(tx, teamById) {
    const head = el("div", {
      className: "txn-head",
      children: [
        el("span", {
          className: `txn-type ${txTypeClass(tx.type)}`,
          text: (tx.type || "").replace(/_/g, " "),
        }),
        tx.team_id != null
          ? el("span", { className: "pill team-mono", text: teamById.get(tx.team_id)?.abbrev || `T${tx.team_id}` })
          : null,
        el("span", { className: "txn-time", text: fmtTime(tx.occurred_at) }),
        tx.bid_amount > 0 ? el("span", { text: `$${tx.bid_amount}` }) : null,
      ].filter(Boolean),
    });
    const items = el("div", { className: "txn-items" });
    (tx.items || []).forEach((it) => items.appendChild(txItemLine(it, teamById)));
    return el("li", { className: "txn-card", children: [head, items] });
  }

  function teamCard(team, perTeamTargets, allTeamsForLookup, txnsByIdLookup) {
    const n = team.needs;
    const head = el("div", {
      className: "team-head",
      children: [
        el("span", { className: "team-name", text: team.name }),
        el("span", { className: "team-abbr", text: team.abbrev }),
      ],
    });
    const meta = el("div", {
      className: "team-meta",
      children: [
        el("span", { text: `${team.record.wins}–${team.record.losses}${team.record.ties ? `–${team.record.ties}` : ""}` }),
        team.waiver_position != null ? el("span", { text: `Waiver #${team.waiver_position}` }) : null,
        team.faab_remaining != null ? el("span", { text: `$${team.faab_remaining} FAAB` }) : null,
      ].filter(Boolean),
    });
    const rows = el("div", {
      className: "bucket-rows",
      children: [
        bucketRow("G", n.guard_proj, n.guard_gap_vs_league, n.top_need_bucket === "G"),
        frontcourtRow(n.forward_proj, n.center_proj, n.frontcourt_gap_vs_league, n.top_need_bucket === "FC"),
      ],
    });

    // Lineup check rides at the top of every team card — it's the only
    // time-sensitive thing on the card, so it outranks the bucket bars.
    const lineup = lineupPanel(team, { compact: true });

    const detail = el("div", { className: "team-detail", attrs: { hidden: "" } });

    // Left column: summary + top picks
    const detailPrimary = el("div", { className: "team-detail-primary" });

    // Auto-generated summary bullets (marked with accent left-border per DESIGN.md).
    if (Array.isArray(team.summary) && team.summary.length > 0) {
      const sumBlock = el("div", { className: "team-summary", attrs: { "aria-label": "Auto-generated team summary" } });
      sumBlock.appendChild(el("h4", { text: "Summary · auto-generated" }));
      const ul = el("ul", { className: "team-summary-list" });
      team.summary.forEach((b) => ul.appendChild(el("li", { text: b })));
      sumBlock.appendChild(ul);
      detailPrimary.appendChild(sumBlock);
    }

    // Top picks for this team
    const needLabel = n.top_need_bucket === "FC" ? "F/C (frontcourt)" : n.top_need_bucket;
    detailPrimary.appendChild(el("h4", { className: "team-detail-head", text: `Top picks · top need: ${needLabel}` }));
    const list = el("div", { className: "team-targets" });
    (perTeamTargets || []).slice(0, 6).forEach((tgt, i) => {
      const p = tgt.player;
      const nameSpan = el("span", { className: "target-name" });
      nameSpan.appendChild(playerNameBtn(p.player_id, p.name, "target-name-text"));
      if (tgt.promoted_for_need) {
        nameSpan.appendChild(el("span", { className: "fit-pill need", text: "Need" }));
      }
      const sub = el("span", { className: "target-sub" });
      if (p.team) sub.appendChild(el("span", { className: "pill team-mono", text: p.team }));
      const adj = tgt.adjusted_score != null ? tgt.adjusted_score : (tgt.projected_points_this_week || tgt.base_score);
      list.appendChild(el("div", {
        className: "team-target-row",
        children: [
          el("span", { className: "target-rank", text: String(i + 1) }),
          el("div", {
            className: "target-body",
            children: [nameSpan, sub],
          }),
          bucketPill(p.bucket),
          el("div", {
            className: "waiver-schedule target-schedule",
            children: [
              weekBlock(tgt.games_this_week, adj, "this wk"),
              weekBlock(tgt.games_next_week, tgt.projected_points_next_week ?? 0, "next wk"),
            ],
          }),
        ],
      }));
    });
    if (!(perTeamTargets && perTeamTargets.length)) {
      list.appendChild(el("p", { className: "muted-cell", text: "No targets ranked." }));
    }
    detailPrimary.appendChild(list);

    // Left column: full roster + recent transactions
    const detailSecondary = el("div", { className: "team-detail-secondary" });
    detailSecondary.appendChild(el("h4", { className: "team-detail-head", text: "Full roster" }));
    detailSecondary.appendChild(el("p", {
      className: "roster-table-caption",
      text: "“Start/Sit · next wk” recommends the 9-slot lineup (2 G, 3 F, 1 F/C, 3 UTIL) that maximizes next week's projected points.",
    }));
    detailSecondary.appendChild(rosterTable(team));
    detailSecondary.appendChild(el("h4", { className: "team-detail-head", text: "Recent transactions" }));
    detailSecondary.appendChild(teamTransactionsBlock(team, allTeamsForLookup, txnsByIdLookup));
    detail.appendChild(detailSecondary);

    // Right column: summary + top picks
    detail.appendChild(detailPrimary);

    // The card is a div+role rather than <button> so we can nest the
    // clickable player-name buttons inside it (button-in-button is
    // invalid HTML). tabindex + Enter/Space handling keep it
    // keyboard-equivalent.
    const isMine = MY_TEAM_ID != null && team.team_id === MY_TEAM_ID;
    if (isMine) head.appendChild(el("span", { className: "team-mine-pill", text: "Your team" }));

    const card = el("div", {
      className: `team-card${isMine ? " team-card--mine" : ""}`,
      attrs: {
        role: "button",
        tabindex: "0",
        "aria-expanded": "false",
        "data-team-id": String(team.team_id),
      },
      children: [head, meta, lineup, rows, detail].filter(Boolean),
    });
    const toggle = () => {
      const expanded = card.getAttribute("aria-expanded") === "true";
      card.setAttribute("aria-expanded", expanded ? "false" : "true");
      if (expanded) detail.setAttribute("hidden", "");
      else detail.removeAttribute("hidden");
    };
    card.addEventListener("click", (e) => {
      // The "Apply on ESPN" link lives inside the card; following it must not
      // also collapse the card behind the new tab.
      if (e.target.closest("a")) { e.stopPropagation(); return; }
      toggle();
    });
    card.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        toggle();
      }
    });
    return card;
  }

  function renderTeams(teams, byTeam, transactions) {
    const grid = $("#team-grid");
    grid.replaceChildren();
    if (!teams || teams.length === 0) {
      grid.appendChild(el("p", { className: "empty", text: "No team data yet." }));
      return;
    }
    const targetIndex = new Map((byTeam || []).map((row) => [row.team_id, row.targets]));
    const teamByIdMap = new Map((teams || []).map((t) => [t.team_id, t]));
    const txnsByIdMap = new Map((transactions || []).map((tx) => [String(tx.transaction_id), tx]));
    // Your team sorts first; league order is otherwise untouched.
    const ordered = teams.slice().sort((a, b) => {
      if (a.team_id === MY_TEAM_ID) return -1;
      if (b.team_id === MY_TEAM_ID) return 1;
      return 0;
    });
    ordered.forEach((t) => grid.appendChild(teamCard(t, targetIndex.get(t.team_id), teamByIdMap, txnsByIdMap)));
  }

  // ---------- Render: Today (the standing daily brief) ----------
  //
  // The artifact no competitor builds: the page is already right when it
  // opens. Five blocks, each one action with one number and one tap-through,
  // targeted to fit a 375×812 viewport without scrolling.
  //
  // Everything here is a recomposition of data already on the page — Today
  // introduces no new numbers, so it can never disagree with the tab you'd
  // check to verify it.

  // An availability pill for a player we only know by id (trade packages
  // don't carry injury_status; the player index does).
  //
  // Load-bearing on the trade block: trades.py values every player at their
  // healthy per-game rate, so an OUT player is offered at full price with no
  // hint she's hurt. Repricing is Trades 2.0's job — until then the status
  // is at least visible, so the number is never the only thing the user sees.
  function injuryPillFor(playerId) {
    const p = PLAYER_INDEX.get(Number(playerId));
    const status = p && p.profile && p.profile.injury_status;
    if (!status || status === "ACTIVE") return null;
    return el("span", { className: "own-neg injury-pill", text: status.replace(/_/g, " ") });
  }

  function todayBlock(label, bodyEl, { action } = {}) {
    if (!bodyEl) return null;
    const head = el("div", { className: "today-block-head" });
    head.appendChild(el("h3", { className: "today-block-label", text: label }));
    if (action) head.appendChild(action);
    return el("section", { className: "today-block", children: [head, bodyEl] });
  }

  // Season form: each side's average weekly score from completed matchups.
  //
  // Deliberately NOT a win probability. The spec (§5.5) proposed a normal
  // approximation over weekly-score variance, and it was built and pulled:
  // that model can only say which team is better *on the season*, so it
  // ignores the live score and renders "52% odds" next to a 50-point lead —
  // a number that visibly contradicts the one above it.
  //
  // An honest matchup win-probability needs remaining-schedule modelling
  // (how many games each side has left in the period), which the current
  // state doesn't carry. Backlogged. Until then this shows the two averages
  // and lets the reader draw the comparison — no false precision.
  function seasonForm(team) {
    const hist = team.matchup_history || [];
    if (hist.length < 3) return null;
    return hist.reduce((s, r) => s + r.team_points, 0) / hist.length;
  }

  function matchupBlock(team) {
    const cm = team.current_matchup;
    if (!cm) return null;
    const opp = TEAM_BY_ID.get(cm.opponent_team_id);
    const lead = cm.team_points - cm.opponent_points;
    const wrap = el("div", { className: "today-matchup" });

    const score = el("div", { className: "today-matchup-score" });
    score.appendChild(el("span", { className: "today-matchup-abbr", text: team.abbrev }));
    score.appendChild(el("span", {
      className: `today-matchup-pts ${lead >= 0 ? "is-lead" : ""}`.trim(),
      text: fmtPoints(cm.team_points),
    }));
    score.appendChild(el("span", { className: "today-matchup-sep", text: "–" }));
    score.appendChild(el("span", {
      className: `today-matchup-pts ${lead < 0 ? "is-lead" : ""}`.trim(),
      text: fmtPoints(cm.opponent_points),
    }));
    score.appendChild(el("span", { className: "today-matchup-abbr", text: opp ? opp.abbrev : "—" }));
    wrap.appendChild(score);

    const sub = el("div", { className: "today-matchup-sub" });
    const verb = lead > 0 ? "up" : lead < 0 ? "down" : "level";
    sub.appendChild(el("span", {
      className: `today-matchup-lead ${lead > 0 ? "own-pos" : lead < 0 ? "own-neg" : ""}`.trim(),
      text: lead === 0 ? "Level" : `${verb} ${fmtPoints(Math.abs(lead))}`,
    }));
    const myForm = seasonForm(team);
    const oppForm = opp ? seasonForm(opp) : null;
    if (myForm != null && oppForm != null) {
      sub.appendChild(el("span", {
        className: "today-matchup-odds",
        text: `season avg ${Math.round(myForm)} vs ${Math.round(oppForm)}/wk`,
      }));
    }
    wrap.appendChild(sub);
    return wrap;
  }

  function claimBlock(state, team) {
    const row = (state.waiver_targets_by_team || []).find((r) => r.team_id === team.team_id);
    const top = (row && row.targets && row.targets[0]) || (state.waiver_targets_overall || [])[0];
    if (!top) return null;
    const p = top.player;
    const wrap = el("div", { className: "today-claim" });

    const nameLine = el("div", { className: "today-claim-name" });
    nameLine.appendChild(playerNameBtn(p.player_id, p.name, "today-claim-name-text"));
    nameLine.appendChild(bucketPill(p.bucket));
    if (p.team) nameLine.appendChild(el("span", { className: "pill team-mono", text: p.team }));
    if (top.promoted_for_need) nameLine.appendChild(el("span", { className: "fit-pill need", text: "Need" }));
    wrap.appendChild(nameLine);

    const stat = el("div", { className: "today-claim-stat" });
    stat.appendChild(el("span", {
      className: "today-hero-num",
      text: fmtPoints(top.projected_points_this_week ?? top.base_score),
    }));
    stat.appendChild(el("span", {
      className: "today-hero-unit",
      text: `proj this wk · ${top.games_this_week ?? 0} gms`,
    }));
    wrap.appendChild(stat);

    if (top.ai_summary) {
      wrap.appendChild(el("p", {
        className: "today-claim-take",
        children: [
          el("span", { className: "waiver-summary-badge", text: "AI" }),
          el("span", { text: top.ai_summary }),
        ],
      }));
    }
    return wrap;
  }

  function tradeBlock(state, team) {
    const sc = (state.trade_scenarios || []).find((s) => s.team_id === team.team_id);
    if (!sc || !sc.offers || !sc.offers.length) return null;
    const offer = sc.offers[0];
    const wrap = el("div", { className: "today-trade" });

    // "OUT"/"IN" here are trade directions, not availability — hence the
    // separate injury pill, which is why it reads "IN · Olivia Miles · OUT".
    const line = el("div", { className: "today-trade-line" });
    line.appendChild(el("span", { className: "trade-result-tag trade-result-tag--out", text: "OUT" }));
    line.appendChild(playerNameBtn(sc.best_player.player_id, sc.best_player.name, "today-trade-name"));
    const givePill = injuryPillFor(sc.best_player.player_id);
    if (givePill) line.appendChild(givePill);
    wrap.appendChild(line);

    const inLine = el("div", { className: "today-trade-line" });
    inLine.appendChild(el("span", { className: "trade-result-tag trade-result-tag--in", text: "IN" }));
    offer.pkg_received.players.forEach((pl, i) => {
      if (i) inLine.appendChild(el("span", { className: "trade-pkg-plus", text: "+" }));
      inLine.appendChild(playerNameBtn(pl.player_id, pl.name, "today-trade-name"));
      const pill = injuryPillFor(pl.player_id);
      if (pill) inLine.appendChild(pill);
    });
    wrap.appendChild(inLine);

    wrap.appendChild(el("div", {
      className: "today-trade-meta",
      children: [
        el("span", { text: `with ${offer.from_team_abbrev}` }),
        el("span", { text: `fit ${Math.round(offer.need_fit_score * 100)}%` }),
      ],
    }));
    return wrap;
  }

  function teamPickerPrompt() {
    const wrap = el("div", { className: "today-empty" });
    wrap.appendChild(el("p", {
      className: "today-empty-h",
      text: "Pick your team to get your daily brief.",
    }));
    wrap.appendChild(el("p", {
      className: "today-empty-sub",
      text: "Tonight's lineup moves, your top claim, and your best trade opener — scoped to your roster.",
    }));
    const btn = el("button", { className: "today-empty-btn", text: "Choose team", attrs: { type: "button" } });
    btn.addEventListener("click", () => openSheet("team-picker", btn));
    wrap.appendChild(btn);
    return wrap;
  }

  function renderToday(state) {
    const mount = $("#today-mount");
    if (!mount) return;
    mount.replaceChildren();

    const team = myTeam();
    if (!team) { mount.appendChild(teamPickerPrompt()); return; }

    const head = el("header", { className: "today-head" });
    head.appendChild(el("h2", { className: "today-h", text: team.name }));
    head.appendChild(el("span", {
      className: "today-sub",
      text: `${team.record.wins}–${team.record.losses}${team.record.ties ? `–${team.record.ties}` : ""}` +
            (team.faab_remaining != null ? ` · $${team.faab_remaining} FAAB` : ""),
    }));
    mount.appendChild(head);

    const grid = el("div", { className: "today-grid" });
    const blocks = [
      todayBlock("Lineup", lineupPanel(team, { horizon: "tonight" })),
      todayBlock("Matchup", matchupBlock(team)),
      todayBlock("Top claim", claimBlock(state, team), {
        action: tabLink("Waivers", "section-waivers"),
      }),
      todayBlock("Trade opener", tradeBlock(state, team), {
        action: tabLink("Trades", "section-trades"),
      }),
    ].filter(Boolean);
    blocks.forEach((b) => grid.appendChild(b));
    mount.appendChild(grid);

    if (Array.isArray(team.summary) && team.summary.length) {
      const note = el("div", { className: "today-note", attrs: { "aria-label": "Auto-generated team note" } });
      note.appendChild(el("span", { className: "today-note-badge", text: "AUTO" }));
      note.appendChild(el("span", { className: "today-note-text", text: team.summary.slice(0, 2).join(" ") }));
      mount.appendChild(note);
    }
  }

  function tabLink(label, panelId) {
    const b = el("button", { className: "today-block-link", text: `${label} →`, attrs: { type: "button" } });
    b.addEventListener("click", () => selectTab(panelId));
    return b;
  }

  // ---------- Render: news ----------
  function newsCard(n, playerNameById) {
    const tags = el("div", { className: "news-tags" });
    (n.athlete_ids || []).forEach((aid) => {
      const name = playerNameById.get(aid);
      if (name) tags.appendChild(playerNameBtn(aid, name, "pill team-mono news-player"));
    });
    const pubLine = n.published_at
      ? el("span", { className: "news-time", text: fmtTime(n.published_at) })
      : null;
    const body = [
      el("a", {
        className: "news-headline",
        attrs: { href: n.url || "#", target: "_blank", rel: "noopener" },
        text: n.headline,
      }),
    ];
    if (n.description) body.push(el("p", { className: "news-desc", text: n.description }));
    if (pubLine) body.push(pubLine);
    if (tags.children.length) body.push(tags);
    return el("li", { className: "news-card", children: body });
  }

  function renderNews(news, teams) {
    const list = $("#news-list");
    list.replaceChildren();
    if (!news || news.length === 0) {
      list.appendChild(el("li", { className: "empty", text: "No news yet — pipeline hasn't fetched the WNBA feed." }));
      return;
    }
    const playerNameById = new Map();
    (teams || []).forEach((t) => {
      (t.roster || []).forEach((r) => playerNameById.set(r.player.player_id, r.player.name));
    });
    news.slice(0, 12).forEach((n) => list.appendChild(newsCard(n, playerNameById)));
  }

  // ---------- Render: transactions ----------

  // Mirror of pipeline/positions.py:LINEUP_SLOT_LABEL — keep in sync.
  // Slot -1 is ESPN's "no slot" sentinel (player wasn't in a lineup);
  // we render that as null so the formatter knows to skip it.
  const SLOT_LABEL = {
    0: "BE", 1: "G", 2: "F", 3: "G/F", 4: "F",
    5: "F/C", 6: "UTIL", 7: "BE", 8: "BE",
  };
  function slotLabel(id) {
    if (id == null || id < 0) return null;
    return SLOT_LABEL[id] || `S${id}`;
  }
  function teamAbbr(id, teamById) {
    if (id == null || id === 0) return null;  // 0 = "no team" / FA pool
    return teamById.get(id)?.abbrev || `T${id}`;
  }

  function txTypeClass(t) {
    const k = (t || "").toUpperCase();
    if (k.includes("ADD")) return "add";
    if (k.includes("DROP")) return "drop";
    if (k.includes("TRADE")) return "trade";
    return "lineup";
  }

  function txItemLine(it, teamById) {
    const fromTeam = teamAbbr(it.from_team_id, teamById);
    const toTeam   = teamAbbr(it.to_team_id, teamById);
    const fromSlot = slotLabel(it.from_slot_id);
    const toSlot   = slotLabel(it.to_slot_id);

    // Build a human "from → to" string. Each side may be a team, a slot,
    // both, or neither. Empty sides are rendered as "FA" so the direction
    // of the move is always legible.
    const fromParts = [fromTeam, fromSlot].filter(Boolean);
    const toParts   = [toTeam,   toSlot  ].filter(Boolean);
    const fromText = fromParts.length ? fromParts.join(" · ") : "FA";
    const toText   = toParts.length   ? toParts.join(" · ")   : "FA";

    // Name is clickable when we have a real player_id (always true for ESPN
    // transaction items); the strong wrapper keeps the visual weight.
    const nameStrong = el("strong");
    if (it.player_id != null) {
      nameStrong.appendChild(playerNameBtn(it.player_id, it.player_name || `#${it.player_id}`, "txn-name-text"));
    } else {
      nameStrong.textContent = it.player_name || `#${it.player_id}`;
    }
    const pieces = [nameStrong];
    // Only render the arrow chunk when there's an actual change.
    if (fromText !== toText) {
      pieces.push(
        el("span", { text: fromText }),
        el("span", { className: "txn-arrow", text: "→" }),
        el("span", { text: toText }),
      );
    }
    return el("span", { className: "txn-item", children: pieces });
  }
  function renderTxns(txns, teams) {
    const list = $("#txn-list");
    list.replaceChildren();
    if (!txns || txns.length === 0) {
      list.appendChild(el("li", { className: "empty", text: "No transactions yet this period." }));
      return;
    }
    const teamById = new Map((teams || []).map((t) => [t.team_id, t]));
    const showing = Math.min(txns.length, 50);
    txns.slice(0, showing).forEach((tx) => list.appendChild(renderTxnCard(tx, teamById)));
    if (txns.length > showing) {
      const more = el("li", { className: "muted-cell", text: `Showing ${showing} of ${txns.length} total` });
      list.appendChild(more);
    }
  }

  // ---------- Player detail modal ----------
  //
  // A single modal at the body level is populated on demand from a
  // per-player index built once when state.json loads. Index keys are
  // string player IDs (matches the `data-player-id` attribute we put on
  // every clickable player name). The index covers everything we know
  // locally — there's no extra network call to open the modal.
  //
  // Long-term, this is the foundation for the player-page backlog item:
  // when we add per-player splits / cumulative transaction history, the
  // schema flows into the index, the modal picks up new sections.

  // ESPN player headshot URL pattern. Some retired or minor players don't
  // have a headshot — the <img> error handler hides the broken icon.
  const ESPN_HEADSHOT = (pid) =>
    `https://a.espncdn.com/i/headshots/wnba/players/full/${pid}.png`;
  const ESPN_PLAYER_PAGE = (pid) =>
    `https://www.espn.com/wnba/player/_/id/${pid}`;

  // Player ID -> { profile, rosteredBy, waiverTarget, perTeamTarget,
  //                news, txns }. Set once on bootstrap.
  let PLAYER_INDEX = new Map();
  // Team ID -> team object, used by the modal for "rostered by".
  let TEAM_BY_ID = new Map();
  // League meta (scoring period + capture date), used to date recent games.
  let META = {};
  // Element to restore focus to when the modal closes.
  let lastFocusedTrigger = null;

  function buildPlayerIndex(state) {
    const idx = new Map();
    META = state.meta || {};
    const teamsById = new Map((state.teams || []).map((t) => [t.team_id, t]));
    TEAM_BY_ID = teamsById;

    const ensure = (pid) => {
      if (!idx.has(pid)) {
        idx.set(pid, {
          profile: null,
          rostered_by: null,
          waiver_target: null,
          per_team_targets: [],
          news: [],
          txns: [],
        });
      }
      return idx.get(pid);
    };

    (state.teams || []).forEach((t) => {
      (t.roster || []).forEach((r) => {
        const entry = ensure(r.player.player_id);
        entry.profile = entry.profile || r.player;
        entry.rostered_by = {
          team_id: t.team_id,
          team_name: t.name,
          team_abbrev: t.abbrev,
          lineup_slot_label: r.lineup_slot_label,
          is_active: r.is_active,
          projected_per_game: r.projected_per_game,
          projected_points_this_week: r.projected_points_this_week,
          games_this_week: r.games_this_week,
        };
      });
    });

    (state.waiver_targets_overall || []).forEach((t) => {
      const entry = ensure(t.player.player_id);
      entry.profile = entry.profile || t.player;
      entry.waiver_target = t;
      if (t.ai_summary) entry.ai_summary = t.ai_summary;
      if (t.ai_summary_date) entry.ai_summary_date = t.ai_summary_date;
    });

    (state.waiver_targets_by_team || []).forEach((row) => {
      const teamMeta = teamsById.get(row.team_id);
      (row.targets || []).forEach((t) => {
        const entry = ensure(t.player.player_id);
        entry.profile = entry.profile || t.player;
        if (t.ai_summary && !entry.ai_summary) entry.ai_summary = t.ai_summary;
        if (t.ai_summary_date && !entry.ai_summary_date) entry.ai_summary_date = t.ai_summary_date;
        entry.per_team_targets.push({
          team_id: row.team_id,
          team_name: teamMeta ? teamMeta.name : `Team ${row.team_id}`,
          team_abbrev: teamMeta ? teamMeta.abbrev : `T${row.team_id}`,
          adjusted_score: t.adjusted_score,
          projected_points_this_week: t.projected_points_this_week,
          promoted_for_need: !!t.promoted_for_need,
        });
      });
    });

    // News tagged with this player's athleteId or team. Already capped
    // server-side (build_state.MAX_HISTORY_PER_PLAYER); the modal paginates
    // via "show more" rather than truncating here.
    Object.entries(state.news_by_player || {}).forEach(([pid, articles]) => {
      const num = Number(pid);
      if (!Number.isFinite(num)) return;
      const entry = ensure(num);
      entry.news = articles || [];
    });

    // Reddit r/wnba posts mentioning this player. Capped server-side.
    Object.entries(state.reddit_posts_by_player || {}).forEach(([pid, posts]) => {
      const num = Number(pid);
      if (!Number.isFinite(num)) return;
      const entry = ensure(num);
      entry.reddit = posts || [];
    });

    // Twitter/X posts mentioning this player. Capped server-side.
    Object.entries(state.twitter_posts_by_player || {}).forEach(([pid, tweets]) => {
      const num = Number(pid);
      if (!Number.isFinite(num)) return;
      const entry = ensure(num);
      entry.twitter = tweets || [];
    });

    // Bluesky posts mentioning this player. Capped server-side.
    Object.entries(state.bluesky_posts_by_player || {}).forEach(([pid, posts]) => {
      const num = Number(pid);
      if (!Number.isFinite(num)) return;
      const entry = ensure(num);
      entry.bluesky = posts || [];
    });

    // Instagram posts from/about this player. Capped server-side.
    Object.entries(state.instagram_posts_by_player || {}).forEach(([pid, posts]) => {
      const num = Number(pid);
      if (!Number.isFinite(num)) return;
      const entry = ensure(num);
      entry.instagram = posts || [];
    });

    // Social profile handles (for links in the modal footer).
    Object.entries(state.player_socials || {}).forEach(([pid, handles]) => {
      const num = Number(pid);
      if (!Number.isFinite(num)) return;
      const entry = ensure(num);
      entry.socials = handles;
    });

    // Transactions where this player appears in any line item.
    (state.transactions_recent || []).forEach((tx) => {
      (tx.items || []).forEach((it) => {
        if (it.player_id == null) return;
        const entry = ensure(it.player_id);
        entry.txns.push({ tx, item: it });
      });
    });

    return idx;
  }

  function openPlayerModal(playerId) {
    const modal = $("#player-modal");
    if (!modal) return;
    const entry = PLAYER_INDEX.get(Number(playerId));
    if (!entry || !entry.profile) {
      toast("No details on this player yet.");
      return;
    }
    populatePlayerModal(entry);
    modal.removeAttribute("hidden");
    modal.setAttribute("aria-hidden", "false");
    document.body.classList.add("modal-open");
    // Focus the close button so Esc / Enter work immediately.
    const closeBtn = modal.querySelector(".player-modal-close");
    if (closeBtn) closeBtn.focus();
  }

  function closePlayerModal() {
    const modal = $("#player-modal");
    if (!modal) return;
    modal.setAttribute("hidden", "");
    modal.setAttribute("aria-hidden", "true");
    document.body.classList.remove("modal-open");
    if (lastFocusedTrigger && document.contains(lastFocusedTrigger)) {
      lastFocusedTrigger.focus();
    }
    lastFocusedTrigger = null;
  }

  function populatePlayerModal(entry) {
    const p = entry.profile;
    const photo = $("#player-modal-photo") || $(".player-modal-photo");
    if (photo) {
      photo.src = ESPN_HEADSHOT(p.player_id);
      photo.alt = `${p.name} headshot`;
      photo.classList.remove("is-fallback");
      photo.onerror = () => {
        // Hide the broken image; fall back to initials block via CSS class.
        photo.classList.add("is-fallback");
        photo.removeAttribute("src");
      };
    }

    $("#player-modal-name").textContent = p.name;

    const metaWrap = $("#player-modal-meta");
    metaWrap.replaceChildren();
    metaWrap.appendChild(bucketPill(p.bucket));
    if (p.team) metaWrap.appendChild(el("span", { className: "pill team-mono", text: p.team }));
    if (p.position && p.position !== p.bucket) {
      metaWrap.appendChild(el("span", { className: "player-modal-pos", text: p.position }));
    }
    if (p.injury_status && p.injury_status !== "ACTIVE") {
      metaWrap.appendChild(el("span", { className: "own-neg player-modal-injury", text: p.injury_status }));
    }

    const statusWrap = $("#player-modal-status");
    statusWrap.replaceChildren();
    if (entry.rostered_by) {
      const r = entry.rostered_by;
      const slot = r.lineup_slot_label || "?";
      const activeLabel = r.is_active ? "active" : "bench";
      statusWrap.appendChild(el("span", {
        className: "player-modal-status-line",
        text: `Rostered by ${r.team_name} (${r.team_abbrev}) · ${slot} · ${activeLabel}`,
      }));
    } else if (entry.waiver_target) {
      statusWrap.appendChild(el("span", {
        className: "player-modal-status-line player-modal-status-fa",
        text: `Free agent · ${fmtPct(entry.waiver_target.percent_owned)} owned league-wide`,
      }));
    } else {
      statusWrap.appendChild(el("span", {
        className: "player-modal-status-line muted",
        text: "Not currently rostered in this league.",
      }));
    }

    // Stats grid — pull from whichever context has them.
    const r = entry.rostered_by;
    const wt = entry.waiver_target;
    const perGame = r?.projected_per_game ?? wt?.projected_per_game;
    const weekProj = r?.projected_points_this_week ?? wt?.projected_points_this_week;
    const games = r?.games_this_week ?? wt?.games_this_week;
    const seasonAvg = wt?.season_avg_points;
    const ownership = wt?.percent_owned;
    const ownChange = wt?.percent_change;

    const statsWrap = $("#player-modal-stats");
    statsWrap.replaceChildren();
    statsWrap.appendChild(statBlock("This week", fmtPoints(weekProj), "proj pts"));
    statsWrap.appendChild(statBlock("Per game", fmtPoints(perGame), "proj"));
    statsWrap.appendChild(statBlock("Games", games != null ? String(games) : "—", "this week"));
    if (seasonAvg != null) statsWrap.appendChild(statBlock("Season", fmtPoints(seasonAvg), "actual /g"));
    if (ownership != null) {
      const ch = fmtPctChange(ownChange);
      const sub = ch ? `${ch} 7d` : "owned";
      statsWrap.appendChild(statBlock("Ownership", fmtPct(ownership), sub));
    }
    // Force a fixed 3-column grid so orphan stats (e.g. 5th item) stay at
    // column-width and don't stretch to fill the row.
    statsWrap.style.gridTemplateColumns = "repeat(3, 1fr)";

    // GM take — AI-authored "why pick them up". Hidden when absent.
    const gmTake = $("#player-modal-gmtake");
    const gmBody = $("#player-modal-gmtake-body");
    if (gmTake && gmBody) {
      if (entry.ai_summary) {
        gmBody.textContent = entry.ai_summary;
        const dateEl = gmTake.querySelector(".gm-take-date");
        if (dateEl && entry.ai_summary_date) {
          const stale = _isSummaryStale(entry.ai_summary_date);
          dateEl.textContent = "as of " + entry.ai_summary_date + (stale ? " · may be stale" : "");
          dateEl.classList.toggle("gm-take-date--stale", stale);
          dateEl.removeAttribute("hidden");
        } else if (dateEl) {
          dateEl.setAttribute("hidden", "");
        }
        gmTake.removeAttribute("hidden");
      } else {
        gmBody.textContent = "";
        gmTake.setAttribute("hidden", "");
      }
    }

    // Recent games (last 2 weeks) — row of score pills, newest first.
    const recentWrap = $("#player-modal-recent-wrap");
    const recentGames = entry.waiver_target?.recent_games || [];
    if (recentWrap) {
      if (recentGames.length) {
        recentWrap.removeAttribute("hidden");
        const container = $("#player-modal-recent-games");
        container.replaceChildren();
        const max = Math.max(...recentGames.map((g) => g.fantasy_points));
        recentGames.forEach((g) => {
          const isHigh = g.fantasy_points === max && max >= 20;
          container.appendChild(el("span", {
            className: "recent-game",
            attrs: { title: `${longGameDate(g.scoring_period_id)} · ${g.fantasy_points} fpts` },
            children: [
              el("span", {
                className: `recent-game-pill${isHigh ? " recent-game-pill--high" : ""}`,
                text: String(Math.round(g.fantasy_points)),
              }),
              el("span", { className: "recent-game-date", text: shortGameDate(g.scoring_period_id) }),
            ],
          }));
        });
        const avg = recentGames.reduce((s, g) => s + g.fantasy_points, 0) / recentGames.length;
        container.appendChild(el("span", { className: "recent-game-avg", text: `avg ${avg.toFixed(1)}` }));
      } else {
        recentWrap.setAttribute("hidden", "");
      }
    }

    // Social — Reddit + Twitter + Bluesky + Instagram merged, sorted newest-first.
    const socialWrap = $("#player-modal-social-wrap");
    const socialList = $("#player-modal-social");
    if (socialWrap && socialList) {
      const allPosts = [
        ...(entry.reddit || []).map((post) => ({
          source: "reddit", label: `r/${post.subreddit || "wnba"}`,
          title: post.title, url: post.url, ts: post.published_at,
        })),
        ...(entry.twitter || []).map((post) => ({
          source: "twitter", label: post.screen_name ? `@${post.screen_name}` : "X",
          title: post.title, url: post.url, ts: post.published_at,
        })),
        ...(entry.bluesky || []).map((post) => ({
          source: "bluesky", label: post.handle ? `@${post.handle}` : "Bluesky",
          title: post.title, url: post.url, ts: post.published_at,
        })),
        ...(entry.instagram || []).map((post) => ({
          source: "instagram",
          label: post.username ? `@${post.username}` : "Instagram",
          title: post.title, url: post.url, ts: post.published_at,
        })),
      ].sort((a, b) => (b.ts || "").localeCompare(a.ts || ""));

      if (allPosts.length) {
        socialWrap.removeAttribute("hidden");
        renderExpandableList(socialList, allPosts, 8, (post) => {
          const li = el("li", { className: "social-item" });
          li.appendChild(el("span", { className: `social-badge social-badge--${post.source}`, text: post.label }));
          li.appendChild(el("a", {
            className: "social-title",
            text: post.title,
            attrs: { href: post.url || "#", target: "_blank", rel: "noopener noreferrer" },
          }));
          if (post.ts) li.appendChild(el("span", { className: "social-time", text: fmtTime(post.ts) }));
          return li;
        });
      } else {
        socialWrap.setAttribute("hidden", "");
      }
    }

    // News — compact: headline + time + direct/team tag only (no description).
    const newsList = $("#player-modal-news");
    if (entry.news && entry.news.length) {
      renderExpandableList(newsList, entry.news, 5, (n) => {
        const isDirectMention = Array.isArray(n.athlete_ids) && n.athlete_ids.includes(p.player_id);
        const li = el("li", { className: "player-modal-news-item" });
        const row = el("div", { className: "player-modal-news-row" });
        row.appendChild(el("a", {
          className: "player-modal-news-headline",
          text: n.headline,
          attrs: { href: n.url || "#", target: "_blank", rel: "noopener" },
        }));
        const meta = el("span", { className: "player-modal-news-meta" });
        if (n.published_at) meta.appendChild(el("span", { className: "player-modal-news-time", text: fmtTime(n.published_at) }));
        if (!isDirectMention) meta.appendChild(el("span", { className: "player-modal-news-team-tag", text: "Team" }));
        row.appendChild(meta);
        li.appendChild(row);
        return li;
      });
    } else {
      newsList.replaceChildren();
      newsList.appendChild(el("li", { className: "empty", text: "No tagged headlines." }));
    }

    // Transactions (most recent first).
    const txnList = $("#player-modal-txns");
    if (entry.txns && entry.txns.length) {
      renderExpandableList(txnList, entry.txns, 10, ({ tx, item }) => {
        const teamBy = TEAM_BY_ID;
        const head = el("div", {
          className: "player-modal-txn-head",
          children: [
            el("span", { className: `txn-type ${txTypeClass(tx.type)}`, text: (tx.type || "").replace(/_/g, " ") }),
            tx.team_id != null
              ? el("span", { className: "pill team-mono", text: teamBy.get(tx.team_id)?.abbrev || `T${tx.team_id}` })
              : null,
            el("span", { className: "txn-time", text: fmtTime(tx.occurred_at) }),
          ].filter(Boolean),
        });
        const direction = formatTxnDirection(item, teamBy);
        const body = el("div", { className: "player-modal-txn-body", text: direction });
        return el("li", {
          className: "player-modal-txn-item",
          children: [head, body],
        });
      });
    } else {
      txnList.replaceChildren();
      txnList.appendChild(el("li", { className: "empty", text: "No transactions on record." }));
    }

    // Social profile links in footer.
    const socialLinksEl = $("#player-modal-social-links");
    if (socialLinksEl) {
      socialLinksEl.replaceChildren();
      const handles = entry.socials || {};
      if (handles.twitter) {
        socialLinksEl.appendChild(el("a", {
          className: "player-modal-social-link player-modal-social-link--twitter",
          text: "X / Twitter",
          attrs: { href: `https://x.com/${handles.twitter}`, target: "_blank", rel: "noopener noreferrer" },
        }));
      }
      if (handles.instagram) {
        socialLinksEl.appendChild(el("a", {
          className: "player-modal-social-link player-modal-social-link--instagram",
          text: "Instagram",
          attrs: { href: `https://instagram.com/${handles.instagram}`, target: "_blank", rel: "noopener noreferrer" },
        }));
      }
    }

    // ESPN deep link
    $("#player-modal-espn").href = ESPN_PLAYER_PAGE(p.player_id);
  }

  function statBlock(label, value, sub) {
    return el("div", {
      className: "player-modal-stat",
      children: [
        el("span", { className: "player-modal-stat-label", text: label }),
        el("span", { className: "player-modal-stat-value", text: value }),
        sub ? el("span", { className: "player-modal-stat-sub", text: sub }) : null,
      ].filter(Boolean),
    });
  }

  function formatTxnDirection(it, teamBy) {
    const fromTeam = teamAbbr(it.from_team_id, teamBy);
    const toTeam   = teamAbbr(it.to_team_id, teamBy);
    const fromSlot = slotLabel(it.from_slot_id);
    const toSlot   = slotLabel(it.to_slot_id);
    const fromParts = [fromTeam, fromSlot].filter(Boolean);
    const toParts   = [toTeam,   toSlot  ].filter(Boolean);
    const fromText = fromParts.length ? fromParts.join(" · ") : "FA";
    const toText   = toParts.length   ? toParts.join(" · ")   : "FA";
    return fromText === toText ? fromText : `${fromText} → ${toText}`;
  }

  // Player-name buttons attach their own click listeners (see
  // `playerNameBtn`) so we only need delegation for the modal close
  // affordances and a global Escape key.
  document.addEventListener("click", (e) => {
    if (e.target.closest("[data-modal-close]")) closePlayerModal();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      const modal = $("#player-modal");
      if (modal && !modal.hasAttribute("hidden")) closePlayerModal();
    }
  });

  // ---------- Trades ----------
  function renderTrades(state) {
    const grid = $("#trades-grid");
    if (!grid) return;
    const scenarios = state.trade_scenarios || [];
    if (!scenarios.length) {
      grid.innerHTML = '<p class="empty">No trade scenarios available. Run the pipeline to generate them.</p>';
      return;
    }

    // Build player stats lookup from waiver targets (free agents have rich data)
    // and roster entries (rostered players — ppg only).
    const playerStats = {};
    for (const wt of (state.waiver_targets_overall || [])) {
      playerStats[wt.player.player_id] = {
        ppg: wt.projected_per_game || 0,
        nextWeek: wt.projected_points_next_week || 0,
        gamesNext: wt.games_next_week || 0,
        seasonAvg: wt.season_avg_points || 0,
      };
    }
    const teamById = {};
    for (const team of (state.teams || [])) {
      teamById[team.team_id] = team;
      for (const entry of (team.roster || [])) {
        const pid = entry.player.player_id;
        if (!playerStats[pid]) {
          playerStats[pid] = { ppg: entry.projected_per_game || 0, nextWeek: 0, gamesNext: 0, seasonAvg: 0 };
        }
      }
    }

    function fairBadge(ratio) {
      if (ratio >= 0.93 && ratio <= 1.07) return { cls: "trade-fair-badge--even", label: "Even" };
      if (ratio >= 0.85 && ratio <= 1.15) return { cls: "trade-fair-badge--slight", label: ratio > 1 ? "Slight +give" : "Slight +get" };
      return { cls: "trade-fair-badge--lean", label: ratio > 1 ? "Overpay" : "Underpay" };
    }

    // Compact player-stat row for the offer detail panel.
    function playerStatRow(player, tag) {
      const st = playerStats[player.player_id] || {};
      const nxt = st.gamesNext ? `${fmtPoints(st.nextWeek)} (${st.gamesNext}g)` : "—";
      const sea = st.seasonAvg  ? `${fmtPoints(st.seasonAvg)}/g` : "—";
      return `<div class="trade-offer-player-row">
        <span class="trade-result-tag ${tag === "OUT" ? "trade-result-tag--out" : "trade-result-tag--in"}">${tag}</span>
        <button class="player-name-btn" data-player-id="${player.player_id}">${player.name}</button>
        <span class="trade-offer-stat"><span class="trade-offer-stat-label">/g</span> ${fmtPoints(player.projected_per_game)}</span>
        <span class="trade-offer-stat"><span class="trade-offer-stat-label">nxt wk</span> ${nxt}</span>
        <span class="trade-offer-stat"><span class="trade-offer-stat-label">seas</span> ${sea}</span>
      </div>`;
    }

    const html = scenarios.map((sc, si) => {
      const bp = sc.best_player;
      const needLabel = sc.top_need_bucket === "G" ? "Guards" : "Frontcourt";

      const offersHtml = !sc.offers || !sc.offers.length
        ? '<p class="trade-no-offers">No team has a fair package within ±25% value.</p>'
        : sc.offers.map((offer, oi) => {
            const pkg = offer.pkg_received;
            const badge = fairBadge(offer.value_ratio);

            const pkgHtml = pkg.players.map(p => {
              // Packages are priced at each player's healthy per-game rate,
              // so an unavailable player looks like a bargain. Surface the
              // status next to the price until Trades 2.0 discounts it.
              const st = PLAYER_INDEX.get(Number(p.player_id))?.profile?.injury_status;
              const inj = st && st !== "ACTIVE"
                ? `<span class="own-neg injury-pill">${st.replace(/_/g, " ")}</span>` : "";
              return `<span class="trade-pkg-player">` +
                `<button class="player-name-btn" data-player-id="${p.player_id}">${p.name}</button>` +
                `<span class="trade-pkg-ppg">${fmtPoints(p.projected_per_game)}/g</span>${inj}` +
                `</span>`;
            }).join('<span class="trade-pkg-plus">+</span>');

            const rankClass = oi < 3 ? ` trade-offer--rank${oi + 1}` : "";
            return `<li class="trade-offer${rankClass}" data-si="${si}" data-oi="${oi}">
              <div class="trade-offer-summary">
                <div class="trade-offer-rank">${oi + 1}</div>
                <div class="trade-offer-body">
                  <div class="trade-offer-from">From <strong>${offer.from_team_abbrev}</strong></div>
                  <div class="trade-offer-pkg">${pkgHtml}</div>
                  <div class="trade-offer-meta">
                    <span class="trade-pkg-total">${fmtPoints(pkg.total_ppg)}/g total</span>
                    <span class="trade-fair-badge ${badge.cls}">${badge.label}</span>
                    <span class="trade-fit-score">Fit ${Math.round(offer.need_fit_score * 100)}%</span>
                  </div>
                </div>
                <span class="trade-offer-chevron" aria-hidden="true">▶</span>
              </div>
              <div class="trade-offer-detail" hidden></div>
            </li>`;
          }).join("");

      return `<div class="trade-scenario" data-si="${si}">
        <div class="trade-scenario-head">
          <div class="trade-scenario-head-content">
            <span class="trade-scenario-team">${sc.team_abbrev}</span>
            <span class="trade-scenario-title">Give up
              <button class="player-name-btn" data-player-id="${bp.player_id}">${bp.name}</button>
              <span class="trade-best-ppg">${fmtPoints(bp.projected_per_game)}/g</span>
            </span>
            <span class="trade-scenario-sub">Top need: ${needLabel}</span>
          </div>
          <button class="trade-scenario-toggle" type="button"
            aria-expanded="false" aria-controls="trade-sc-body-${si}"
            aria-label="Expand ${sc.team_abbrev} trade offers">▶</button>
        </div>
        <div class="trade-scenario-body" id="trade-sc-body-${si}">
          <ol class="trade-offer-list">${offersHtml}</ol>
        </div>
      </div>`;
    }).join("");

    grid.innerHTML = html;

    // Scenario collapse/expand via the toggle button.
    grid.querySelectorAll(".trade-scenario-toggle").forEach(btn => {
      btn.addEventListener("click", () => {
        const sc = btn.closest(".trade-scenario");
        const open = sc.classList.toggle("trade-scenario--open");
        btn.setAttribute("aria-expanded", String(open));
        btn.textContent = open ? "▼" : "▶";
      });
    });

    // Offer expand/collapse with lazy lineup simulation.
    grid.querySelectorAll(".trade-offer").forEach(li => {
      li.addEventListener("click", e => {
        if (e.target.closest(".player-name-btn")) return;

        const detail = li.querySelector(".trade-offer-detail");
        const isOpen = li.classList.toggle("trade-offer--open");
        const chevron = li.querySelector(".trade-offer-chevron");
        if (chevron) chevron.textContent = isOpen ? "▼" : "▶";

        if (isOpen && detail && !detail.dataset.rendered) {
          detail.dataset.rendered = "1";
          const si     = Number(li.dataset.si);
          const oi     = Number(li.dataset.oi);
          const sc     = scenarios[si];
          const offer  = sc.offers[oi];
          const teamRecv = teamById[sc.team_id];
          const teamOffr = teamById[offer.from_team_id];

          const bpRow   = playerStatRow(sc.best_player, "OUT");
          const pkgRows = offer.pkg_received.players.map(p => playerStatRow(p, "IN")).join("");

          let impactHtml = "";
          if (teamRecv && teamOffr) {
            const giveIds    = new Set([sc.best_player.player_id]);
            const receiveIds = new Set(offer.pkg_received.players.map(p => p.player_id));
            const result     = evaluateTrade(teamRecv, teamOffr, giveIds, receiveIds);
            impactHtml = `<div class="trade-offer-detail-impact">
              <div class="trade-result-grid">${tradeSideHtml(result.teamA)}${tradeSideHtml(result.teamB)}</div>
            </div>`;
          }

          detail.innerHTML = `<div class="trade-offer-detail-players">${bpRow}${pkgRows}</div>${impactHtml}`;
          detail.querySelectorAll(".player-name-btn[data-player-id]").forEach(btn => {
            btn.addEventListener("click", ev => { ev.stopPropagation(); openPlayerModal(Number(btn.dataset.playerId)); });
          });
        }

        if (detail) detail.hidden = !isOpen;
      });
    });

    // Player-name buttons in offer summaries open the modal without toggling.
    grid.querySelectorAll(".trade-offer-summary .player-name-btn[data-player-id]").forEach(btn => {
      btn.addEventListener("click", e => { e.stopPropagation(); openPlayerModal(Number(btn.dataset.playerId)); });
    });
    // Best-player button in the scenario head.
    grid.querySelectorAll(".trade-scenario-head .player-name-btn[data-player-id]").forEach(btn => {
      btn.addEventListener("click", e => { e.stopPropagation(); openPlayerModal(Number(btn.dataset.playerId)); });
    });
  }

  // ---------- Trade Calculator ----------

  // Optimal lineup simulation after a trade.
  // WNBA fantasy slots: 2 G, 3 F, 1 F/C, 3 UTIL — 9 active total.
  function simulateLineup(rosterEntries) {
    const { activeSlots, bench } = optimalLineupSlots(rosterEntries, (e) => e.projected_per_game || 0);
    const thisWeek = activeSlots.reduce((s, { entry: e }) => s + (e.projected_points_this_week || 0), 0);
    const nextWeek = activeSlots.reduce((s, { entry: e }) => s + (e.projected_points_next_week || 0), 0);
    const ppg      = activeSlots.reduce((s, { entry: e }) => s + (e.projected_per_game || 0), 0);

    return { activeSlots, bench, thisWeek, nextWeek, ppg };
  }

  function evaluateTrade(teamA, teamB, giveIds, receiveIds) {
    function postTradeRoster(myTeam, give, receive, otherTeam) {
      const kept     = myTeam.roster.filter(e => !give.has(e.player.player_id));
      const incoming = otherTeam.roster.filter(e => receive.has(e.player.player_id));
      return [...kept, ...incoming];
    }

    const beforeA = simulateLineup(teamA.roster);
    const afterA  = simulateLineup(postTradeRoster(teamA, giveIds, receiveIds, teamB));
    const beforeB = simulateLineup(teamB.roster);
    const afterB  = simulateLineup(postTradeRoster(teamB, receiveIds, giveIds, teamA));

    return {
      teamA: {
        team: teamA, before: beforeA, after: afterA,
        dWeek: afterA.thisWeek - beforeA.thisWeek,
        dNext: afterA.nextWeek - beforeA.nextWeek,
        dPpg:  afterA.ppg - beforeA.ppg,
        given:    teamA.roster.filter(e => giveIds.has(e.player.player_id)),
        received: teamB.roster.filter(e => receiveIds.has(e.player.player_id)),
      },
      teamB: {
        team: teamB, before: beforeB, after: afterB,
        dWeek: afterB.thisWeek - beforeB.thisWeek,
        dNext: afterB.nextWeek - beforeB.nextWeek,
        dPpg:  afterB.ppg - beforeB.ppg,
        given:    teamB.roster.filter(e => receiveIds.has(e.player.player_id)),
        received: teamA.roster.filter(e => giveIds.has(e.player.player_id)),
      },
    };
  }

  function renderTradeResult(result) {
    const container = $("#trade-calc-result");
    if (!container) return;
    container.removeAttribute("hidden");

    container.innerHTML = `<div class="trade-result-grid">${tradeSideHtml(result.teamA)}${tradeSideHtml(result.teamB)}</div>`;

    container.querySelectorAll(".player-name-btn[data-player-id]").forEach(btn => {
      btn.addEventListener("click", () => openPlayerModal(Number(btn.dataset.playerId)));
    });
  }

  function initTradeCalc(state) {
    const mount = $("#trade-calc-mount");
    if (!mount) return;
    const teams = (state.teams || []).slice().sort((a, b) => a.abbrev.localeCompare(b.abbrev));
    if (!teams.length) return;

    const teamById = {};
    teams.forEach(t => { teamById[t.team_id] = t; });

    const teamOptions = teams.map(t =>
      `<option value="${t.team_id}">${t.abbrev} — ${t.name}</option>`
    ).join("");

    mount.innerHTML = `
      <div class="trade-calc">
        <h3 class="trade-calc-h">Trade Calculator</h3>
        <p class="trade-calc-sub">Select players from two rosters. The calculator re-optimizes each team's lineup after the trade and projects the impact on this week, next week, and the per-game rate.</p>
        <div class="trade-calc-form">
          <div class="trade-calc-side">
            <label class="trade-calc-team-label" for="calc-team-a">Your team</label>
            <select class="trade-calc-team-sel" id="calc-team-a">
              <option value="">— pick a team —</option>${teamOptions}
            </select>
            <div class="trade-calc-players" id="calc-players-a"><p class="trade-calc-hint">Pick a team to see their roster.</p></div>
          </div>
          <div class="trade-calc-divider-col" aria-hidden="true">⇄</div>
          <div class="trade-calc-side">
            <label class="trade-calc-team-label" for="calc-team-b">Trade with</label>
            <select class="trade-calc-team-sel" id="calc-team-b">
              <option value="">— pick a team —</option>${teamOptions}
            </select>
            <div class="trade-calc-players" id="calc-players-b"><p class="trade-calc-hint">Pick a team to see their roster.</p></div>
          </div>
        </div>
        <div class="trade-calc-actions">
          <button class="trade-calc-btn" id="calc-evaluate" disabled>Evaluate trade</button>
        </div>
        <div class="trade-calc-result" id="trade-calc-result" hidden></div>
      </div>
    `;

    function rosterListHtml(team, side) {
      const sorted = [...team.roster].sort((a, b) => (b.projected_per_game || 0) - (a.projected_per_game || 0));
      return sorted.map(e => {
        const ppg = e.projected_per_game != null ? `${fmtPoints(e.projected_per_game)}/g` : "—";
        const beTag = e.is_active ? "" : `<span class="trade-calc-bench">BE</span>`;
        const bkt = `<span class="waiver-bucket bucket-${e.player.bucket}">${e.player.bucket}</span>`;
        return `<div class="trade-calc-player">
          <label class="trade-calc-check-area">
            <input type="checkbox" class="trade-calc-check" data-side="${side}" value="${e.player.player_id}" aria-label="Trade ${e.player.name}"/>
          </label>
          <button class="player-name-btn trade-calc-player-name" type="button" data-player-id="${e.player.player_id}">${e.player.name}</button>
          <span class="trade-calc-player-meta">${bkt}${ppg}${beTag}</span>
        </div>`;
      }).join("");
    }

    function updateEvalBtn() {
      const teamAId = parseInt($("#calc-team-a").value || "0");
      const teamBId = parseInt($("#calc-team-b").value || "0");
      const hasA = $$('#calc-players-a .trade-calc-check:checked').length > 0;
      const hasB = $$('#calc-players-b .trade-calc-check:checked').length > 0;
      $("#calc-evaluate").disabled = !teamAId || !teamBId || teamAId === teamBId || (!hasA && !hasB);
    }

    function onTeamChange(side) {
      const selEl = $(`#calc-team-${side}`);
      const listEl = $(`#calc-players-${side}`);
      const teamId = parseInt(selEl.value || "0");
      if (!teamId || !teamById[teamId]) {
        listEl.innerHTML = '<p class="trade-calc-hint">Pick a team to see their roster.</p>';
      } else {
        listEl.innerHTML = rosterListHtml(teamById[teamId], side);
        listEl.querySelectorAll('input[type="checkbox"]').forEach(cb =>
          cb.addEventListener("change", updateEvalBtn)
        );
        listEl.querySelectorAll('.player-name-btn[data-player-id]').forEach(btn =>
          btn.addEventListener("click", (e) => { e.stopPropagation(); openPlayerModal(Number(btn.dataset.playerId)); })
        );
      }
      // Hide result when teams change
      const res = $("#trade-calc-result");
      if (res) res.setAttribute("hidden", "");
      updateEvalBtn();
    }

    $("#calc-team-a").addEventListener("change", () => onTeamChange("a"));
    $("#calc-team-b").addEventListener("change", () => onTeamChange("b"));

    $("#calc-evaluate").addEventListener("click", () => {
      const teamAId = parseInt($("#calc-team-a").value || "0");
      const teamBId = parseInt($("#calc-team-b").value || "0");
      const giveIds    = new Set([...$$('#calc-players-a .trade-calc-check:checked')].map(cb => parseInt(cb.value)));
      const receiveIds = new Set([...$$('#calc-players-b .trade-calc-check:checked')].map(cb => parseInt(cb.value)));
      const result = evaluateTrade(teamById[teamAId], teamById[teamBId], giveIds, receiveIds);
      renderTradeResult(result);
      // Scroll result into view
      const res = $("#trade-calc-result");
      if (res) res.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
  }

  // ---------- Team picker + navigation wiring ----------
  function renderTeamPicker(teams, onPick) {
    const grid = $("#team-picker-grid");
    if (!grid) return;
    grid.replaceChildren();
    (teams || []).slice().sort((a, b) => a.abbrev.localeCompare(b.abbrev)).forEach((t) => {
      const btn = el("button", {
        className: `team-picker-btn${t.team_id === MY_TEAM_ID ? " is-current" : ""}`,
        // Explicit label: the visible name can be ellipsised to fit the tile,
        // and name-from-contents on a two-span button reads as one run-on
        // string. Screen readers get the clean version.
        attrs: { type: "button", "aria-label": `${t.name} (${t.abbrev})` },
        children: [
          el("span", { className: "team-picker-abbr", text: t.abbrev }),
          el("span", { className: "team-picker-name", text: t.name }),
        ],
      });
      btn.addEventListener("click", () => { setMyTeam(t.team_id); closeSheet("team-picker"); onPick(); });
      grid.appendChild(btn);
    });
  }

  function initNav(state) {
    const teams = state.teams || [];
    // Re-render every my-team-sensitive surface. Cheap (one state object,
    // no refetch) and keeps "switch team" from needing a page reload.
    const rerender = () => {
      renderToday(state);
      // Waivers re-scope to the newly picked team — net gain, drop pairing,
      // and bid band are all roster-relative.
      renderWaivers(state);
      renderTeams(teams, state.waiver_targets_by_team, state.transactions_recent);
      renderTeamPicker(teams, rerender);
    };

    renderTeamPicker(teams, rerender);

    // Chrome (chip, bottom nav, sheet close, Escape) is wired by the
    // parse-time delegated listener above. Only the data-dependent bits
    // bind here.
    const clear = $("#team-picker-clear");
    if (clear) {
      clear.addEventListener("click", () => {
        setMyTeam(null);
        closeSheet("team-picker");
        rerender();
        selectTab("section-teams");
      });
    }
  }

  // Keep the bottom bar's highlight in step with whatever selected the tab
  // (top tabs, hash, or a Today block link).
  function syncBottomNav(panelId) {
    const direct = $$(".bottom-nav-btn").some((b) => b.getAttribute("data-panel") === panelId);
    $$(".bottom-nav-btn").forEach((b) => {
      // Panels reached through the More sheet (News, Transactions) have no
      // bar item of their own — light up More so the bar never reads as
      // "you are nowhere".
      const on = b.id === "bottom-nav-more"
        ? !direct
        : b.getAttribute("data-panel") === panelId;
      if (on) b.setAttribute("aria-current", "page");
      else b.removeAttribute("aria-current");
    });
  }

  // ---------- Render error states ----------
  function renderEmptyAll(reason) {
    renderMeta({});
    const today = $("#today-mount");
    if (today) today.replaceChildren(el("p", { className: "empty", text: reason }));
    $("#waiver-list").replaceChildren(el("li", { className: "empty", text: reason }));
    $("#team-grid").replaceChildren(el("p", { className: "empty", text: reason }));
    const newsList = $("#news-list");
    if (newsList) newsList.replaceChildren(el("li", { className: "empty", text: reason }));
    $("#txn-list").replaceChildren(el("li", { className: "empty", text: reason }));
  }

  // ---------- Bootstrap ----------
  async function main() {
    initTheme();
    try {
      const resp = await fetch(STATE_URL, { cache: "no-store" });
      if (!resp.ok) {
        renderEmptyAll(
          `Couldn't load state.json (HTTP ${resp.status}). ` +
          `Run \`python -m pipeline.refresh\` to generate it.`,
        );
        return;
      }
      const state = await resp.json();
      // Build the per-player index BEFORE rendering — the player-name
      // buttons rendered inside team cards, waivers, and txns expect it
      // to exist when a click fires. It also populates TEAM_BY_ID, which
      // myTeam() reads, so it has to precede initMyTeam().
      PLAYER_INDEX = buildPlayerIndex(state);
      initMyTeam(state.teams);
      renderMeta(state.meta || {});
      renderToday(state);
      renderWaivers(state);
      renderTeams(state.teams, state.waiver_targets_by_team, state.transactions_recent);
      renderTrades(state);
      initTradeCalc(state);
      renderNews(state.news_recent, state.teams);
      renderTxns(state.transactions_recent, state.teams);
      initNav(state);
      // The hash wins over the default tab, but only after Today has
      // rendered — otherwise a #waivers deep link would leave Today empty
      // if the user tabs back to it.
      initTabsFromHash();
    } catch (err) {
      console.error("FantasyGM: failed to load state", err);
      renderEmptyAll(
        "Couldn't load state.json. If you're opening index.html via file://, " +
        "use `python3 -m http.server -d docs 8000` and visit http://localhost:8000.",
      );
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", main);
  } else {
    main();
  }
})();
