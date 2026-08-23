(function () {
  "use strict";

  var ROWS = 5;
  var PAGE_SIZE = 25;
  var COUNT_CAP = 5000;
  var HIGH = "￿";
  var iconUrls = [];

  var db = null;
  var dbReady = null;
  var apiLevels = {};
  var maxTid = 0;
  var totalTitles = 0;
  var totalApps = 0;
  var state = {};

  var el = {
    status: document.getElementById("status"),
    results: document.getElementById("results"),
    q: document.getElementById("q"),
    pkg: document.getElementById("pkg"),
    unique: document.getElementById("unique"),
    minsdk: document.getElementById("minsdk"),
    maxsdk: document.getElementById("maxsdk"),
    device: document.getElementById("device"),
    form: document.getElementById("filters"),
    random: document.getElementById("random")
  };

  function readState() {
    var p = new URLSearchParams(location.search);
    return {
      q: p.get("q") || "",
      pkg: p.get("pkg") || "",
      unique: p.get("unique") !== "0",
      minsdk: p.get("minsdk") || "",
      maxsdk: p.get("maxsdk") || "",
      device: p.get("device") || "",
      page: Math.max(1, parseInt(p.get("page"), 10) || 1),
      tid: p.get("tid") ? parseInt(p.get("tid"), 10) : null
    };
  }

  function writeState(next, replace) {
    var p = new URLSearchParams();
    if (next.q) p.set("q", next.q);
    if (next.pkg) p.set("pkg", next.pkg);
    if (!next.unique) p.set("unique", "0");
    if (next.minsdk) p.set("minsdk", next.minsdk);
    if (next.maxsdk) p.set("maxsdk", next.maxsdk);
    if (next.device) p.set("device", next.device);
    if (next.page > 1) p.set("page", String(next.page));
    if (next.tid) p.set("tid", String(next.tid));
    var url = location.pathname + (p.toString() ? "?" + p : "");
    history[replace ? "replaceState" : "pushState"](null, "", url);
  }

  function syncForm() {
    el.q.value = state.q;
    el.pkg.value = state.pkg;
    el.unique.checked = state.unique;
    el.minsdk.value = state.minsdk;
    el.maxsdk.value = state.maxsdk;
    el.device.value = state.device;
  }

  function terms(text) {
    return (text || "")
      .toLowerCase()
      .split(/[^0-9a-z]+/)
      .filter(function (t) { return t.length >= 2; })
      .slice(0, 6);
  }

  function buildFilter(alias) {
    var where = [];
    var params = {};
    terms(state.q).forEach(function (t, i) {
      where.push(alias + ".tid IN (SELECT tid FROM tokens WHERE tok >= :t" + i +
                 " AND tok < :u" + i + ")");
      params[":t" + i] = t;
      params[":u" + i] = t + HIGH;
    });
    if (state.pkg) {
      var pk = state.pkg.toLowerCase();
      where.push(alias + ".tid IN (SELECT tid FROM groups WHERE pkg_lc >= :pk" +
                 " AND pkg_lc < :pku)");
      params[":pk"] = pk;
      params[":pku"] = pk + HIGH;
    }
    if (state.minsdk) {
      where.push(alias + ".min_sdk >= :mn");
      params[":mn"] = parseInt(state.minsdk, 10);
    }
    if (state.maxsdk) {
      where.push(alias + ".min_sdk <= :mx");
      params[":mx"] = parseInt(state.maxsdk, 10);
    }
    if (state.device) {
      where.push("(',' || " + alias + ".dev || ',') LIKE :dev");
      params[":dev"] = "%," + state.device + ",%";
    }
    return { sql: where.length ? " WHERE " + where.join(" AND ") : "", params: params };
  }

  function sdkLabel(level) {
    if (level === null || level === undefined) return "?";
    return apiLevels[level] ? apiLevels[level] + " (API " + level + ")" : "API " + level;
  }

  function versionText(a) {
    var name = a.ver === null || a.ver === undefined ? "" : String(a.ver).trim();
    var code = a.vcode;
    var hasCode = code !== null && code !== undefined;
    if (name && hasCode && name !== String(code)) return name + " (" + code + ")";
    if (name) return name;
    if (hasCode) return "build " + code;
    return "?";
  }

  function fmtSize(bytes) {
    if (!bytes) return "?";
    var mb = bytes / 1048576;
    return mb >= 1024 ? (mb / 1024).toFixed(1) + "GB" : mb.toFixed(1) + "MB";
  }

  function downloadUrl(item, filename) {
    return "https://archive.org/download/" + encodeURIComponent(item) + "/" +
      filename.split("/").map(encodeURIComponent).join("/");
  }

  function columnCount() {
    var style = getComputedStyle(el.results).getPropertyValue("grid-template-columns");
    return Math.max(1, (style || "").trim().split(/\s+/).filter(function (t) {
      return t && t !== "none";
    }).length);
  }

  function syncPageSize() {
    var next = columnCount() * ROWS;
    if (next !== PAGE_SIZE) { PAGE_SIZE = next; return true; }
    return false;
  }

  function releaseIcons() {
    iconUrls.forEach(URL.revokeObjectURL);
    iconUrls = [];
  }

  async function loadIcons(tids) {
    await ensureDb();
    var map = new Map();
    var unique = Array.from(new Set(tids));
    if (!unique.length) return map;

    var rows = await db.query(
      "SELECT tid, webp FROM icons WHERE tid IN (" + unique.join(",") + ")");
    var decoding = [];
    rows.forEach(function (r) {
      if (!r.webp) return;
      var url = URL.createObjectURL(new Blob([r.webp], { type: "image/webp" }));
      iconUrls.push(url);
      map.set(r.tid, url);
      var pre = new Image();
      pre.src = url;
      decoding.push(pre.decode().catch(function () {}));
    });
    await Promise.all(decoding);
    return map;
  }

  function iconFor(label, url) {
    var box = document.createElement("div");
    box.className = "icon";
    if (url) {
      box.classList.add("has-img");
      var img = document.createElement("img");
      img.src = url;
      img.alt = "";
      box.appendChild(img);
      return box;
    }
    var letters = (label || "?").replace(/[^A-Za-z0-9]/g, " ").trim().split(/\s+/);
    box.textContent = ((letters[0] || "?").charAt(0) +
      (letters[1] ? letters[1].charAt(0) : "")).toUpperCase();
    return box;
  }

  function row(labelText, valueNode) {
    var div = document.createElement("div");
    div.className = "row";
    div.appendChild(document.createTextNode(labelText + " "));
    var b = document.createElement("b");
    if (typeof valueNode === "string") b.textContent = valueNode;
    else b.appendChild(valueNode);
    div.appendChild(b);
    return div;
  }

  function openTitle(tid) {
    state.tid = tid;
    writeState(state);
    render();
    window.scrollTo(0, 0);
  }

  function titleCard(t, iconUrl) {
    var card = document.createElement("div");
    card.className = "card";
    card.appendChild(iconFor(t.label, iconUrl));

    var body = document.createElement("div");
    body.className = "body";

    var name = document.createElement("div");
    name.className = "name";
    name.textContent = t.label;
    name.title = t.label;
    body.appendChild(name);

    body.appendChild(row("Package:", t.editions > 1
      ? t.editions + " package IDs"
      : (t.pkg || "(unidentified)")));
    body.appendChild(row("Device:", t.dev || "phone"));
    body.appendChild(row("Minimum OS:", sdkLabel(t.min_sdk)));

    var more = document.createElement("a");
    more.className = "more";
    more.href = "?tid=" + t.tid;
    more.textContent = "Show all";
    more.addEventListener("click", function (e) {
      e.preventDefault();
      openTitle(t.tid);
    });
    body.appendChild(more);

    card.appendChild(body);
    return card;
  }

  function appCard(a, label, iconUrl, showPackage) {
    var card = document.createElement("div");
    card.className = "card";
    card.appendChild(iconFor(label, iconUrl));

    var body = document.createElement("div");
    body.className = "body";

    var name = document.createElement("div");
    name.className = "name";
    name.textContent = label;
    body.appendChild(name);

    if (showPackage) body.appendChild(row("Package:", a.pkg || "(unidentified)"));
    body.appendChild(row("Version:", versionText(a) + " – " + fmtSize(a.size)));
    body.appendChild(row("Device:", a.dev || "phone"));
    body.appendChild(row("Minimum OS:", sdkLabel(a.min_sdk)));

    var link = document.createElement("a");
    link.href = downloadUrl(a.item, a.fn);
    link.textContent = a.fn.split("/").pop();
    link.title = a.item + "/" + a.fn;
    link.rel = "noopener";
    body.appendChild(row("Link:", link));

    if (a.md5) {
      var md5 = document.createElement("div");
      md5.className = "row";
      md5.textContent = "MD5: " + a.md5;
      body.appendChild(md5);
    }

    card.appendChild(body);
    return card;
  }

  function setPager(page, pages, capped) {
    [["pager", "page", "prev", "next"], ["pager2", "page2", "prev2", "next2"]]
      .forEach(function (ids) {
        var nav = document.getElementById(ids[0]);
        nav.hidden = pages <= 1;
        document.getElementById(ids[1]).textContent =
          page + " / " + pages + (capped ? "+" : "");
        var prev = document.getElementById(ids[2]);
        var next = document.getElementById(ids[3]);
        prev.disabled = page <= 1;
        next.disabled = page >= pages;
        prev.onclick = function () { goPage(page - 1); };
        next.onclick = function () { goPage(page + 1); };
      });
  }

  function goPage(n) {
    state.page = n;
    writeState(state);
    render();
    window.scrollTo(0, 0);
  }

  async function render() {
    releaseIcons();
    el.results.textContent = "";
    syncForm();
    syncPageSize();

    if (state.tid) await renderTitle();
    else await renderList();
  }

  async function renderTitle() {
    await ensureDb();
    var t = (await db.query("SELECT * FROM titles WHERE tid = :t",
                            { ":t": state.tid }))[0];
    if (!t) { el.status.textContent = "Not found"; return; }

    var rows = await db.query(
      "SELECT a.*, g.pkg AS pkg FROM apps a JOIN groups g USING(gid) " +
      "WHERE g.tid = :t ORDER BY g.pkg, a.vcode DESC, a.vsort DESC, a.size DESC",
      { ":t": state.tid });

    el.status.textContent = "";
    el.status.appendChild(document.createTextNode(
      "Results: " + rows.length +
      (t.editions > 1 ? " across " + t.editions + " package IDs" : "") + " — "));
    var back = document.createElement("a");
    back.href = "#";
    back.textContent = "Go to: previous search";
    back.addEventListener("click", function (e) {
      e.preventDefault();
      state.tid = null;
      writeState(state);
      render();
    });
    el.status.appendChild(back);

    setPager(1, 1, false);
    var icons = await loadIcons([t.tid]);
    rows.forEach(function (a) {
      el.results.appendChild(appCard(a, t.label, icons.get(t.tid), true));
    });
  }

  var pageCache = new Map();
  var CACHE_MAX = 8;

  function filterKey() {
    return [state.q, state.pkg, state.unique ? 1 : 0, state.minsdk,
            state.maxsdk, state.device, PAGE_SIZE].join("");
  }

  function cacheGet(page) {
    return pageCache.get(filterKey() + "" + page);
  }

  function cacheSet(page, value) {
    var key = filterKey() + "" + page;
    pageCache.set(key, value);
    while (pageCache.size > CACHE_MAX) {
      pageCache.delete(pageCache.keys().next().value);
    }
  }

  async function ensureDb() {
    if (db) return;
    if (dbReady) await dbReady;
  }

  async function fetchPage(page) {
    await ensureDb();
    var hit = cacheGet(page);
    if (hit) return hit;

    var f = buildFilter("t");
    var from = state.unique
      ? " FROM titles t"
      : " FROM apps a JOIN groups g USING(gid) JOIN titles t ON t.tid = g.tid";
    var select = state.unique
      ? "SELECT t.*, (SELECT pkg FROM groups WHERE tid = t.tid LIMIT 1) AS pkg"
      : "SELECT a.*, g.pkg AS pkg, t.label AS tlabel, t.tid AS tid";
    var order = state.unique
      ? " ORDER BY t.identified DESC, t.label COLLATE NOCASE, t.tid"
      : " ORDER BY t.identified DESC, t.label COLLATE NOCASE, a.vcode DESC, a.vsort DESC";

    var total;
    if (!f.sql) {
      total = state.unique ? totalTitles : totalApps;
    } else {
      var counted = await db.query(
        "SELECT COUNT(*) AS n FROM (SELECT 1" + from + f.sql +
        " LIMIT " + COUNT_CAP + ")", f.params);
      total = counted[0].n;
    }
    var pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
    var clamped = Math.min(Math.max(1, page), pages);

    var rows = await db.query(
      select + from + f.sql + order + " LIMIT :lim OFFSET :off",
      Object.assign({ ":lim": PAGE_SIZE, ":off": (clamped - 1) * PAGE_SIZE }, f.params));

    var result = { total: total, capped: !!f.sql && total >= COUNT_CAP,
                   pages: pages, rows: rows };
    cacheSet(clamped, result);
    return result;
  }

  var prefetchTimer = null;

  function schedulePrefetch(page, pages) {
    clearTimeout(prefetchTimer);
    prefetchTimer = setTimeout(function () {
      [page + 1, page - 1].forEach(function (p) {
        if (p < 1 || p > pages || cacheGet(p)) return;
        fetchPage(p).then(function (r) {
          var tids = r.rows.map(function (x) { return x.tid; });
          var unique = Array.from(new Set(tids));
          if (unique.length) {
            db.query("SELECT tid, webp FROM icons WHERE tid IN (" +
                     unique.join(",") + ")").catch(function () {});
          }
        }).catch(function () {});
      });
    }, 150);
  }

  async function renderList() {
    el.status.textContent = "Searching…";
    var data = await fetchPage(state.page);
    if (state.page > data.pages) state.page = data.pages;

    if (!data.rows.length) {
      el.results.innerHTML = '<p class="empty">No matches.</p>';
      el.status.textContent = "Results: 0";
      setPager(1, 1, false);
      return;
    }

    var icons = await loadIcons(data.rows.map(function (r) { return r.tid; }));
    var frag = document.createDocumentFragment();
    if (state.unique) {
      data.rows.forEach(function (t) {
        frag.appendChild(titleCard(t, icons.get(t.tid)));
      });
    } else {
      data.rows.forEach(function (a) {
        frag.appendChild(appCard(a, a.tlabel || a.fn, icons.get(a.tid), true));
      });
    }
    el.results.appendChild(frag);

    el.status.textContent = "Results: " + data.total + (data.capped ? "+" : "");
    setPager(state.page, data.pages, data.capped);
    schedulePrefetch(state.page, data.pages);
  }

  function fillSdkOptions() {
    var levels = Object.keys(apiLevels).map(Number).sort(function (a, b) { return a - b; });
    [el.minsdk, el.maxsdk].forEach(function (sel) {
      var any = document.createElement("option");
      any.value = "";
      any.textContent = "Any";
      sel.appendChild(any);
      levels.forEach(function (lv) {
        var o = document.createElement("option");
        o.value = String(lv);
        o.textContent = apiLevels[lv] + " (API " + lv + ")";
        sel.appendChild(o);
      });
    });
  }

  function isDefaultView() {
    return !state.tid && state.unique && !state.q && !state.pkg &&
           !state.minsdk && !state.maxsdk && !state.device && state.page === 1;
  }

  async function renderFirstPaint() {
    var res = await fetch("data/first.json", { cache: "no-cache" });
    if (!res.ok) throw new Error("first.json " + res.status);
    var data = await res.json();
    if (data.rows.length < PAGE_SIZE) throw new Error("baked page too small");
    var rows = data.rows.slice(0, PAGE_SIZE);

    var decoding = [];
    rows.forEach(function (r) {
      if (!r.icon) return;
      var img = new Image();
      img.src = r.icon;
      decoding.push(img.decode().catch(function () {}));
    });
    await Promise.all(decoding);

    var frag = document.createDocumentFragment();
    rows.forEach(function (t) { frag.appendChild(titleCard(t, r_icon(t))); });
    el.results.appendChild(frag);
    el.status.textContent = "Results: " + data.total;
    setPager(1, Math.max(1, Math.ceil(data.total / PAGE_SIZE)), false);
  }

  function r_icon(t) { return t.icon || undefined; }

  async function boot() {
    var painted = false;
    state = readState();
    syncPageSize();
    if (isDefaultView()) {
      try {
        await renderFirstPaint();
        painted = true;
      } catch (err) {
        painted = false;
      }
    }

    dbReady = (async function () {
    var worker;
    try {
      var abs = function (p) { return new URL(p, location.href).toString(); };
      worker = await createDbWorker(
        [{
          from: "inline",
          config: {
            serverMode: "full",
            url: abs("data/apk.sqlite.png"),
            requestChunkSize: 65536
          }
        }],
        abs("vendor/sqlite.worker.js"),
        abs("vendor/sql-wasm.wasm")
      );
    } catch (err) {
      el.status.textContent = "Could not load the index: " + err;
      throw err;
    }
    db = worker.db;

    var meta = {};
    (await db.query("SELECT key, value FROM meta")).forEach(function (r) {
      meta[r.key] = r.value;
    });
    apiLevels = JSON.parse(meta.api_levels || "{}");
    maxTid = Number(meta.max_tid || 0);
    totalTitles = Number(meta.titles || 0);
    totalApps = Number(meta.apps || 0);
    fillSdkOptions();
    })();
    await dbReady;

    state = readState();
    if (painted && isDefaultView()) {
      schedulePrefetch(1, Math.max(1, Math.ceil(totalTitles / PAGE_SIZE)));
      return;
    }
    await render();
  }

  el.form.addEventListener("submit", function (e) {
    e.preventDefault();
    state.q = el.q.value.trim();
    state.pkg = el.pkg.value.trim();
    state.unique = el.unique.checked;
    state.minsdk = el.minsdk.value;
    state.maxsdk = el.maxsdk.value;
    state.device = el.device.value;
    state.page = 1;
    state.tid = null;
    writeState(state);
    render();
  });

  el.random.addEventListener("click", async function () {
    await ensureDb();
    if (!db || !maxTid) return;
    var r = [];
    for (var attempt = 0; attempt < 4 && !r.length; attempt++) {
      var pick = 1 + Math.floor(Math.random() * maxTid);
      r = await db.query(
        "SELECT tid FROM titles WHERE tid >= :r AND identified = 1 " +
        "ORDER BY tid LIMIT 1", { ":r": pick });
    }
    if (!r.length) {
      r = await db.query(
        "SELECT tid FROM titles WHERE identified = 1 ORDER BY tid LIMIT 1");
    }
    if (!r.length) return;
    state = readState();
    openTitle(r[0].tid);
  });

  window.addEventListener("popstate", function () {
    state = readState();
    render();
  });

  var resizeTimer = null;
  window.addEventListener("resize", function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () {
      if (db && syncPageSize()) { state.page = 1; render(); }
    }, 250);
  });

  boot();
})();
