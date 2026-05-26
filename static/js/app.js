/* ====== 全局状态 ====== */
const API = "/api";
let token = localStorage.getItem("lottery_token") || null;
let currentLottery = "ssq";
let currentTab = "draws";
let drawsPage = 1, searchPage = 1;
let drawsTotal = 0, searchTotal = 0;
const PAGE_SIZE = 20;

/* ====== API 封装 ====== */
async function api(method, url, body) {
  const opt = { method, headers: { "Content-Type": "application/json" } };
  if (token) opt.headers["Authorization"] = `Bearer ${token}`;
  if (body) opt.body = JSON.stringify(body);
  const res = await fetch(API + url, opt);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    let msg = err.detail;
    if (Array.isArray(msg)) msg = msg.map(d => d.msg).filter(Boolean).join("; ");
    if (typeof msg === "object") msg = JSON.stringify(msg);
    throw new Error(msg || `HTTP ${res.status}`);
  }
  return res.json();
}

/* ====== 工具 ====== */
function $(id) { return document.getElementById(id); }

function qs(sel) { return document.querySelector(sel); }

function qsa(sel) { return document.querySelectorAll(sel); }

function esc(s) { const d = document.createElement("div"); d.textContent = s; return d.innerHTML; }

function renderBalls(nums, cls = "red") {
  return nums.map(n => `<span class="ball ${cls}">${String(n).padStart(2, "0")}</span>`).join("");
}

function formatDate(d) {
  if (!d) return "";
  const m = d.match(/^\d{4}-\d{2}-\d{2}/);
  return m ? m[0] : d;
}

function showMsg(id, text, isError = true) {
  const el = $(id);
  if (!el) return;
  el.textContent = text;
  el.style.color = isError ? "#e74c3c" : "#27ae60";
}

// 彩种切换时的标签文本
const LOTTERY_LABELS = {
  ssq: { main: "红球", extra: "蓝球", mainCount: 6, extraCount: 1 },
  dlt: { main: "前区", extra: "后区", mainCount: 5, extraCount: 2 },
  hk6: { main: "搅珠", extra: "特别", mainCount: 6, extraCount: 1 },
};

/* ====== 页面导航 ====== */
function showPage(page) {
  qsa(".page").forEach(p => p.classList.remove("active"));
  // 彩种页面共用 id="page-lottery"，其他页面 id="page-{name}"
  const mapping = {
    "lottery-ssq": "page-lottery",
    "lottery-dlt": "page-lottery",
    "lottery-hk6": "page-lottery",
    "calculator": "page-calculator",
    "compare": "page-compare",
    "favorites": "page-favorites",
    "home": "page-home",
  };
  const target = $(mapping[page] || "page-" + page);
  if (!target) return;
  target.classList.add("active");

  if (page === "home") loadHome();
  else if (page === "lottery-ssq") loadLottery("ssq");
  else if (page === "lottery-dlt") loadLottery("dlt");
  else if (page === "lottery-hk6") loadLottery("hk6");
  else if (page === "favorites") loadFavorites();
  else if (page === "calculator") {
    document.title = "奖金计算器 - 彩票数据平台";
  } else if (page === "compare") {
    document.title = "号码对比 - 彩票数据平台";
  }
}

document.addEventListener("click", e => {
  const link = e.target.closest("[data-page]");
  if (link) { e.preventDefault(); showPage(link.dataset.page); }
});

/* ====== Tab 切换 ====== */
document.addEventListener("click", e => {
  const tab = e.target.closest(".tab");
  if (!tab) return;
  const container = tab.closest(".page");
  if (!container) return;
  qsa(".tab", container).forEach(t => t.classList.remove("active"));
  qsa(".tab-content", container).forEach(t => t.style.display = "none");
  tab.classList.add("active");
  currentTab = tab.dataset.tab;
  const content = $(`tab-${currentTab}`);
  if (content) content.style.display = "block";

  if (currentTab === "hotcold") doHotCold();
  if (currentTab === "predict") doPredict();
  if (currentTab === "search") { /* 等待用户输入 */ }
});

