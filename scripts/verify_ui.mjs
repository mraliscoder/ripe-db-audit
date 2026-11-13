import { spawn } from "node:child_process";
import { mkdtemp, readFile, writeFile, mkdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";

const root = resolve(fileURLToPath(new URL("..", import.meta.url)));
const artifacts = join(root, "artifacts");
await mkdir(artifacts, { recursive: true });
const temp = await mkdtemp(join(tmpdir(), "arc-app-ui-"));
const python = spawn(
  join(root, ".venv/bin/python"),
  [join(root, "scripts/ui_test_server.py")],
  { cwd: root, stdio: ["ignore", "ignore", "pipe"] },
);
let serverLog = "";
python.stderr.on("data", (v) => (serverLog += v.toString()));
const chrome = spawn(
  process.env.CHROME_PATH ||
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  [
    "--headless=new",
    "--disable-gpu",
    ...(process.env.CI ? ["--no-sandbox"] : []),
    "--disable-background-networking",
    "--no-first-run",
    "--no-default-browser-check",
    "--remote-debugging-port=0",
    "--user-data-dir=" + temp,
    "about:blank",
  ],
  { stdio: ["ignore", "ignore", "pipe"] },
);
let browserLog = "";
chrome.stderr.on("data", (v) => (browserLog += v.toString()));
const delay = (ms) => new Promise((r) => setTimeout(r, ms));
let ws,
  seq = 0;
const pending = new Map(),
  errors = [];
const checks = [];
const historyRequests = [];
try {
  let endpoint;
  for (let i = 0; i < 150; i++) {
    try {
      const [port, path] = (
        await readFile(join(temp, "DevToolsActivePort"), "utf8")
      )
        .trim()
        .split("\n");
      endpoint = "ws://127.0.0.1:" + port + path;
      break;
    } catch {}
    if (chrome.exitCode !== null) throw Error(browserLog);
    await delay(100);
  }
  assert.ok(endpoint, "Chrome started");
  for (let i = 0; i < 100; i++) {
    try {
      if ((await fetch("http://127.0.0.1:8091/api/health")).ok) break;
    } catch {}
    if (python.exitCode !== null) throw Error(serverLog);
    await delay(100);
  }
  ws = new WebSocket(endpoint);
  await new Promise((resolve, reject) => {
    ws.addEventListener("open", resolve, { once: true });
    ws.addEventListener("error", reject, { once: true });
  });
  ws.addEventListener("message", (e) => {
    const m = JSON.parse(e.data);
    if (
      m.method === "Network.requestWillBeSent" &&
      m.params.request.method === "GET" &&
      m.params.request.url === "http://127.0.0.1:8091/api/analyses"
    )
      historyRequests.push(m.params.request.url);
    if (m.method === "Runtime.exceptionThrown")
      errors.push(m.params.exceptionDetails);
    if (m.id && pending.has(m.id)) {
      const p = pending.get(m.id);
      pending.delete(m.id);
      clearTimeout(p.timer);
      m.error ? p.reject(Error(JSON.stringify(m.error))) : p.resolve(m.result);
    }
  });
  const call = (method, params = {}, sessionId) =>
    new Promise((resolve, reject) => {
      const id = ++seq;
      const timer = setTimeout(
        () => reject(Error("CDP timeout " + method)),
        15000,
      );
      pending.set(id, { resolve, reject, timer });
      ws.send(
        JSON.stringify({
          id,
          method,
          params,
          ...(sessionId ? { sessionId } : {}),
        }),
      );
    });
  const { targetId } = await call("Target.createTarget", {
    url: "about:blank",
  });
  const { sessionId } = await call("Target.attachToTarget", {
    targetId,
    flatten: true,
  });
  const command = (method, params) => call(method, params, sessionId);
  await call("Target.activateTarget", { targetId });
  await command("Emulation.setFocusEmulationEnabled", { enabled: true });
  await command("Runtime.enable");
  await command("Page.enable");
  await command("Network.enable");
  await command("Emulation.setDeviceMetricsOverride", {
    width: 1512,
    height: 1040,
    deviceScaleFactor: 1,
    mobile: false,
  });
  const evaluate = async (expression) => {
    const r = await command("Runtime.evaluate", {
      expression,
      returnByValue: true,
      awaitPromise: true,
    });
    if (r.exceptionDetails) throw Error(JSON.stringify(r.exceptionDetails));
    return r.result.value;
  };
  const wait = async (expression) => {
    for (let i = 0; i < 140; i++) {
      if (await evaluate(expression)) return;
      await delay(100);
    }
    throw Error("UI timeout: " + expression);
  };
  const click = async (selector) => {
    await wait(`!!document.querySelector(${JSON.stringify(selector)})`);
    await evaluate(
      `document.querySelector(${JSON.stringify(selector)}).click()`,
    );
  };
  const fill = async (selector, value) =>
    evaluate(
      `(()=>{const e=document.querySelector(${JSON.stringify(selector)});Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value").set.call(e,${JSON.stringify(value)});e.dispatchEvent(new Event('input',{bubbles:true}));})()`,
    );
  const submit = async (value) => {
    if (await evaluate('!!document.getElementById("another-analysis")')) {
      await click("#another-analysis");
      await wait('!!document.getElementById("analysis-form")');
      assert.equal(
        await evaluate('document.querySelectorAll("a.nav-link").length'),
        0,
      );
      assert.equal(
        await evaluate('new URLSearchParams(location.search).has("job")'),
        false,
      );
      await wait('document.activeElement.id === "regid"');
    }
    await fill("#regid", value);
    await evaluate(`document.getElementById('analysis-form').requestSubmit()`);
  };
  const screenshot = async (name) => {
    const shot = await command("Page.captureScreenshot", {
      format: "png",
      captureBeyondViewport: false,
    });
    await writeFile(
      join(artifacts, name + ".png"),
      Buffer.from(shot.data, "base64"),
    );
  };
  await command("Page.navigate", { url: "http://127.0.0.1:8091/" });
  await wait(
    'document.readyState==="complete" && !!document.getElementById("analysis-form")',
  );
  assert.equal(
    await evaluate('document.getElementById("summary").hidden'),
    true,
  );
  assert.equal(
    await evaluate(
      'getComputedStyle(document.getElementById("summary")).display',
    ),
    "none",
  );
  await screenshot("initial");
  assert.equal(await evaluate('document.getElementById("regid").value'), "");
  assert.equal(
    await evaluate('document.getElementById("regid").placeholder'),
    "ru.ecode",
  );
  checks.push(
    "Empty state: blank regID with example placeholder, no fabricated results",
  );
  assert.equal(
    await evaluate('document.querySelectorAll("a.nav-link").length'),
    0,
  );
  assert.equal(
    await evaluate(
      'document.body.textContent.includes("Recent analyses") || document.body.textContent.includes("Public RIPE data") || document.body.textContent.includes("Results cached for 1 hour")',
    ),
    false,
  );
  checks.push(
    "No unavailable navigation, shared history or redundant labels before scan",
  );
  await submit("bad");
  await delay(50);
  assert.equal(
    await evaluate(
      'document.getElementById("regid").getAttribute("aria-invalid")',
    ),
    "true",
  );
  checks.push("Field validation and focus");
  await submit("ru.ecode");
  await wait('!document.getElementById("progress-panel").hidden');
  assert.equal(
    await evaluate('document.getElementById("start").disabled'),
    true,
  );
  assert.equal(
    await evaluate('document.querySelectorAll("a.nav-link").length'),
    0,
  );
  await screenshot("progress");
  checks.push("Sidebar navigation hidden while analysis runs");
  await wait(
    '!document.getElementById("workspace").hidden && document.getElementById("progress-panel").hidden',
  );
  checks.push("SSE stages to completed result");
  assert.equal(
    await evaluate('document.querySelectorAll("a.nav-link").length'),
    3,
  );
  assert.equal(
    await evaluate(
      '!!document.getElementById("analysis-form") || !!document.getElementById("regid") || !!document.getElementById("start")',
    ),
    false,
  );
  assert.equal(
    await evaluate(
      'document.getElementById("another-analysis").textContent.includes("Another analysis")',
    ),
    true,
  );
  assert.ok(
    await evaluate(
      'document.querySelector(".cache-expiry").textContent.includes("Results cached until")',
    ),
  );
  checks.push(
    "Completed scan replaces form with Another analysis and exact cache expiry",
  );
  const job = await evaluate('new URLSearchParams(location.search).get("job")');
  await wait('document.querySelectorAll(".data-table tbody tr").length>0');
  assert.equal(
    await evaluate(
      '!!document.querySelector("aside #result-header") && !!document.querySelector("aside #summary") && !!document.querySelector("aside .sidebar-audit-heading")',
    ),
    true,
  );
  assert.equal(
    await evaluate(
      '!!document.querySelector("main #result-header") || !!document.querySelector("main #summary") || !!document.querySelector("main .page-heading")',
    ),
    false,
  );
  checks.push(
    "Audit context and counts live in sidebar only; main area is for results",
  );
  await screenshot("resources");
  const sidebarBefore = await evaluate(
    'document.querySelector(".sidebar").getBoundingClientRect().top',
  );
  await evaluate("window.scrollTo(0, 700)");
  await delay(100);
  assert.ok((await evaluate("window.scrollY")) > 0);
  assert.equal(
    await evaluate(
      'document.querySelector(".sidebar").getBoundingClientRect().top',
    ),
    sidebarBefore,
  );
  assert.ok(
    await evaluate(
      'document.querySelector(".sidebar-foot").getBoundingClientRect().bottom <= innerHeight',
    ),
  );
  await screenshot("sidebar-scrolled");
  await evaluate("window.scrollTo(0, 0)");
  checks.push(
    "Desktop sidebar and copyright stay in viewport while results scroll",
  );

  await submit("ru.ecode");
  await wait(
    '!document.getElementById("workspace").hidden && !!document.getElementById("another-analysis")',
  );
  assert.equal(
    await evaluate('new URLSearchParams(location.search).get("job")'),
    job,
  );
  checks.push(
    "Another analysis resets focus and URL; cache returns same completed job",
  );
  await click('a.nav-link[href*="view=objects"]');
  await wait('document.querySelectorAll(".data-table tbody tr").length>0');
  const totalRows = await evaluate(
    'document.querySelectorAll(".data-table tbody tr").length',
  );
  assert.ok(totalRows <= 50);
  await evaluate(
    '[...document.querySelectorAll("#pagination button")].find(b=>b.textContent.includes("Next")).click()',
  );
  await wait(
    'document.getElementById("pagination")?.textContent.includes("51")',
  );
  await command("Page.reload");
  await wait(
    'document.getElementById("pagination")?.textContent.includes("51")',
  );
  checks.push("Server pagination and URL restoration");
  await click(".prefix-button");
  await wait(
    '!!document.getElementById("resource-panel") && document.querySelector(".detail-title").textContent!=="Resource details"',
  );
  assert.ok(
    await evaluate(
      'document.getElementById("resource-panel").textContent.includes("ORG")',
    ),
  );
  await screenshot("resource-detail");
  checks.push("Resource detail and provenance");
  await command("Input.dispatchKeyEvent", {
    type: "keyDown",
    key: "Escape",
    code: "Escape",
    windowsVirtualKeyCode: 27,
  });
  await wait('!document.getElementById("resource-panel")');
  assert.equal(
    await evaluate(
      'document.activeElement.classList.contains("prefix-button")',
    ),
    true,
  );
  checks.push("Modal sheet Escape and focus restoration");
  await click('a.nav-link[href*="view=tree"]');
  await wait('!!document.querySelector(".tree-toggle")');
  const before = await evaluate(
    'document.querySelectorAll(".data-table tbody tr").length',
  );
  await click(".tree-toggle");
  await wait(
    `document.querySelectorAll('.data-table tbody tr').length>${before}`,
  );
  checks.push("Lazy hierarchy expansion");
  await click('a.nav-link[href*="view=findings"]');
  await wait(
    'document.getElementById("data-panel").getAttribute("aria-busy")==="false"',
  );
  await fill("#search", "no-match-zzzz");
  await wait(
    'document.getElementById("data-panel").textContent.includes("No findings")',
  );
  await click("#clear-search");
  await wait('document.getElementById("search").value===""');
  assert.equal(await evaluate("document.activeElement.id"), "search");
  checks.push("Search, no-results, immediate clear and focus");
  await wait('document.querySelectorAll(".finding-message").length>0');
  assert.equal(
    await evaluate(
      '[...document.querySelectorAll(".finding-message")].some(e=>/[А-Яа-я]/.test(e.textContent))',
    ),
    false,
  );
  checks.push("Legacy cached finding text renders in English");

  await click('a.nav-link[href*="view=objects"]');
  await wait('!!document.querySelector("#pagination [role=combobox]")');
  await evaluate(
    'document.querySelector("#pagination [role=combobox]").focus()',
  );
  await command("Input.dispatchKeyEvent", {
    type: "keyDown",
    key: "ArrowDown",
    code: "ArrowDown",
    windowsVirtualKeyCode: 40,
  });
  await command("Input.dispatchKeyEvent", {
    type: "keyUp",
    key: "ArrowDown",
    code: "ArrowDown",
    windowsVirtualKeyCode: 40,
  });
  await wait('!!document.querySelector("[role=listbox]")');
  await screenshot("shadcn-select");
  await command("Input.dispatchKeyEvent", {
    type: "keyDown",
    key: "Home",
    code: "Home",
    windowsVirtualKeyCode: 36,
  });
  await command("Input.dispatchKeyEvent", {
    type: "keyUp",
    key: "Home",
    code: "Home",
    windowsVirtualKeyCode: 36,
  });
  await delay(100);
  await command("Input.dispatchKeyEvent", {
    type: "keyDown",
    key: "Enter",
    code: "Enter",
    windowsVirtualKeyCode: 13,
  });
  await command("Input.dispatchKeyEvent", {
    type: "keyUp",
    key: "Enter",
    code: "Enter",
    windowsVirtualKeyCode: 13,
  });
  await wait(
    'document.querySelector("#pagination [role=combobox]").textContent.includes("25")',
  );
  checks.push("shadcn Select keyboard popup and page-size change");
  await wait(
    'document.getElementById("data-panel").getAttribute("aria-busy")==="false"',
  );
  const axe = await readFile(
    join(root, "frontend/node_modules/axe-core/axe.min.js"),
    "utf8",
  );
  await evaluate(axe);
  let violations = await evaluate(
    '(async()=>{const r=await axe.run(document,{runOnly:{type:"tag",values:["wcag2a","wcag2aa","wcag21aa","wcag22aa"]}});return r.violations.map(v=>({id:v.id,impact:v.impact,description:v.description,nodes:v.nodes.map(n=>n.target)}));})()',
  );
  await writeFile(
    join(artifacts, "accessibility.json"),
    JSON.stringify(violations, null, 2),
  );
  assert.equal(
    violations.length,
    0,
    "Accessibility: " + JSON.stringify(violations),
  );
  checks.push("axe WCAG 2.2 AA automated baseline");
  await command("Emulation.setDeviceMetricsOverride", {
    width: 390,
    height: 844,
    deviceScaleFactor: 1,
    mobile: true,
  });
  await evaluate("window.scrollTo(0,0)");
  const width = await evaluate(
    "({viewport:innerWidth,document:document.documentElement.scrollWidth})",
  );
  assert.ok(
    width.document <= width.viewport,
    "Mobile overflow: " + JSON.stringify(width),
  );
  await screenshot("mobile");
  checks.push("Narrow viewport without page overflow");
  await command("Emulation.setEmulatedMedia", {
    features: [{ name: "prefers-reduced-motion", value: "reduce" }],
  });
  await submit("aa.fail");
  await wait(
    'document.getElementById("notice").textContent.includes("regID not found")',
  );
  assert.equal(
    await evaluate('document.getElementById("start").disabled'),
    false,
  );
  assert.equal(
    await evaluate('document.querySelectorAll("a.nav-link").length'),
    0,
  );
  checks.push(
    "Failure and recovery preserve usable UI without unavailable navigation",
  );
  await submit("aa.empty");
  await wait(
    'document.getElementById("result-header")?.textContent.includes("aa.empty")',
  );
  await click('a.nav-link[href*="view=findings"]');
  await wait(
    'document.getElementById("data-panel").textContent.includes("No findings")',
  );
  checks.push("Zero-resource and clean findings states");
  await submit("aa.partial");
  await wait(
    'document.getElementById("notice").textContent.includes("Incomplete analysis")',
  );
  checks.push("Incomplete result clearly labelled");
  await command("Network.setBlockedURLs", { urls: ["*events*"] });
  await submit("aa.polling");
  await wait(
    'document.getElementById("result-header")?.textContent.includes("aa.polling")',
  );
  checks.push("SSE unavailable: polling completes");
  await submit("aa.asn-status");
  await wait(
    '!document.getElementById("workspace").hidden && document.querySelectorAll(".data-table tbody tr").length===4',
  );
  assert.equal(
    await evaluate('document.querySelectorAll(".recent-inactive-asn").length'),
    3,
  );
  assert.equal(
    await evaluate(
      '[...document.querySelectorAll(".bgp-label.warning")].filter(e=>e.textContent === "Inactive < 3 months").length',
    ),
    2,
  );
  assert.equal(
    await evaluate(
      '[...document.querySelectorAll(".data-table tbody tr")].find(e=>e.textContent.includes("AS64498")).classList.contains("recent-inactive-asn")',
    ),
    false,
  );
  assert.equal(
    await evaluate(
      'document.getElementById("summary").textContent.includes("Findings0")',
    ),
    true,
  );
  await screenshot("asn-inactive-warning");
  checks.push(
    "Recently inactive and new unannounced ASNs are yellow, active ASN is neutral; findings unchanged",
  );
  assert.equal(
    historyRequests.length,
    0,
    "No shared analysis history is requested",
  );
  checks.push("No global history fetches throughout scan and reset flows");
  assert.equal(
    errors.length,
    0,
    "JavaScript errors: " + JSON.stringify(errors),
  );
  await writeFile(
    join(artifacts, "ui-report.json"),
    JSON.stringify(
      { passed: true, checks, jsErrors: errors, viewport: width },
      null,
      2,
    ),
  );
  console.log(JSON.stringify({ passed: true, checks, jsErrors: 0 }, null, 2));
} catch (error) {
  console.error(error);
  process.exitCode = 1;
} finally {
  ws?.close();
  chrome.kill("SIGTERM");
  python.kill("SIGTERM");
  await delay(400);
  await rm(temp, {
    recursive: true,
    force: true,
    maxRetries: 5,
    retryDelay: 200,
  });
}
