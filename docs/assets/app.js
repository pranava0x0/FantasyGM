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
  });

  // ---------- Tabs ----------
  function selectTab(panelId) {
    $$(".tab").forEach((b) => {
      b.setAttribute("aria-selected", b.getAttribute("aria-controls") === panelId ? "true" : "false");
    });
    $$(".tab-panel").forEach((p) => {
      if (p.id === panelId) p.removeAttribute("hidden");
      else p.setAttribute("hidden", "");
    });
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
    if (fitBucket && p.bucket === fitBucket) {
      nameLine.appendChild(el("span", { className: "fit-pill", text: `Fits ${fitBucket}` }));
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

    const children = [
      el("span", { className: "waiver-rank", text: String(idx + 1) }),
      el("div", {
        className: "waiver-body",
        children: [nameLine, sub],
      }),
      el("div", {
        className: "waiver-schedule",
        children: [
          weekBlock(t.games_this_week, thisWkPts, "this wk"),
          weekBlock(t.games_next_week, nextWkPts, "next wk"),
        ],
      }),
    ];

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

  function renderWaivers(overall) {
    const list = $("#waiver-list");
    list.replaceChildren();
    if (!overall || overall.length === 0) {
      list.appendChild(el("li", { className: "empty", text: "No waiver targets — pipeline hasn't run yet." }));
      return;
    }
    overall.forEach((t, i) => list.appendChild(waiverCard(t, i)));
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

  function rosterTable(team) {
    const wrap = el("div", { className: "roster-table" });
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
      rows.forEach((r) => wrap.appendChild(rosterRow(slot, r)));
    });
    if (!seenOrder.length) wrap.appendChild(el("p", { className: "muted-cell", text: "Roster empty." }));
    return wrap;
  }

  function rosterRow(slotLabel, r) {
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
    const card = el("div", {
      className: "team-card",
      attrs: {
        role: "button",
        tabindex: "0",
        "aria-expanded": "false",
        "data-team-id": String(team.team_id),
      },
      children: [head, meta, rows, detail],
    });
    const toggle = () => {
      const expanded = card.getAttribute("aria-expanded") === "true";
      card.setAttribute("aria-expanded", expanded ? "false" : "true");
      if (expanded) detail.setAttribute("hidden", "");
      else detail.removeAttribute("hidden");
    };
    card.addEventListener("click", toggle);
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
    teams.forEach((t) => grid.appendChild(teamCard(t, targetIndex.get(t.team_id), teamByIdMap, txnsByIdMap)));
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
  // Element to restore focus to when the modal closes.
  let lastFocusedTrigger = null;

  function buildPlayerIndex(state) {
    const idx = new Map();
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

    // News tagged with this player's athleteId. Cap at 8.
    Object.entries(state.news_by_player || {}).forEach(([pid, articles]) => {
      const num = Number(pid);
      if (!Number.isFinite(num)) return;
      const entry = ensure(num);
      entry.news = (articles || []).slice(0, 8);
    });

    // Reddit r/wnba posts mentioning this player. Cap at 5.
    Object.entries(state.reddit_posts_by_player || {}).forEach(([pid, posts]) => {
      const num = Number(pid);
      if (!Number.isFinite(num)) return;
      const entry = ensure(num);
      entry.reddit = (posts || []).slice(0, 5);
    });

    // Twitter/X posts mentioning this player. Cap at 5.
    Object.entries(state.twitter_posts_by_player || {}).forEach(([pid, tweets]) => {
      const num = Number(pid);
      if (!Number.isFinite(num)) return;
      const entry = ensure(num);
      entry.twitter = (tweets || []).slice(0, 5);
    });

    // Bluesky posts mentioning this player. Cap at 5.
    Object.entries(state.bluesky_posts_by_player || {}).forEach(([pid, posts]) => {
      const num = Number(pid);
      if (!Number.isFinite(num)) return;
      const entry = ensure(num);
      entry.bluesky = (posts || []).slice(0, 5);
    });

    // Instagram posts from/about this player. Cap at 5.
    Object.entries(state.instagram_posts_by_player || {}).forEach(([pid, posts]) => {
      const num = Number(pid);
      if (!Number.isFinite(num)) return;
      const entry = ensure(num);
      entry.instagram = (posts || []).slice(0, 5);
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
            className: `recent-game-pill${isHigh ? " recent-game-pill--high" : ""}`,
            text: String(Math.round(g.fantasy_points)),
            attrs: { title: `Period ${g.scoring_period_id}: ${g.fantasy_points} fpts` },
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
        socialList.replaceChildren();
        allPosts.slice(0, 12).forEach((post) => {
          const li = el("li", { className: "social-item" });
          li.appendChild(el("span", { className: `social-badge social-badge--${post.source}`, text: post.label }));
          li.appendChild(el("a", {
            className: "social-title",
            text: post.title,
            attrs: { href: post.url || "#", target: "_blank", rel: "noopener noreferrer" },
          }));
          if (post.ts) li.appendChild(el("span", { className: "social-time", text: fmtTime(post.ts) }));
          socialList.appendChild(li);
        });
      } else {
        socialWrap.setAttribute("hidden", "");
      }
    }

    // News — compact: headline + time + direct/team tag only (no description).
    const newsList = $("#player-modal-news");
    newsList.replaceChildren();
    if (entry.news && entry.news.length) {
      entry.news.forEach((n) => {
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
        newsList.appendChild(li);
      });
    } else {
      newsList.appendChild(el("li", { className: "empty", text: "No tagged headlines." }));
    }

    // Transactions (most recent first).
    const txnList = $("#player-modal-txns");
    txnList.replaceChildren();
    if (entry.txns && entry.txns.length) {
      entry.txns.slice(0, 10).forEach(({ tx, item }) => {
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
        txnList.appendChild(el("li", {
          className: "player-modal-txn-item",
          children: [head, body],
        }));
      });
    } else {
      txnList.appendChild(el("li", { className: "empty", text: "No transactions in the recent window." }));
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

  // ---------- Render error states ----------
  function renderEmptyAll(reason) {
    renderMeta({});
    $("#waiver-list").replaceChildren(el("li", { className: "empty", text: reason }));
    $("#team-grid").replaceChildren(el("p", { className: "empty", text: reason }));
    const newsList = $("#news-list");
    if (newsList) newsList.replaceChildren(el("li", { className: "empty", text: reason }));
    $("#txn-list").replaceChildren(el("li", { className: "empty", text: reason }));
  }

  // ---------- Bootstrap ----------
  async function main() {
    initTheme();
    initTabsFromHash();
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
      // to exist when a click fires.
      PLAYER_INDEX = buildPlayerIndex(state);
      renderMeta(state.meta || {});
      renderWaivers(state.waiver_targets_overall);
      renderTeams(state.teams, state.waiver_targets_by_team, state.transactions_recent);
      renderNews(state.news_recent, state.teams);
      renderTxns(state.transactions_recent, state.teams);
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