/* ====== 首页 ====== */
async function loadHome() {
  document.title = "彩票数据平台";
  const container = $("home-cards");
  container.innerHTML = "<p>加载中...</p>";
  try {
    const lotteries = await api("GET", "/lotteries");
    container.innerHTML = lotteries.map(l => {
      const lat = l.latest;
      return `<div class="card lottery-card ${l.code}">
        <h3>${esc(l.name)}</h3>
        ${lat ? `
          <div class="draw-info">第 ${esc(lat.draw_number)} 期 · ${formatDate(lat.draw_date)}</div>
          <div class="numbers-row">${renderBalls(lat.numbers, "red")} ${renderBalls(lat.extra_numbers, l.code === "ssq" ? "blue" : "gold")}</div>
          <div class="draw-meta">${lat.prize_pool ? "奖池: ¥" + (+lat.prize_pool).toLocaleString() : ""}</div>
        ` : "<p>暂无数据（首次加载需稍候，后台正在抓取）</p>"}
        <a href="#" data-page="lottery-${l.code}">查看详情 →</a>
      </div>`;
    }).join("");
  } catch (e) {
    container.innerHTML = `<p class="msg">加载失败: ${esc(e.message)}</p>`;
  }
}

/* ====== 彩种详情 ====== */
async function loadLottery(lottery) {
  currentLottery = lottery;
  drawsPage = 1;
  searchPage = 1;
  const cfg = LOTTERY_LABELS[lottery];
  const name = lottery === "ssq" ? "双色球" : (lottery === "dlt" ? "大乐透" : "香港六合彩");
  document.title = name + " - 彩票数据平台";
  $("lottery-title").textContent = name;
  // 更新奖金计算器标签
  const ml = $("calc-main-label"), el = $("calc-extra-label");
  if (ml) ml.textContent = cfg.main + "号码";
  if (el) el.textContent = cfg.extra + "号码";
  // 切换到 draws tab
  qsa("#page-lottery .tab").forEach(t => t.classList.remove("active"));
  qsa("#page-lottery .tab-content").forEach(t => t.style.display = "none");
  qs("#page-lottery .tab[data-tab='draws']").classList.add("active");
  currentTab = "draws";
  $("tab-draws").style.display = "block";
  loadDrawsList(1);
}

async function loadDrawsList(page) {
  drawsPage = page;
  const container = $("draws-table-container");
  container.innerHTML = "<p>加载中...</p>";
  try {
    const data = await api("GET", `/draws/${currentLottery}/latest?count=${PAGE_SIZE}`);
    if (!data || data.length === 0) {
      container.innerHTML = "<p>暂无数据，后台正在抓取...</p>";
      return;
    }
    const cfg = LOTTERY_LABELS[currentLottery];
    container.innerHTML = `<div class="table-wrap"><table>
      <thead><tr><th>期号</th><th>日期</th><th>${cfg.main}号码</th><th>${cfg.extra}号码</th><th>操作</th></tr></thead>
      <tbody>${data.map(d => `<tr>
        <td><strong>${esc(d.draw_number)}</strong></td>
        <td>${formatDate(d.draw_date)}</td>
        <td>${renderBalls(d.numbers, "red")}</td>
        <td>${renderBalls(d.extra_numbers, currentLottery === "ssq" ? "blue" : "gold")}</td>
        <td><button class="btn-small btn-secondary" onclick="quickFav('${currentLottery}','${esc(d.draw_number)}')">收藏</button></td>
      </tr>`).join("")}</tbody>
    </table></div>`;
  } catch (e) {
    container.innerHTML = `<p class="msg">加载失败: ${esc(e.message)}</p>`;
  }
}

/* ====== 号码搜索 ====== */
async function doNumberSearch(page) {
  searchPage = page || 1;
  const input = $("search-input");
  const numbers = input.value.trim();
  if (!numbers) { showMsg("search-results", "请输入号码"); return; }
  const matchType = $("search-match").value;
  const container = $("search-results");
  container.innerHTML = "<p>搜索中...</p>";
  try {
    const data = await api("GET",
      `/draws/${currentLottery}/search?numbers=${encodeURIComponent(numbers)}&match_type=${matchType}&page=${searchPage}&page_size=${PAGE_SIZE}`);
    if (data.total === 0) {
      container.innerHTML = '<p>未找到匹配结果</p>';
      $("search-pagination").innerHTML = "";
      return;
    }
    const cfg = LOTTERY_LABELS[currentLottery];
    container.innerHTML = `<p style="margin-bottom:8px">共找到 <strong>${data.total}</strong> 期匹配</p>
      <div class="table-wrap"><table>
      <thead><tr><th>期号</th><th>日期</th><th>完整号码</th><th>匹配个数</th><th>操作</th></tr></thead>
      <tbody>${data.results.map(d => `<tr>
        <td><strong>${esc(d.draw_number)}</strong></td>
        <td>${formatDate(d.draw_date)}</td>
        <td>${renderBalls(d.numbers, "red")} ${renderBalls(d.extra_numbers, currentLottery === "ssq" ? "blue" : "gold")}</td>
        <td><strong>${d.match_count}</strong></td>
        <td><button class="btn-small btn-secondary" onclick="quickFav('${currentLottery}','${esc(d.draw_number)}')">收藏</button></td>
      </tr>`).join("")}</tbody>
    </table></div>`;
    searchTotal = data.total;
    renderSearchPagination();
  } catch (e) {
    container.innerHTML = `<p class="msg">${esc(e.message)}</p>`;
  }
}

