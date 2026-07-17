#!/usr/bin/env node
// Read-only X search collector for an existing dedicated Chrome CDP session.

function parseArgs(argv) {
  const options = {
    cdp: "http://127.0.0.1:9225",
    expectedHandle: "AgentJc11443",
    maxPosts: 30,
    scrolls: 3,
    waitMs: 10000,
    cutoff: null,
    summaryOnly: false,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const name = argv[index];
    if (name === "--summary-only") {
      options.summaryOnly = true;
      continue;
    }
    const value = argv[index + 1];
    if (!value) throw new Error(`missing value for ${name}`);
    index += 1;
    if (name === "--cdp") options.cdp = value;
    else if (name === "--query-url") options.queryUrl = value;
    else if (name === "--expected-handle") options.expectedHandle = value.replace(/^@/, "");
    else if (name === "--max-posts") options.maxPosts = Number(value);
    else if (name === "--scrolls") options.scrolls = Number(value);
    else if (name === "--wait-ms") options.waitMs = Number(value);
    else if (name === "--cutoff") options.cutoff = value;
    else throw new Error(`unknown argument: ${name}`);
  }
  if (!options.queryUrl) throw new Error("--query-url is required");
  if (!Number.isInteger(options.maxPosts) || options.maxPosts < 1 || options.maxPosts > 100) {
    throw new Error("--max-posts must be an integer from 1 to 100");
  }
  if (!Number.isInteger(options.scrolls) || options.scrolls < 0 || options.scrolls > 10) {
    throw new Error("--scrolls must be an integer from 0 to 10");
  }
  if (!Number.isInteger(options.waitMs) || options.waitMs < 1000 || options.waitMs > 30000) {
    throw new Error("--wait-ms must be an integer from 1000 to 30000");
  }
  if (!/^[A-Za-z0-9_]{1,15}$/.test(options.expectedHandle)) {
    throw new Error("--expected-handle is invalid");
  }
  const cdpUrl = new URL(options.cdp);
  if (cdpUrl.protocol !== "http:" || !["127.0.0.1", "localhost"].includes(cdpUrl.hostname)) {
    throw new Error("--cdp must be a loopback HTTP endpoint");
  }
  const queryUrl = new URL(options.queryUrl);
  if (!["x.com", "www.x.com"].includes(queryUrl.hostname) || queryUrl.pathname !== "/search") {
    throw new Error("--query-url must be an https://x.com/search URL");
  }
  if (queryUrl.protocol !== "https:") throw new Error("--query-url must use HTTPS");
  if (options.cutoff && Number.isNaN(Date.parse(options.cutoff))) {
    throw new Error("--cutoff must be an ISO timestamp");
  }
  options.cdp = cdpUrl.origin;
  options.queryUrl = queryUrl.href;
  return options;
}

const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

class CdpClient {
  constructor(webSocketUrl) {
    this.socket = new WebSocket(webSocketUrl);
    this.pending = new Map();
    this.nextId = 1;
    this.opened = new Promise((resolve, reject) => {
      this.socket.addEventListener("open", resolve, { once: true });
      this.socket.addEventListener("error", () => reject(new Error("CDP socket error")), { once: true });
    });
    this.socket.addEventListener("message", (event) => {
      const message = JSON.parse(event.data);
      if (!message.id || !this.pending.has(message.id)) return;
      const { resolve, reject } = this.pending.get(message.id);
      this.pending.delete(message.id);
      if (message.error) reject(new Error(message.error.message));
      else resolve(message.result);
    });
  }

  async call(method, params = {}) {
    await this.opened;
    const id = this.nextId++;
    const result = new Promise((resolve, reject) => this.pending.set(id, { resolve, reject }));
    this.socket.send(JSON.stringify({ id, method, params }));
    return result;
  }

  close() {
    this.socket.close();
  }
}

async function evaluate(client, expression) {
  const response = await client.call("Runtime.evaluate", {
    expression,
    returnByValue: true,
    awaitPromise: true,
  });
  if (response.exceptionDetails) throw new Error("page evaluation failed");
  return response.result?.value;
}

async function waitFor(client, expression, waitMs) {
  const deadline = Date.now() + waitMs;
  let value;
  while (Date.now() < deadline) {
    value = await evaluate(client, expression);
    if (value?.ready) return value;
    await sleep(500);
  }
  return value;
}

const sessionExpression = String.raw`(() => {
  const button = document.querySelector('[data-testid="SideNav_AccountSwitcher_Button"]');
  const text = button?.innerText || '';
  const body = document.body?.innerText || '';
  return {
    ready: document.readyState === 'complete' && (Boolean(button) || body.includes('Something went wrong')),
    searchPage: location.pathname === '/search',
    signedIn: Boolean(button),
    accountMatches: text.includes('@__EXPECTED_HANDLE__'),
    loadError: body.includes('Something went wrong') || body.includes('Try reloading')
  };
})()`;

const resultsReadyExpression = String.raw`(() => {
  const body = document.body?.innerText || '';
  const count = document.querySelectorAll('article[data-testid="tweet"]').length;
  return {
    ready: count > 0 || body.includes('No results for'),
    count,
    empty: body.includes('No results for')
  };
})()`;

