const AUTH_PATH = "/__auth";
const LOGOUT_PATH = "/__auth/logout";
const REFRESH_PATH = "/__refresh";
const COOKIE_NAME = "skt_report_auth";
const PBKDF2_ITERATIONS = 100000;
const SESSION_MAX_AGE = 60 * 60 * 24 * 7;

const GITHUB_OWNER = "yongchengliao2-web";
const GITHUB_REPOSITORY = "skt-singapore-report";
const GITHUB_BRANCH = "main";
const GITHUB_WORKFLOW = "refresh-main-report.yml";
const GITHUB_API_ROOT = `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPOSITORY}`;
const RUN_TITLE_PREFIX = "SKT refresh ";

function secret(env, name) {
  return String(env?.[name] || "").trim();
}

function hexToBytes(value) {
  const bytes = new Uint8Array(value.length / 2);
  for (let index = 0; index < value.length; index += 2) {
    bytes[index / 2] = Number.parseInt(value.slice(index, index + 2), 16);
  }
  return bytes;
}

function bytesToHex(value) {
  return [...value].map(byte => byte.toString(16).padStart(2, "0")).join("");
}

function timingSafeEqual(left, right) {
  if (!left || !right || left.length !== right.length) return false;
  let difference = 0;
  for (let index = 0; index < left.length; index += 1) {
    difference |= left.charCodeAt(index) ^ right.charCodeAt(index);
  }
  return difference === 0;
}

async function derivePasswordHash(password, saltHex) {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(password),
    "PBKDF2",
    false,
    ["deriveBits"],
  );
  const bits = await crypto.subtle.deriveBits(
    {
      name: "PBKDF2",
      hash: "SHA-256",
      salt: hexToBytes(saltHex),
      iterations: PBKDF2_ITERATIONS,
    },
    key,
    256,
  );
  return bytesToHex(new Uint8Array(bits));
}

function cookieValue(request) {
  const header = request.headers.get("Cookie") || "";
  for (const part of header.split(";")) {
    const separator = part.indexOf("=");
    if (separator < 0) continue;
    const name = part.slice(0, separator).trim();
    if (name === COOKIE_NAME) return part.slice(separator + 1).trim();
  }
  return "";
}

function isAuthorized(request, env) {
  const sessionToken = secret(env, "REPORT_SESSION_TOKEN");
  return Boolean(sessionToken) && timingSafeEqual(cookieValue(request), sessionToken);
}

function sessionCookie(env, maxAge = SESSION_MAX_AGE, value = null) {
  const token = value ?? secret(env, "REPORT_SESSION_TOKEN");
  return `${COOKIE_NAME}=${token}; Path=/; Max-Age=${maxAge}; HttpOnly; Secure; SameSite=Strict`;
}

function redirect(location, cookie) {
  const headers = new Headers({
    Location: location,
    "Cache-Control": "no-store",
  });
  if (cookie) headers.set("Set-Cookie", cookie);
  return new Response(null, { status: 303, headers });
}