function renderSearchPagination() {
  const totalPages = Math.ceil(searchTotal / PAGE_SIZE);
  let html = "";
  for (let i = 1; i <= Math.min(totalPages, 20); i++) {
    html += `<button class="${i === searchPage ? 'active' : ''}" onclick="doNumberSearch(${i})">${i}</button>`;
  }
  $("search-pagination").innerHTML = html;
}

/* ====== 冷热号统计 ====== */
async function doHotCold() {
  const range = $("hotcold-range").value;
  const container = $("hotcold-tables");
  container.innerHTML = "<p>统计中...</p>";
  try {
    const data = await api("GET", `/analysis/${currentLottery}/hot-cold?range=${range}`);
    if (!data.hot || data.hot.length === 0) {
      container.innerHTML = "<p>暂无数据</p>";
      return;
    }
    // ECharts 柱状图
    const chartDom = $("hotcold-chart");
    chartDom.style.height = "400px";
    const myChart = echarts.init(chartDom);
    const hot = data.hot.slice(0, 15);
    myChart.setOption({
      title: { text: `热号 TOP15（近 ${range} 期）`, left: "center", textStyle: { fontSize: 14 } },
      tooltip: { trigger: "axis" },
      xAxis: { type: "category", data: hot.map(h => h.number), axisLabel: { fontSize: 12 } },
      yAxis: { type: "value" },
      series: [{ type: "bar", data: hot.map(h => h.count),
        itemStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: "#e74c3c" }, { offset: 1, color: "#f1948a" }]) },
        label: { show: true, position: "top", fontSize: 11 },
      }],
      grid: { left: "5%", right: "5%", top: "15%", bottom: "10%" },
    });

    // 冷热号表格
    const coldText = data.cold.slice(0, 15).map(h =>
      `<span class="ball red" style="width:auto;border-radius:4px;padding:2px 8px;font-size:12px;display:inline-block">${h.number}: ${h.count}次</span>`
    ).join(" ");
    container.innerHTML = `
      <div class="card"><h4 style="margin-bottom:8px">热门号码</h4>
        ${data.hot.slice(0, 15).map(h =>
          `<span class="ball red" style="width:auto;border-radius:4px;padding:2px 10px;font-size:13px;display:inline-block;margin:3px">${h.number} <small style="opacity:.8">(${h.count}次/${h.rate}%)</small></span>`
        ).join("")}
      </div>
      <div class="card"><h4 style="margin-bottom:8px">冷门号码</h4>
        ${data.cold.slice(0, 15).map(h =>
          `<span class="ball blue" style="width:auto;border-radius:4px;padding:2px 10px;font-size:13px;display:inline-block;margin:3px">${h.number} <small style="opacity:.8">(仅${h.count}次)</small></span>`
        ).join("")}
      </div>`;
  } catch (e) {
    container.innerHTML = `<p class="msg">${esc(e.message)}</p>`;
  }
}