const extractExpression = String.raw`(() => {
  const isStatusPath = (href) => {
    if (!href.includes('/status/')) return false;
    const id = (href.split('/status/')[1] || '').split('?')[0].split('/')[0];
    return id.length > 0 && [...id].every((char) => char >= '0' && char <= '9');
  };
  return [...document.querySelectorAll('article[data-testid="tweet"]')].map((article) => {
    const userText = article.querySelector('[data-testid="User-Name"]')?.innerText || '';
    const userLines = userText.split(String.fromCharCode(10)).map((line) => line.trim()).filter(Boolean);
    const statusPath = [...article.querySelectorAll('a[href*="/status/"]')]
      .map((node) => node.getAttribute('href') || '')
      .find(isStatusPath) || '';
    const statusUrl = statusPath ? new URL(statusPath, location.origin).href.split('?')[0] : '';
    const tweetText = article.querySelector('[data-testid="tweetText"]');
    const engagement = {};
    for (const name of ['reply', 'retweet', 'like']) {
      const control = article.querySelector('[data-testid="' + name + '"]');
      if (control?.getAttribute('aria-label')) engagement[name] = control.getAttribute('aria-label');
    }
    return {
      statusUrl,
      authorHandle: userLines.find((line) => line.startsWith('@')) || '',
      displayName: userLines.find((line) => !line.startsWith('@')) || '',
      timestamp: article.querySelector('time')?.getAttribute('datetime') || '',
      text: tweetText?.innerText || '',
      language: tweetText?.getAttribute('lang') || null,
      hasMedia: Boolean(article.querySelector('[data-testid="tweetPhoto"], video')),
      hasQuotedPost: Boolean(article.querySelector('[data-testid="quoteTweet"]')),
      engagement
    };
  });
})()`;

async function collect(options) {
  const createResponse = await fetch(`${options.cdp}/json/new?${encodeURIComponent(options.queryUrl)}`, {
    method: "PUT",
  });
  if (!createResponse.ok) throw new Error(`could not create temporary X tab (${createResponse.status})`);
  const target = await createResponse.json();
  if (!target.id || !target.webSocketDebuggerUrl) throw new Error("CDP target response was incomplete");

  let client;
  let output;
  let exitCode = 0;
  let tabClosed = false;
  try {
    client = new CdpClient(target.webSocketDebuggerUrl);
    await client.call("Runtime.enable");
    const session = await waitFor(
      client,
      sessionExpression.replace("__EXPECTED_HANDLE__", options.expectedHandle),
      options.waitMs,
    );
    if (!session?.searchPage) throw new Error("temporary target left the X search surface");
    if (session.loadError) throw new Error("X search returned a load error");
    if (!session.signedIn || !session.accountMatches) {
      output = { ok: false, error: "session-canary-failed", requiresReauthentication: true };
      exitCode = 3;
    } else {
      const readiness = await waitFor(client, resultsReadyExpression, options.waitMs);
      const posts = new Map();
      for (let pass = 0; pass <= options.scrolls; pass += 1) {
        const rows = (await evaluate(client, extractExpression)) || [];
        for (const row of rows) {
          if (!row.statusUrl || !row.authorHandle || !row.timestamp) continue;
          if (options.cutoff && Date.parse(row.timestamp) < Date.parse(options.cutoff)) continue;
          posts.set(row.statusUrl, row);
          if (posts.size >= options.maxPosts) break;
        }
        if (posts.size >= options.maxPosts || pass === options.scrolls) break;
        await evaluate(client, "window.scrollBy(0, Math.max(800, Math.floor(window.innerHeight * 0.9))); true");
        await sleep(900);
      }
      const rows = [...posts.values()].slice(0, options.maxPosts);
      const authors = new Set(rows.map((row) => row.authorHandle.toLowerCase()));
      output = {
        ok: true,
        account: `@${options.expectedHandle}`,
        collectedAt: new Date().toISOString(),
        search: {
          url: options.queryUrl,
          emptyResultUi: Boolean(readiness?.empty),
        },
        coverage: {
          postCount: rows.length,
          uniqueAuthorCount: authors.size,
          withText: rows.filter((row) => row.text.trim()).length,
          withMedia: rows.filter((row) => row.hasMedia).length,
          scrollPasses: options.scrolls + 1,
        },
      };
      if (!options.summaryOnly) output.posts = rows;
    }
  } catch (error) {
    output = { ok: false, error: "collector-failed", detail: error.message };
    exitCode = 4;
  } finally {
    client?.close();
    try {
      const closeResponse = await fetch(`${options.cdp}/json/close/${target.id}`);
      tabClosed = closeResponse.ok;
    } catch {
      tabClosed = false;
    }
  }
  output.temporaryTabClosed = tabClosed;
  process.stdout.write(`${JSON.stringify(output, null, 2)}\n`);
  return exitCode;
}

let options;
try {
  options = parseArgs(process.argv.slice(2));
} catch (error) {
  process.stderr.write(`${error.message}\n`);
  process.exit(2);
}

process.exitCode = await collect(options);
