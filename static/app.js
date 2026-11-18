"use strict";
const $ = (id) => document.getElementById(id);
const tz = "Asia/Taipei";
const asDate = (stamp) => new Date(stamp * 1000);
const isoDate = (stamp) => new Date((stamp + 8 * 3600) * 1000).toISOString().slice(0, 10);
const timeText = (stamp) => asDate(stamp).toLocaleTimeString("zh-TW", {timeZone:tz, hourCycle:"h23", hour:"2-digit", minute:"2-digit", second:"2-digit"});
const stampText = (stamp) => stamp == null ? "—" : isoDate(stamp) + " " + timeText(stamp);
let state = null, anchor = 0, busy = false, ready = false, stateVersion = 0;
let page = 1, lastQuery = "", recordVersion = 0;

function tell(message = "", error = false) {
  $("notice").textContent = message;
  $("notice").className = message ? (error ? "msg error" : "msg") : "";
}
function signedOut() {
  ++stateVersion; ++recordVersion;
  state = null; ready = false; lastQuery = ""; page = 1;
  $("results").replaceChildren(); $("pager").hidden = true;
  $("scope").value = "mine"; $("from").value = ""; $("to").value = "";
  $("workspace").hidden = true; $("identity").hidden = true; $("loading").hidden = true;
  $("login-panel").hidden = false; $("retry").hidden = true;
  chooseTab("clock");
}
async function request(path, data) {
  const init = {cache:"no-store", credentials:"same-origin"};
  if (data !== undefined) {
    init.method = "POST"; init.headers = {"Content-Type":"application/json"};
    init.body = JSON.stringify(data);
  }
  let response;
  try { response = await fetch(path,init); }
  catch { throw new Error("連線中斷。請恢復網路後重新確認打卡紀錄。"); }
  if (!response.ok) {
    const result = await response.json().catch(() => ({}));
    if (response.status === 401 && path !== "/api/login") signedOut();
    throw new Error(result.error || "服務暫時無法使用。");
  }
  return response;
}
function tick() {
  if (!state) return;
  const stamp = state.now + Math.floor((performance.now() - anchor) / 1000);
  $("clock").textContent = timeText(stamp);
  $("today").textContent = asDate(stamp).toLocaleDateString("zh-TW", {timeZone:tz, year:"numeric", month:"long", day:"numeric", weekday:"long"});
}
function draw() {
  $("loading").hidden = true; $("login-panel").hidden = true; $("workspace").hidden = false;
  $("identity").hidden = false; $("identity-name").textContent = state.user.name;
  $("scope-label").hidden = state.user.role !== "admin";
  const working = Boolean(state.open);
  $("status").textContent = working ? "上班中" : "待上班";
  $("context").textContent = working ? "本班上班時間　" + stampText(state.open.started) : "可以開始下一個班次";
  $("punch").textContent = working ? "下班打卡" : "上班打卡";
  $("punch").classList.toggle("out",working); $("punch").disabled = busy || !ready;
  $("last-in").textContent = stampText(state.latest?.started);
  $("last-out").textContent = working ? "待下班" : stampText(state.latest?.ended);
  if (!$("from").value) {
    $("to").value = isoDate(state.now); $("from").value = $("to").value.slice(0,8) + "01";
  }
  tick();
}
async function refresh() {
  const version = ++stateVersion;
  ready = false; $("punch").disabled = true;
  try {
    const value = await (await request("/api/state")).json();
    if (version !== stateVersion) return false;
    if (state && state.user.id !== value.user.id) {
      ++recordVersion; lastQuery = ""; $("results").replaceChildren(); $("pager").hidden = true;
      $("scope").value = "mine"; chooseTab("clock");
    }
    state = value; anchor = performance.now(); ready = true; $("retry").hidden = true; draw();
    return true;
  } catch (e) {
    if (version !== stateVersion) return false;
    $("loading").hidden = true; $("retry").hidden = false; tell(e.message,true);
    return false;
  }
}
function chooseTab(tab) {
  for (const name of ["clock","records"]) {
    $(name+"-panel").hidden = tab !== name;
    if (name === tab) $("tab-"+name).setAttribute("aria-current","page");
    else $("tab-"+name).removeAttribute("aria-current");
  }
}
$("login-form").onsubmit = async (event) => {
  event.preventDefault(); $("login-submit").disabled = true; tell();
  try {
    await request("/api/login",{account:$("account").value.trim(),password:$("password").value,remember:$("remember").checked});
    $("password").value = ""; await refresh();
  } catch (e) { tell(e.message,true); }
  finally { $("login-submit").disabled = false; }
};
$("logout").onclick = async () => {
  try { await request("/api/logout",{}); signedOut(); tell("已登出這台裝置。"); }
  catch (e) { tell(e.message,true); }
};
$("retry").onclick = () => { tell(); refresh(); };
$("tab-clock").onclick = () => { chooseTab("clock"); refresh(); };
$("tab-records").onclick = () => { chooseTab("records"); loadRecords(true); };
$("punch").onclick = async () => {
  if (busy || !ready || !state) return;
  busy = true; $("punch").disabled = true; tell();
  const action = state.open ? "out" : "in";
  let message, failed = false;
  try {
    const result = await (await request("/api/punch", {action,revision:state.user.revision,key:crypto.randomUUID()})).json();
    message = (action === "out" ? "下班" : "上班") + "打卡成功，已記錄 " + timeText(result.stamp) + "。";
  } catch (e) { failed = true; message = e.message; }
  finally {
    const ok = await refresh(); busy = false;
    if (state) draw();
    if (ok || !state) tell(message,failed);
    else if (!failed) tell(message+" 最新狀態暫時無法讀取，請重新連線。");
  }
};
function dateQuery() {
  const form = $("filters");
  $("to").setCustomValidity($("to").value < $("from").value ? "結束日期必須在開始日期當天或之後。" : "");
  if (!form.reportValidity()) return null;
  return new URLSearchParams({from:$("from").value,to:$("to").value,scope:$("scope").value});
}
function element(tag, text, cls) {
  const el = document.createElement(tag); el.textContent = text;
  if (cls) el.className = cls;
  return el;
}
function recordCard(row) {
  const box = element("article","","record"), head = element("div","","record-head");
  head.append(element("strong",isoDate(row.started)+"　"+row.name),element("span",row.ended == null ? "待下班" : row.corrected ? "已補登" : "已下班","tag"));
  const times = element("dl","");
  for (const [title,stamp] of [["上班",row.started],["下班",row.ended]]) {
    const cell = element("div","");
    cell.append(element("dt",title),element("dd",stampText(stamp))); times.append(cell);
  }
  box.append(head,times); return box;
}
async function loadRecords(reset = false) {
  const generation = ++recordVersion;
  const q = reset || !lastQuery ? dateQuery() : new URLSearchParams(lastQuery);
  if (!q) return;
  if (reset) { page = 1; lastQuery = q.toString(); }
  q.set("page",String(page)); $("pager").hidden = true;
  $("results").textContent = "正在載入紀錄…"; tell();
  try {
    const result = await (await request("/api/records?"+q)).json();
    if (generation !== recordVersion) return;
    $("results").replaceChildren(...result.rows.map(recordCard));
    if (!result.rows.length) $("results").append(element("p","此日期範圍尚無打卡紀錄。","empty"));
    $("pager").hidden = page === 1 && !result.hasMore; $("prev").disabled = page === 1;
    $("next").disabled = !result.hasMore; $("page").textContent = "第 "+page+" 頁";
  } catch (e) {
    if (generation === recordVersion) $("results").replaceChildren();
    tell(e.message,true);
  }
}
$("filters").onsubmit = (event) => { event.preventDefault(); loadRecords(true); };
for (const id of ["to","from"]) $(id).oninput = () => $("to").setCustomValidity("");
$("prev").onclick = () => { page--; loadRecords(); };
$("next").onclick = () => { page++; loadRecords(); };
$("export").onclick = async () => {
  const q = dateQuery(); if (!q) return;
  $("export").disabled = true; tell();
  try {
    const resp = await request("/api/export?"+q);
    const url = URL.createObjectURL(await resp.blob());
    const a = document.createElement("a"); a.href = url;
    a.download = "出勤紀錄_"+q.get("from")+"_"+q.get("to")+".xlsx";
    document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url),60000);
    tell("Excel 檔案已準備完成。");
  } catch (e) { tell(e.message,true); }
  finally { $("export").disabled = false; }
};
document.addEventListener("visibilitychange",() => { if (!document.hidden && state && !busy) refresh(); });
window.addEventListener("pageshow",e => { if (e.persisted) { signedOut(); refresh(); } });
window.addEventListener("online",() => { if (!busy) refresh(); });
window.addEventListener("offline",() => {
  ready = false; $("punch").disabled = true; $("retry").hidden = false;
  tell("目前離線，請連線後再打卡。",true);
});
setInterval(tick,1000); refresh();
