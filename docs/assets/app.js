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
  });

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
  }

  // ---------- Render: waiver targets ----------
  function bucketPill(bucket) {
    return el("span", {
      className: `pill outline bucket-${bucket}`,
      text: bucket,
    });
  }

  function waiverCard(t, idx, fitBucket) {
    const p = t.player;
    const sub = el("span", { className: "waiver-sub" });
    sub.appendChild(bucketPill(p.bucket));
    if (p.team) sub.appendChild(el("span", { className: "pill team-mono", text: p.team }));
    if (t.season_avg_points != null) {
      sub.appendChild(el("span", { text: `${fmtPoints(t.season_avg_points)} avg` }));
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
            document.createTextNode(fmtPoints(t.projected_points_next_period)),
            el("span", { className: "unit", text: "proj" }),
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

  function teamCard(team, perTeamTargets) {
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
        bucketRow("F", w.forward_proj, w.forward_gap_vs_league, w.weakest_bucket === "F"),
        bucketRow("C", w.center_proj, w.center_gap_vs_league, w.weakest_bucket === "C"),
      ],
    });

    const detail = el("div", { className: "team-detail", attrs: { hidden: "" } });
    detail.appendChild(el("h4", { text: `Top picks for ${team.abbrev} · weakest at ${w.weakest_bucket}` }));
    const list = el("div", { className: "team-targets" });
    (perTeamTargets || []).slice(0, 6).forEach((t, i) => {
      const p = t.player;
      list.appendChild(el("div", {
        className: "team-target-row",
        children: [
          el("span", { className: "target-rank", text: String(i + 1) }),
          el("span", { className: "target-name", text: p.name }),
          bucketPill(p.bucket),
          el("span", {
            className: "target-pts",
            text: fmtPoints(t.projected_points_next_period),
          }),
        ],
      }));
    });
    if (!(perTeamTargets && perTeamTargets.length)) {
      list.appendChild(el("p", { className: "muted-cell", text: "No targets ranked." }));
    }
    detail.appendChild(list);

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

  function renderTeams(teams, byTeam) {
    const grid = $("#team-grid");
    grid.replaceChildren();
    if (!teams || teams.length === 0) {
      grid.appendChild(el("p", { className: "empty", text: "No team data yet." }));
      return;
    }
    const targetIndex = new Map((byTeam || []).map((row) => [row.team_id, row.targets]));
    teams.forEach((t) => grid.appendChild(teamCard(t, targetIndex.get(t.team_id))));
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
    txns.slice(0, 30).forEach((tx) => {
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
      list.appendChild(el("li", {
        className: "txn-card",
        children: [head, items],
      }));
    });
  }

  // ---------- Render error states ----------
  function renderEmptyAll(reason) {
    renderMeta({});
    $("#waiver-list").replaceChildren(el("li", { className: "empty", text: reason }));
    $("#team-grid").replaceChildren(el("p", { className: "empty", text: reason }));
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
      renderMeta(state.meta || {});
      renderWaivers(state.waiver_targets_overall);
      renderTeams(state.teams, state.waiver_targets_by_team);
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