/* ====== 号码预测 ====== */
async function doPredict() {
  const range = $("predict-range").value;
  const count = $("predict-count").value;
  // 收集选中的方法
  const methods = ["meth-hot","meth-cold","meth-mix","meth-random",
    "meth-whot","meth-oe","meth-bs","meth-sum","meth-markov","meth-smart"]
    .filter(id => $(id).checked)
    .map(id => id.replace("meth-",""))
    .join(",");
  if (!methods) { showMsg("predict-result", "请至少选择一种预测方法"); return; }

  const dan = $("predict-dan").value.trim();
  const tuo = $("predict-tuo").value.trim();

  const container = $("predict-result");
  container.innerHTML = "<p>分析中...</p>";
  try {
    const danParam = dan ? `&dan=${encodeURIComponent(dan)}` : "";
    const tuoParam = tuo ? `&tuo=${encodeURIComponent(tuo)}` : "";
    const data = await api("GET",
      `/analysis/${currentLottery}/predict?range=${range}&methods=${methods}&count=${count}${danParam}${tuoParam}`);
    const cfg = LOTTERY_LABELS[currentLottery];
    const methodColors = {hot:"#e74c3c", cold:"#3498db", mix:"#27ae60", random:"#f39c12",
      whot:"#e67e22", oe:"#9b59b6", bs:"#1abc9c", sum:"#34495e", markov:"#e84393", smart:"#2c3e50"};

    // 每种方法的结果卡片
    let html = `<div class="disclaimer" style="text-align:left;margin:0 0 12px;padding:8px 12px;background:#fff3cd;border-radius:6px;font-size:13px;color:#856404">
      ${esc(data.disclaimer)}</div>
      <p style="margin-bottom:12px;font-size:14px;color:#555">基于 <strong>${data.total_periods}</strong> 期数据分析 · 共 <strong>${data.count}</strong> 注</p>`;

    for (const [key, r] of Object.entries(data.results)) {
      const color = methodColors[key] || "#666";
      html += `<div class="card" style="margin-bottom:12px;border-left:4px solid ${color}">
        <h4 style="margin-bottom:4px">${esc(r.name)}</h4>
        <p style="font-size:13px;color:#888;margin-bottom:8px">${esc(r.description)}</p>`;
      r.bets.forEach((bet, i) => {
        html += `<div class="numbers-row" style="margin:4px 0">
          <span style="font-size:13px;color:#999;min-width:30px">#${i+1}</span>
          ${renderBalls(bet.main_numbers, "red")}
          ${currentLottery !== "hk6" ? renderBalls(bet.extra_numbers, cfg.extra === "蓝球" ? "blue" : "gold") : ""}
        </div>`;
      });
      html += `</div>`;
    }

    // 参考统计
    html += `<div class="card-grid" style="grid-template-columns:1fr 1fr;margin-top:8px">
      <div class="card">
        <h4 style="margin-bottom:8px">热号 TOP10</h4>
        <div style="font-size:13px">${data.hot_main.slice(0, 10).map(h =>
          `<span style="display:inline-block;margin:2px 4px">${h.number} <small style="color:#e74c3c">(${h.frequency}次)</small></span>`
        ).join("")}</div>
      </div>
      <div class="card">
        <h4 style="margin-bottom:8px">遗漏最久的号码</h4>
        <div style="font-size:13px">${data.cold_main.slice(0, 5).map(h =>
          `<span style="display:inline-block;margin:2px 4px">${h.number} <small style="color:#888">(遗漏${h.missing}期)</small></span>`
        ).join("")}</div>
      </div>
    </div>`;

    container.innerHTML = html;
  } catch (e) {
    container.innerHTML = `<p class="msg">${esc(e.message)}</p>`;
  }
}