function loginPage(invalid = false, status = 401) {
  const error = invalid
    ? '<p class="error" role="alert">密码不正确，请重新输入。</p>'
    : "";
  const html = `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta name="robots" content="noindex,nofollow,noarchive" />
  <title>SKT 新加坡 | 访问验证</title>
  <style>
    * { box-sizing: border-box; }
    html, body { min-height: 100%; }
    body {
      margin: 0;
      display: grid;
      place-items: center;
      padding: 24px;
      color: #17332b;
      background: #f4f7f5;
      font-family: "Microsoft YaHei", "PingFang SC", "Segoe UI", Arial, sans-serif;
    }
    main {
      width: min(100%, 420px);
      padding: 28px;
      border: 1px solid #cddbd5;
      border-top: 4px solid #146b52;
      border-radius: 8px;
      background: #fff;
      box-shadow: 0 14px 34px rgba(20, 63, 50, 0.1);
    }
    .brand { display: flex; align-items: center; gap: 10px; color: #146b52; font-size: 13px; font-weight: 900; }
    .mark { display: grid; place-items: center; width: 34px; height: 34px; border-radius: 6px; color: #fff; background: #146b52; font-size: 12px; }
    h1 { margin: 24px 0 8px; font-size: 24px; line-height: 1.25; letter-spacing: 0; }
    .note { margin: 0 0 22px; color: #667b73; font-size: 14px; line-height: 1.6; }
    label { display: block; margin-bottom: 7px; font-size: 13px; font-weight: 850; }
    input {
      width: 100%; height: 44px; border: 1px solid #b9cbc4; border-radius: 6px; padding: 0 12px;
      color: #17332b; background: #fbfdfc; font: inherit; outline: none;
    }
    input:focus { border-color: #146b52; box-shadow: 0 0 0 3px rgba(20, 107, 82, 0.12); }
    button {
      width: 100%; height: 44px; margin-top: 12px; border: 1px solid #146b52; border-radius: 6px;
      color: #fff; background: #146b52; font: inherit; font-weight: 900; cursor: pointer;
    }
    button:hover { background: #0f5944; }
    .error { margin: 12px 0 0; color: #b42318; font-size: 13px; font-weight: 800; }
    .session { margin: 14px 0 0; color: #71837c; text-align: center; font-size: 12px; }
  </style>
</head>
<body>
  <main>
    <div class="brand"><span class="mark">SKT</span><span>新加坡经营报告</span></div>
    <h1>访问验证</h1>
    <p class="note">请输入报告访问密码。</p>
    <form method="post" action="${AUTH_PATH}">
      <label for="password">访问密码</label>
      <input id="password" name="password" type="password" autocomplete="current-password" maxlength="128" required autofocus />
      <button type="submit">进入报告</button>
      ${error}
    </form>
    <p class="session">验证成功后，本设备 7 天内免重复输入。</p>
  </main>
</body>
</html>`;
  return new Response(html, {
    status,
    headers: {
      "Content-Type": "text/html; charset=utf-8",
      "Cache-Control": "no-store, private",
      "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
      "Referrer-Policy": "no-referrer",
      "X-Content-Type-Options": "nosniff",
      "X-Frame-Options": "DENY",
    },
  });
}

function jsonResponse(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store, private",
      "Referrer-Policy": "no-referrer",
      "X-Content-Type-Options": "nosniff",
    },
  });
}

async function githubApi(env, path, init = {}) {
  const token = secret(env, "GITHUB_ACTIONS_TOKEN");
  if (!token) throw new Error("刷新服务尚未完成配置");
  const response = await fetch(`${GITHUB_API_ROOT}${path}`, {
    ...init,
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
      "User-Agent": "skt-cloudflare-refresh",
      "X-GitHub-Api-Version": "2022-11-28",
      ...(init.headers || {}),
    },
  });
  const contentType = response.headers.get("Content-Type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : null;
  if (!response.ok) {
    console.error(`GitHub API request failed: ${response.status} ${path}`);
    throw new Error(`刷新服务请求失败（${response.status}）`);
  }
  return payload;
}

function workflowRunsPath() {
  return `/actions/workflows/${encodeURIComponent(GITHUB_WORKFLOW)}/runs?branch=${encodeURIComponent(GITHUB_BRANCH)}&per_page=30`;
}

async function listWorkflowRuns(env) {
  const payload = await githubApi(env, workflowRunsPath());
  return Array.isArray(payload?.workflow_runs) ? payload.workflow_runs : [];
}

function requestTokenForRun(run) {
  const title = String(run?.display_title || "");
  if (title.startsWith(RUN_TITLE_PREFIX)) {
    const suffix = title.slice(RUN_TITLE_PREFIX.length);
    if (/^[0-9a-f]{8}-[0-9a-f-]{27}$/i.test(suffix)) return suffix;
  }
  return `run:${run.id}`;
}

function isActiveRun(run) {
  return run && run.status && run.status !== "completed";
}

