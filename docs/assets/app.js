/* ============================================================
   FantasyGM — frontend app.
   Reads ./data/state.json (built by pipeline.refresh) and renders
   waiver targets, team weakness cards, and the transaction log.

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
    const v = Number(n);
    const sign = v > 0 ? "+" : v < 0 ? "" : "";
    return `${sign}${v.toFixed(1)}%`;
  }
  function fmtDate(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "—";
    return d.toISOString().slice(0, 16).replace("T", " ") + "Z";
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

  // ---------- Render: meta strip ----------
  function renderMeta(meta) {
    $("#league-name").textContent = meta.league_name || "FantasyGM";
    document.title = `${meta.league_name || "FantasyGM"} · FantasyGM`;
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

  function waiverCard(t, idx, fitBucket) {
    const p = t.player;
    const sub = el("span", { className: "waiver-sub" });
    sub.appendChild(bucketPill(p.bucket));
    if (p.team) sub.appendChild(el("span", { className: "pill team-mono", text: p.team }));
    const gp = gamesPill(t.games_this_week);
    if (gp) sub.appendChild(gp);
    if (t.projected_per_game != null) {
      sub.appendChild(el("span", { text: `${fmtPoints(t.projected_per_game)}/g` }));
    }
    if (t.percent_owned != null) {
      const pc = fmtPctChange(t.percent_change);
      const ownText = `${fmtPct(t.percent_owned)} owned`;
      sub.appendChild(el("span", { text: ownText }));
      if (pc != null) {
        const cls = (t.percent_change || 0) > 0 ? "own-pos" : (t.percent_change || 0) < 0 ? "own-neg" : "";
        sub.appendChild(el("span", { className: `own-change ${cls}`.trim(), text: pc }));
      }
    }
    if (p.injury_status && p.injury_status !== "ACTIVE") {
      sub.appendChild(el("span", { className: "own-neg", text: p.injury_status }));
    }

    const nameLine = el("span", { className: "waiver-name", text: p.name });
    if (fitBucket && p.bucket === fitBucket) {
      nameLine.appendChild(el("span", { className: "fit-pill", text: `Fits ${fitBucket}` }));
    }
    if (t.promoted_for_need) {
      nameLine.appendChild(el("span", { className: "fit-pill need", text: "Need" }));
    }

    const weekProj = t.projected_points_this_week != null
      ? t.projected_points_this_week
      : t.projected_points_next_period;

    return el("li", {
      className: "waiver-card",
      children: [
        el("span", { className: "waiver-rank", text: String(idx + 1) }),
        el("div", {
          className: "waiver-body",
          children: [nameLine, sub],
        }),
        el("div", {
          className: "waiver-points",
          children: [
            document.createTextNode(fmtPoints(weekProj)),
            el("span", { className: "unit", text: "week" }),
          ],
        }),
      ],
    });
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

  // ---------- Render: team weakness ----------
  function bucketRow(label, proj, gap, isWeakest) {
    const cls = gap > 0 ? "pos" : gap < 0 ? "neg" : "zero";
    const sign = gap > 0 ? "+" : "";
    const max = Math.max(40, Math.abs(proj) * 1.2, 1);
    const pct = Math.min(100, (Math.max(0, proj) / max) * 100);
    const fill = el("span", { className: `bucket-fill ${isWeakest ? "weakest" : ""}` });
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
  function frontcourtRow(forward_proj, center_proj, gap, isWeakest) {
    const total = forward_proj + center_proj;
    const cls = gap > 0 ? "pos" : gap < 0 ? "neg" : "zero";
    const sign = gap > 0 ? "+" : "";
    const pill = el("span", { className: "pill outline bucket-F", text: "F/C" });
    const max = Math.max(40, Math.abs(total) * 1.2, 1);
    const pct = Math.min(100, (Math.max(0, total) / max) * 100);
    const fSplit = total > 0 ? Math.round((forward_proj / total) * 100) : 0;
    const fill = el("span", { className: `bucket-fill ${isWeakest ? "weakest" : ""}` });
    fill.style.width = `${pct}%`;
    // F portion in amber, C in magenta — narrow stripe inside the bar.
    fill.style.background = isWeakest
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
    const nameSpan = el("span", { className: "roster-name", text: p.name });
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
    const w = team.weakness;
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
        bucketRow("G", w.guard_proj, w.guard_gap_vs_league, w.weakest_bucket === "G"),
        frontcourtRow(w.forward_proj, w.center_proj, w.frontcourt_gap_vs_league, w.weakest_bucket === "FC"),
      ],
    });

    const detail = el("div", { className: "team-detail", attrs: { hidden: "" } });

    // Auto-generated summary bullets (marked with accent left-border per DESIGN.md).
    if (Array.isArray(team.summary) && team.summary.length > 0) {
      const sumBlock = el("div", { className: "team-summary", attrs: { "aria-label": "Auto-generated team summary" } });
      sumBlock.appendChild(el("h4", { text: "Summary · auto-generated" }));
      const ul = el("ul", { className: "team-summary-list" });
      team.summary.forEach((b) => ul.appendChild(el("li", { text: b })));
      sumBlock.appendChild(ul);
      detail.appendChild(sumBlock);
    }

    // Top picks for this team
    const weakLabel = w.weakest_bucket === "FC" ? "F/C (frontcourt)" : w.weakest_bucket;
    detail.appendChild(el("h4", { className: "team-detail-head", text: `Top picks · weakest at ${weakLabel}` }));
    const list = el("div", { className: "team-targets" });
    (perTeamTargets || []).slice(0, 6).forEach((tgt, i) => {
      const p = tgt.player;
      const nameSpan = el("span", { className: "target-name", text: p.name });
      if (tgt.promoted_for_need) {
        nameSpan.appendChild(el("span", { className: "fit-pill need", text: "Need" }));
      }
      const sub = el("span", { className: "target-sub" });
      if (p.team) sub.appendChild(el("span", { className: "pill team-mono", text: p.team }));
      const gp = gamesPill(tgt.games_this_week);
      if (gp) sub.appendChild(gp);
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
          el("span", {
            className: "target-pts",
            text: fmtPoints(adj),
          }),
        ],
      }));
    });
    if (!(perTeamTargets && perTeamTargets.length)) {
      list.appendChild(el("p", { className: "muted-cell", text: "No targets ranked." }));
    }
    detail.appendChild(list);

    // Full roster grouped by lineup-slot type so the user can see who's
    // active vs on the bench, with weekly projections.
    detail.appendChild(el("h4", { className: "team-detail-head", text: "Full roster" }));
    detail.appendChild(rosterTable(team));

    // Recent transactions for this team (joins on recent_transaction_ids).
    detail.appendChild(el("h4", { className: "team-detail-head", text: "Recent transactions" }));
    detail.appendChild(teamTransactionsBlock(team, allTeamsForLookup, txnsByIdLookup));

    const btn = el("button", {
      className: "team-card",
      attrs: { type: "button", "aria-expanded": "false", "data-team-id": String(team.team_id) },
      children: [head, meta, rows, detail],
    });
    btn.addEventListener("click", () => {
      const expanded = btn.getAttribute("aria-expanded") === "true";
      btn.setAttribute("aria-expanded", expanded ? "false" : "true");
      if (expanded) {
        detail.setAttribute("hidden", "");
      } else {
        detail.removeAttribute("hidden");
      }
    });
    return btn;
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
      if (name) tags.appendChild(el("span", { className: "pill team-mono news-player", text: name }));
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

    const pieces = [
      el("strong", { text: it.player_name || `#${it.player_id}` }),
    ];
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
    txns.slice(0, 30).forEach((tx) => list.appendChild(renderTxnCard(tx, teamById)));
  }

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