/* ====== 奖金计算器 ====== */
async function doPrizeCalc() {
  const lottery = $("calc-lottery").value;
  const drawNumber = $("calc-draw").value.trim();
  const numbers = $("calc-numbers").value.trim().split(",").map(s => parseInt(s.trim())).filter(n => !isNaN(n));
  const extra = $("calc-extra").value.trim().split(",").map(s => parseInt(s.trim())).filter(n => !isNaN(n));
  if (!numbers.length) { showMsg("calc-result", "请输入号码"); return; }
  if (!drawNumber) { showMsg("calc-result", "请填写期号"); return; }
  const container = $("calc-result");
  container.style.display = "block";
  container.innerHTML = "<p>计算中...</p>";
  try {
    const data = await api("POST", "/analysis/prize-calc", { lottery, draw_number: drawNumber, numbers, extra_numbers: extra });
    const isWin = data.prize_level !== "未中奖";
    container.innerHTML = `<div class="result-card">
      <p>第 ${esc(data.draw_number)} 期开奖号码：${renderBalls(data.draw_numbers, "red")} ${renderBalls(data.draw_extra, lottery === "ssq" ? "blue" : "gold")}</p>
      <p style="margin-top:8px">你的号码：${renderBalls(data.user_numbers, "red")} ${renderBalls(data.user_extra, lottery === "ssq" ? "blue" : "gold")}</p>
      <div style="margin-top:12px;padding:12px;background:${isWin ? "#d4edda" : "#f8f9fa"};border-radius:8px">
        <p>命中：<strong>${data.match_detail.main_hit}</strong> 个${data.main_label || (lottery === "ssq" ? "红球" : (lottery === "dlt" ? "前区" : "搅珠号码"))} · <strong>${data.match_detail.extra_hit}</strong> 个${data.extra_label || (lottery === "ssq" ? "蓝球" : (lottery === "dlt" ? "后区" : "特别号码"))}</p>
        <div class="prize ${isWin ? "win" : "lose"}">${esc(data.prize_level)}</div>
        <p style="font-size:18px">${isWin ? "奖金：" + data.prize_amount : ""}</p>
      </div>
      <p class="disclaimer" style="text-align:left;margin:8px 0 0">* 浮动奖金（一等奖、二等奖）金额取决于奖池和当期销量，此处仅供参考</p>
    </div>`;
  } catch (e) {
    container.innerHTML = `<p class="msg">${esc(e.message)}</p>`;
  }
}

/* ====== 号码对比 ====== */
async function doCompare() {
  const lottery = $("cmp-lottery").value;
  const drawNumber = $("cmp-draw").value.trim();
  const raw = $("cmp-input").value.trim();
  if (!raw) { showMsg("cmp-result", "请输入号码"); return; }
  if (!drawNumber) { showMsg("cmp-result", "请填写期号"); return; }
  const bets = raw.split("\n").filter(Boolean).map(line => {
    const parts = line.split("|").map(s => s.trim());
    return [
      parts[0].split(",").map(s => parseInt(s.trim())).filter(n => !isNaN(n)),
      (parts[1] || "").split(",").map(s => parseInt(s.trim())).filter(n => !isNaN(n)),
    ];
  });
  if (!bets.length) { showMsg("cmp-result", "未解析出有效号码"); return; }
  const container = $("cmp-result");
  container.style.display = "block";
  container.innerHTML = "<p>对比中...</p>";
  try {
    const data = await api("POST", "/analysis/compare", { lottery, draw_number: drawNumber, bets });
    container.innerHTML = `<div class="result-card">
      <p>第 ${esc(data.draw_number)} 期对比结果</p>
      <div style="margin-top:12px">${data.results.map(r => {
        const isWin = r.prize_level !== "未中奖";
        return `<div class="cmp-row ${isWin ? "highlight" : ""}">
          <span style="font-weight:600;min-width:40px">#${r.index}</span>
          <span>${renderBalls(r.numbers, "red")} ${renderBalls(r.extra_numbers, lottery === "ssq" ? "blue" : "gold")}</span>
          <span>命中 ${r.main_hit}+${r.extra_hit}</span>
          <span class="tag ${isWin ? "win" : "lose"}">${esc(r.prize_level)}</span>
          <span style="color:#888;font-size:13px">${isWin ? r.prize_amount : ""}</span>
        </div>`;
      }).join("")}</div>
    </div>`;
  } catch (e) {
    container.innerHTML = `<p class="msg">${esc(e.message)}</p>`;
  }
}

/* ====== 用户系统 ====== */
function updateUserUI() {
  const navUser = $("nav-user");
  const loginBtn = $("btn-show-login");
  if (token) {
    navUser.style.display = "inline";
    loginBtn.style.display = "none";
    // 尝试获取用户名
    api("GET", "/users/me").then(u => {
      navUser.innerHTML = `<span style="color:#fff;margin-right:8px">${esc(u.username)}</span>
        <a href="#" data-page="favorites">收藏</a>
        <a href="#" id="btn-logout">退出</a>`;
      $("btn-logout").onclick = e => { e.preventDefault(); logout(); };
    }).catch(() => { token = null; localStorage.removeItem("lottery_token"); updateUserUI(); });
  } else {
    navUser.style.display = "none";
    loginBtn.style.display = "inline";
  }
}

