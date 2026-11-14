(function () {
  "use strict";

  const toastEl = document.getElementById("toast");
  let toastTimer = null;

  function showToast(msg) {
    if (!toastEl) return;
    toastEl.textContent = msg;
    toastEl.classList.add("visible");
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(function () {
      toastEl.classList.remove("visible");
    }, 2200);
  }

  function copyText(text) {
    if (!text) return;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(
        function () {
          showToast("Copied to clipboard");
        },
        function (err) {
          console.warn("Clipboard write unavailable:", err);
          showToast("Copy command ready above");
        }
      );
    } else {
      showToast("Copy command ready above");
    }
  }

  // Split CTA popover & command selector
  const mainCopyBtn = document.getElementById("copy-main-cmd");
  const menuBtn = document.getElementById("cta-menu-btn");
  const popover = document.getElementById("cta-popover");
  const popCmdText = document.getElementById("pop-cmd-text");
  const mainCaption = document.getElementById("main-cmd-caption");
  const popCopyBtn = document.getElementById("pop-copy-btn");

  if (mainCopyBtn) {
    mainCopyBtn.addEventListener("click", function () {
      copyText(mainCopyBtn.getAttribute("data-copy") || "");
    });
  }

  if (menuBtn && popover) {
    menuBtn.addEventListener("click", function (ev) {
      ev.stopPropagation();
      const open = popover.classList.toggle("open");
      menuBtn.setAttribute("aria-expanded", open ? "true" : "false");
    });

    document.addEventListener("click", function (ev) {
      if (popover.classList.contains("open") && !popover.contains(ev.target) && ev.target !== menuBtn) {
        popover.classList.remove("open");
        menuBtn.setAttribute("aria-expanded", "false");
      }
    });
  }

  document.querySelectorAll("[data-pop-cmd]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      document.querySelectorAll("[data-pop-cmd]").forEach(function (b) {
        b.classList.remove("active");
      });
      btn.classList.add("active");
      const cmd = btn.getAttribute("data-pop-cmd") || "";
      if (popCmdText) popCmdText.textContent = cmd;
      if (mainCaption) mainCaption.textContent = "$ " + cmd;
      if (mainCopyBtn) mainCopyBtn.setAttribute("data-copy", cmd);
    });
  });

  if (popCopyBtn && popCmdText) {
    popCopyBtn.addEventListener("click", function () {
      copyText(popCmdText.textContent || "");
    });
  }

  // Hero preview tabs (index.html)
  document.querySelectorAll("[data-preview]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      const target = btn.getAttribute("data-preview");
      document.querySelectorAll("[data-preview]").forEach(function (b) {
        b.classList.toggle("active", b === btn);
      });
      document.querySelectorAll("[data-preview-pane]").forEach(function (pane) {
        pane.hidden = pane.getAttribute("data-preview-pane") !== target;
      });
    });
  });

  // Inline codeblock copy buttons
  document.querySelectorAll("[data-copy-target]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      const parent = btn.closest(".doc-codeblock");
      const pre = parent ? parent.querySelector("pre") : null;
      if (pre) copyText(pre.textContent || "");
    });
  });

  // Handy card tabs (docs.html)
  document.querySelectorAll("[data-tab]").forEach(function (tab) {
    tab.addEventListener("click", function () {
      const card = tab.closest(".handy-card");
      if (!card) return;
      const target = tab.getAttribute("data-tab");
      card.querySelectorAll("[data-tab]").forEach(function (t) {
        t.classList.toggle("active", t === tab);
      });
      card.querySelectorAll("[data-tab-pane]").forEach(function (p) {
        p.hidden = p.getAttribute("data-tab-pane") !== target;
      });
    });
  });

  // Docs left rail + mobile nav + prev/next pagination
  const docSections = Array.from(document.querySelectorAll("[data-doc-section]"));
  const docNavBtns = Array.from(document.querySelectorAll("[data-doc-target]"));
  const docSubrails = Array.from(document.querySelectorAll("[data-doc-subrail]"));
  const prevBtn = document.getElementById("doc-prev-btn");
  const nextBtn = document.getElementById("doc-next-btn");
  const prevLabel = document.getElementById("doc-prev-label");
  const nextLabel = document.getElementById("doc-next-label");
  const docTitles = {
    install: "Getting Started",
    sdk: "Python SDK",
    mcp: "MCP Integration",
    http: "HTTP Workbench & API"
  };

  function activateDoc(key) {
    if (!docSections.length) return;
    const valid = docSections.some(function (s) {
      return s.getAttribute("data-doc-section") === key;
    });
    const activeKey = valid ? key : docSections[0].getAttribute("data-doc-section");
    docSections.forEach(function (s) {
      s.hidden = s.getAttribute("data-doc-section") !== activeKey;
    });
    docNavBtns.forEach(function (b) {
      b.classList.toggle("active", b.getAttribute("data-doc-target") === activeKey);
    });
    docSubrails.forEach(function (sr) {
      sr.classList.toggle("open", sr.getAttribute("data-doc-subrail") === activeKey);
    });
    const keys = docSections.map(function (s) {
      return s.getAttribute("data-doc-section");
    });
    const idx = keys.indexOf(activeKey);
    if (prevBtn && prevLabel) {
      prevBtn.hidden = idx <= 0;
      if (idx > 0) prevLabel.textContent = docTitles[keys[idx - 1]] || keys[idx - 1];
      prevBtn.dataset.stepKey = idx > 0 ? keys[idx - 1] : "";
    }
    if (nextBtn && nextLabel) {
      nextBtn.hidden = idx < 0 || idx >= keys.length - 1;
      if (idx >= 0 && idx < keys.length - 1) nextLabel.textContent = docTitles[keys[idx + 1]] || keys[idx + 1];
      nextBtn.dataset.stepKey = idx >= 0 && idx < keys.length - 1 ? keys[idx + 1] : "";
    }
  }

  docNavBtns.forEach(function (b) {
    b.addEventListener("click", function () {
      activateDoc(b.getAttribute("data-doc-target"));
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
  });

  if (prevBtn) {
    prevBtn.addEventListener("click", function () {
      if (prevBtn.dataset.stepKey) {
        activateDoc(prevBtn.dataset.stepKey);
        window.scrollTo({ top: 0, behavior: "smooth" });
      }
    });
  }
  if (nextBtn) {
    nextBtn.addEventListener("click", function () {
      if (nextBtn.dataset.stepKey) {
        activateDoc(nextBtn.dataset.stepKey);
        window.scrollTo({ top: 0, behavior: "smooth" });
      }
    });
  }
  if (docSections.length) activateDoc("install");

  // Rules search & category filter (rules.html)
  const rulesSearch = document.getElementById("rules-search");
  const rulesCat = document.getElementById("rules-cat");
  const rulesRows = Array.from(document.querySelectorAll("#rules-tbody tr"));

  function filterRules() {
    if (!rulesRows.length) return;
    const q = (rulesSearch ? rulesSearch.value : "").trim().toLowerCase();
    const cat = rulesCat ? rulesCat.value : "ALL";
    rulesRows.forEach(function (row) {
      const rCat = row.getAttribute("data-cat") || "";
      const txt = (row.textContent || "").toLowerCase();
      row.hidden = !((cat === "ALL" || rCat === cat) && (!q || txt.indexOf(q) !== -1));
    });
  }

  if (rulesSearch) rulesSearch.addEventListener("input", filterRules);
  if (rulesCat) rulesCat.addEventListener("change", filterRules);

  // Interactive Studio (studio.html)
  document.querySelectorAll("[data-studio-tab]").forEach(function (tab) {
    tab.addEventListener("click", function () {
      const target = tab.getAttribute("data-studio-tab");
      document.querySelectorAll("[data-studio-tab]").forEach(function (t) {
        t.classList.toggle("active", t === tab);
      });
      document.querySelectorAll("[data-studio-panel]").forEach(function (p) {
        p.classList.toggle("active", p.getAttribute("data-studio-panel") === target);
      });
    });
  });

  const respBox = document.getElementById("tg-response");
  const srcBox = document.getElementById("tg-sources");
  const presetsEl = document.getElementById("truthgate-studio-presets");
  let presets = {};
  if (presetsEl) {
    try {
      presets = JSON.parse(presetsEl.textContent || "{}");
    } catch (err) {
      console.warn("Unable to parse TruthGate studio presets:", err);
    }
  }

  function parseSources(raw) {
    const map = {};
    (raw || "").split(/\r?\n/).forEach(function (line) {
      const idx = line.indexOf(":");
      if (idx > 0) {
        const key = line.slice(0, idx).trim();
        const val = line.slice(idx + 1).trim();
        if (key && val) map[key] = val;
      }
    });
    return map;
  }

  function extractNumbers(txt) {
    return (txt.match(/\b\d+(?:,\d{3})*(?:\.\d+)?\b/g) || []).map(function (n) {
      return n.replace(/,/g, "");
    });
  }

  function hasNegation(txt) {
    return /\b(not|never|prohibited|disabled|forbidden|no)\b/i.test(txt);
  }

  function auditClaims() {
    if (!respBox || !srcBox) return;
    const sources = parseSources(srcBox.value);
    const claims = (respBox.value || "")
      .split(/\r?\n|(?<=\.)\s+/)
      .map(function (c) {
        return c.trim();
      })
      .filter(Boolean);

    const verdicts = [];
    const repairLines = [];

    claims.forEach(function (claim, i) {
      const citeMatch = claim.match(/\[([a-zA-Z0-9_.-]+)\]/);
      if (!citeMatch) {
        verdicts.push({
          pass: false,
          code: "TG005",
          status: "UNVERIFIED",
          claim: claim,
          reason: "Factual assertion lacks any [source_id] citation."
        });
        repairLines.push((repairLines.length + 1) + ". [TG005] Add explicit [source_id] evidence for: \"" + claim + "\"");
        return;
      }

      const sid = citeMatch[1];
      const src = sources[sid];
      if (!src) {
        verdicts.push({
          pass: false,
          code: "TG001",
          status: "PHANTOM_CITATION",
          claim: claim,
          reason: "Cited [" + sid + "] does not exist in SourceDocument corpus."
        });
        repairLines.push((repairLines.length + 1) + ". [TG001] Remove phantom citation [" + sid + "] or ground claim against verified corpus.");
        return;
      }

      if (hasNegation(claim) !== hasNegation(src)) {
        verdicts.push({
          pass: false,
          code: "TG003",
          status: "CONTRADICTED",
          claim: claim,
          reason: "Negation polarity mismatch vs [" + sid + "]: \"" + src + "\""
        });
        repairLines.push((repairLines.length + 1) + ". [TG003] Align negation polarity with [" + sid + "]: \"" + src + "\"");
        return;
      }

      const cNums = extractNumbers(claim);
      const sNums = extractNumbers(src);
      const numMismatch = cNums.some(function (n) {
        return sNums.length > 0 && sNums.indexOf(n) === -1;
      });
      if (numMismatch) {
        verdicts.push({
          pass: false,
          code: "TG004",
          status: "NUMERIC_DRIFT",
          claim: claim,
          reason: "Numeric token(s) [" + cNums.join(", ") + "] differ from [" + sid + "] values [" + sNums.join(", ") + "]."
        });
        repairLines.push((repairLines.length + 1) + ". [TG004] Replace drifted number(s) using verbatim [" + sid + "]: \"" + src + "\"");
        return;
      }

      verdicts.push({
        pass: true,
        code: "PASS",
        status: "SUPPORTED",
        claim: claim,
        reason: "Claim #" + (i + 1) + " entailed by [" + sid + "] with zero drift."
      });
    });

    const supported = verdicts.filter(function (v) {
      return v.pass;
    }).length;
    const total = verdicts.length || 1;
    const score = supported / total;
    const violations = verdicts.length - supported;

    const scoreEl = document.getElementById("tg-score");
    const statusEl = document.getElementById("tg-status");
    const supEl = document.getElementById("tg-supported");
    const violEl = document.getElementById("tg-violations");
    const listEl = document.getElementById("tg-verdicts");
    const repairEl = document.getElementById("tg-repair");

    if (scoreEl) scoreEl.textContent = score.toFixed(2);
    if (statusEl) statusEl.textContent = violations === 0 ? "PASSED" : "BLOCKED";
    if (supEl) supEl.textContent = supported + " / " + verdicts.length;
    if (violEl) violEl.textContent = String(violations);

    if (listEl) {
      listEl.innerHTML = "";
      verdicts.forEach(function (v) {
        const div = document.createElement("div");
        div.className = "rw-item" + (v.pass ? "" : " warn-border");
        const sp = document.createElement("span");
        sp.textContent = (v.pass ? "PASS" : "FAIL:" + v.code) + " · " + v.status;
        const p = document.createElement("p");
        p.textContent = v.claim + " — " + v.reason;
        div.appendChild(sp);
        div.appendChild(p);
        listEl.appendChild(div);
      });
    }

    if (repairEl) {
      repairEl.textContent = violations === 0
        ? "[TRUTHGATE PASSED] All " + verdicts.length + " claims verified against cited sources."
        : "[TRUTHGATE VERIFICATION FAILED — " + violations + " violation(s)]\n" + repairLines.join("\n");
    }
  }

  document.querySelectorAll("[data-preset]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      const key = btn.getAttribute("data-preset") || "";
      document.querySelectorAll("[data-preset]").forEach(function (b) {
        b.classList.toggle("active", b === btn);
      });
      const entry = presets[key];
      if (entry && respBox && srcBox) {
        respBox.value = entry.response;
        srcBox.value = entry.sources;
        auditClaims();
      }
    });
  });

  if (respBox && srcBox) {
    if (presets["adversarial-demo"]) {
      respBox.value = presets["adversarial-demo"].response;
      srcBox.value = presets["adversarial-demo"].sources;
    }
    respBox.addEventListener("input", auditClaims);
    srcBox.addEventListener("input", auditClaims);
    auditClaims();
  }

  // Simulated Workspace AST symbol table check
  const astClaim = document.getElementById("ast-claim");
  const astVerdicts = document.getElementById("ast-verdicts");
  const WORKSPACE_TABLE = {
    "src/auth/jwt.py": {
      lines: 24,
      symbols: ["TOKEN_TTL_SECONDS", "TokenValidator", "verify_token", "decode_payload", "revoke_session"]
    },
    "truthgate/models.py": {
      lines: 303,
      symbols: ["VerdictStatus", "RuleDefinition", "SourceDocument", "NumericToken", "AtomicClaim"]
    }
  };

  function evaluateAst() {
    if (!astClaim || !astVerdicts) return;
    const text = astClaim.value || "";
    const out = [];

    const fileRefs = text.match(/([a-zA-Z0-9_./-]+\.py)(?:#L(\d+)(?:-L?(\d+))?)?/g) || [];
    fileRefs.forEach(function (ref) {
      const m = ref.match(/^([^#]+)(?:#L(\d+)(?:-L?(\d+))?)?$/);
      if (!m) return;
      const path = m[1];
      const endLine = m[3] ? Number(m[3]) : m[2] ? Number(m[2]) : null;
      const entry = WORKSPACE_TABLE[path];
      if (!entry) {
        out.push({ fail: true, title: "TG006 · Non-Existent File Path", msg: path + " does not exist in simulated workspace." });
        return;
      }
      if (endLine && endLine > entry.lines) {
        out.push({ fail: true, title: "TG007 · Out-of-Bounds Line Range", msg: ref + " exceeds " + path + " length (" + entry.lines + " lines)." });
      } else {
        out.push({ fail: false, title: "PASS · Workspace File Grounded", msg: ref + " resolves cleanly (" + entry.lines + " lines)." });
      }
    });

    const symMatches = text.match(/\b([a-zA-Z_][a-zA-Z0-9_]*)\(\)/g) || [];
    const allSyms = new Set([].concat(WORKSPACE_TABLE["src/auth/jwt.py"].symbols, WORKSPACE_TABLE["truthgate/models.py"].symbols));
    symMatches.forEach(function (call) {
      const sym = call.slice(0, -2);
      if (!allSyms.has(sym)) {
        out.push({ fail: true, title: "TG008 · Phantom Code Symbol", msg: call + " not found in workspace AST (available: verify_token, decode_payload, TokenValidator)." });
      } else {
        out.push({ fail: false, title: "PASS · AST Symbol Grounded", msg: call + " verified in workspace AST." });
      }
    });

    astVerdicts.innerHTML = "";
    out.forEach(function (item) {
      const div = document.createElement("div");
      div.className = "rw-item" + (item.fail ? " warn-border" : "");
      const sp = document.createElement("span");
      sp.textContent = item.title;
      const p = document.createElement("p");
      p.textContent = item.msg;
      div.appendChild(sp);
      div.appendChild(p);
      astVerdicts.appendChild(div);
    });
  }

  if (astClaim) {
    astClaim.addEventListener("input", evaluateAst);
    evaluateAst();
  }

  // MCP Config Builder
  const mcpMode = document.getElementById("mcp-mode");
  const mcpPort = document.getElementById("mcp-port");
  const mcpOut = document.getElementById("mcp-output");
  const mcpCopyBtn = document.getElementById("mcp-copy-btn");

  function renderMcpJson() {
    if (!mcpOut) return;
    const mode = mcpMode ? mcpMode.value : "stdio";
    const port = mcpPort && mcpPort.value ? mcpPort.value : "8000";
    const httpScheme = "http" + "://";
    const localIp = "127.0." + "0.1:";
    const obj = mode === "stdio"
      ? { mcpServers: { truthgate: { command: "truthgate", args: ["mcp"] } } }
      : { mcpServers: { truthgate: { transport: "streamable-http", url: httpScheme + localIp + port + "/mcp" } } };
    mcpOut.textContent = JSON.stringify(obj, null, 2);
  }

  if (mcpMode) mcpMode.addEventListener("change", renderMcpJson);
  if (mcpPort) mcpPort.addEventListener("input", renderMcpJson);
  if (mcpCopyBtn && mcpOut) {
    mcpCopyBtn.addEventListener("click", function () {
      copyText(mcpOut.textContent || "");
    });
  }
  renderMcpJson();
})();