function publicRunStatus(run, requestId, reused = false) {
  if (!run) {
    return { request_id: requestId, status: "queued", conclusion: null, reused };
  }
  const success = run.status === "completed" && run.conclusion === "success";
  const message = run.status !== "completed"
    ? "刷新任务正在执行"
    : success
      ? "主报表已刷新并发布"
      : `刷新失败（${run.conclusion || "unknown"}），线上报表已保留原版本`;
  return {
    request_id: requestId,
    status: run.status,
    conclusion: run.conclusion || null,
    reused,
    message,
    updated_at: run.updated_at || null,
  };
}

async function dispatchRefresh(env, requestId) {
  await githubApi(env, `/actions/workflows/${encodeURIComponent(GITHUB_WORKFLOW)}/dispatches`, {
    method: "POST",
    body: JSON.stringify({
      ref: GITHUB_BRANCH,
      inputs: { request_id: requestId },
    }),
  });
}

async function handleRefreshPost(request, env, url) {
  const origin = request.headers.get("Origin") || "";
  if (origin !== url.origin) return jsonResponse({ message: "请求来源校验失败" }, 403);

  try {
    const runs = await listWorkflowRuns(env);
    const active = runs.find(isActiveRun);
    if (active) {
      const requestId = requestTokenForRun(active);
      return jsonResponse(publicRunStatus(active, requestId, true), 202);
    }

    const requestId = crypto.randomUUID();
    await dispatchRefresh(env, requestId);
    return jsonResponse(publicRunStatus(null, requestId, false), 202);
  } catch (error) {
    return jsonResponse({ message: error?.message || "刷新任务提交失败" }, 502);
  }
}

async function handleRefreshStatus(env, url) {
  const requestId = String(url.searchParams.get("id") || "").trim();
  const isRunId = /^run:\d+$/.test(requestId);
  const isRequestId = /^[0-9a-f]{8}-[0-9a-f-]{27}$/i.test(requestId);
  if (!isRunId && !isRequestId) return jsonResponse({ message: "任务编号无效" }, 400);

  try {
    let run = null;
    if (isRunId) {
      run = await githubApi(env, `/actions/runs/${requestId.slice(4)}`);
    } else {
      const runs = await listWorkflowRuns(env);
      run = runs.find(item => item.display_title === `${RUN_TITLE_PREFIX}${requestId}`) || null;
    }
    return jsonResponse(publicRunStatus(run, requestId, isRunId));
  } catch (error) {
    return jsonResponse({ message: error?.message || "刷新状态查询失败" }, 502);
  }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const passwordSalt = secret(env, "REPORT_PASSWORD_SALT_HEX");
    const passwordHash = secret(env, "REPORT_PASSWORD_HASH_HEX");
    const sessionToken = secret(env, "REPORT_SESSION_TOKEN");
    if (!passwordSalt || !passwordHash || !sessionToken) {
      return new Response("Report authentication is not configured", { status: 503 });
    }

    if (url.pathname === LOGOUT_PATH) {
      return redirect("/", sessionCookie(env, 0, "deleted"));
    }

    if (url.pathname === AUTH_PATH && request.method === "POST") {
      let password = "";
      try {
        const form = await request.formData();
        password = String(form.get("password") || "").slice(0, 128);
      } catch (error) {
        return loginPage(true);
      }
      const derivedHash = await derivePasswordHash(password, passwordSalt);
      if (timingSafeEqual(derivedHash, passwordHash)) {
        return redirect("/", sessionCookie(env));
      }
      return loginPage(true);
    }

    if (!isAuthorized(request, env)) {
      return loginPage(false, url.pathname === AUTH_PATH ? 200 : 401);
    }

    if (url.pathname === REFRESH_PATH && request.method === "POST") {
      return handleRefreshPost(request, env, url);
    }
    if (url.pathname === REFRESH_PATH && request.method === "GET") {
      return handleRefreshStatus(env, url);
    }
    if (url.pathname === REFRESH_PATH) {
      return jsonResponse({ message: "请求方法不支持" }, 405);
    }
    if (url.pathname === AUTH_PATH) return redirect("/");
    if (!env?.ASSETS?.fetch) return new Response("Asset binding unavailable", { status: 500 });
    return env.ASSETS.fetch(request);
  },
};