// 登录弹窗
$("btn-show-login").addEventListener("click", e => {
  e.preventDefault();
  $("login-modal").style.display = "flex";
  $("login-modal-title").textContent = "登录 / 注册";
  $("login-username").value = "";
  $("login-password").value = "";
  showMsg("login-msg", "", false);
});
qs(".modal-close").addEventListener("click", () => $("login-modal").style.display = "none");
$("login-modal").addEventListener("click", e => { if (e.target === e.currentTarget) $("login-modal").style.display = "none"; });

async function doLogin() {
  const username = $("login-username").value.trim();
  const password = $("login-password").value.trim();
  if (!username || !password) { showMsg("login-msg", "请输入用户名和密码"); return; }
  try {
    const data = await api("POST", "/users/login", { username, password });
    token = data.token;
    localStorage.setItem("lottery_token", token);
    $("login-modal").style.display = "none";
    updateUserUI();
  } catch (e) {
    showMsg("login-msg", e.message);
  }
}

async function doRegister() {
  const username = $("login-username").value.trim();
  const password = $("login-password").value.trim();
  if (!username || !password) { showMsg("login-msg", "请输入用户名和密码"); return; }
  try {
    const data = await api("POST", "/users/register", { username, password });
    token = data.token;
    localStorage.setItem("lottery_token", token);
    $("login-modal").style.display = "none";
    updateUserUI();
  } catch (e) {
    showMsg("login-msg", e.message);
  }
}

function logout() {
  token = null;
  localStorage.removeItem("lottery_token");
  updateUserUI();
  showPage("home");
}

/* ====== 收藏管理 ====== */
async function loadFavorites() {
  if (!token) { $("fav-list").innerHTML = "<p>请先登录</p>"; return; }
  document.title = "我的收藏 - 彩票数据平台";
  const container = $("fav-list");
  container.innerHTML = "<p>加载中...</p>";
  try {
    const data = await api("GET", "/favorites");
    if (data.length === 0) {
      container.innerHTML = "<p>还没有收藏号码</p>";
      return;
    }
    container.innerHTML = data.map(f => `<div class="fav-item">
      <div>
        <strong>${esc(f.lottery_name)}</strong>
        <span style="margin-left:8px">${renderBalls(f.numbers, f.lottery_code === "ssq" ? "red" : "red")} ${renderBalls(f.extra_numbers, f.lottery_code === "ssq" ? "blue" : "gold")}</span>
        ${f.note ? `<span style="color:#888;margin-left:8px">${esc(f.note)}</span>` : ""}
      </div>
      <button class="btn-small btn-danger" onclick="deleteFav(${f.id})">删除</button>
    </div>`).join("");
  } catch (e) {
    container.innerHTML = `<p class="msg">${esc(e.message)}</p>`;
  }
}

async function deleteFav(id) {
  if (!confirm("确定删除该收藏？")) return;
  try {
    await api("DELETE", `/favorites/${id}`);
    loadFavorites();
  } catch (e) {
    alert(e.message);
  }
}

async function addFavoriteFromPage() {
  if (!token) { showMsg("fav-msg", "请先登录"); return; }
  const numbers = $("fav-numbers").value.trim().split(",").map(s => parseInt(s.trim())).filter(n => !isNaN(n));
  const extra = $("fav-extra").value.trim().split(",").map(s => parseInt(s.trim())).filter(n => !isNaN(n));
  if (!numbers.length) { showMsg("fav-msg", "请输入主号码"); return; }
  const note = $("fav-note").value.trim();
  try {
    const data = await api("POST", "/favorites", {
      lottery_code: currentLottery, numbers, extra_numbers: extra, note,
    });
    showMsg("fav-msg", "收藏成功", false);
    $("fav-numbers").value = ""; $("fav-extra").value = ""; $("fav-note").value = "";
  } catch (e) {
    showMsg("fav-msg", e.message);
  }
}

async function quickFav(lottery, drawNumber) {
  if (!token) { alert("请先登录后再收藏"); return; }
  try {
    const data = await api("GET", `/draws/${lottery}?draw_number=${drawNumber}`);
    const nums = data.numbers, extra = data.extra_numbers;
    await api("POST", "/favorites", {
      lottery_code: lottery, numbers: nums, extra_numbers: extra, note: "来自开奖列表",
    });
    alert("收藏成功！");
  } catch (e) {
    alert("收藏失败: " + e.message);
  }
}

/* ====== 初始化 ====== */
document.addEventListener("DOMContentLoaded", () => {
  updateUserUI();
  loadHome();
});
