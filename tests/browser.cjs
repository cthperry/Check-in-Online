// 使用獨立瀏覽器設定檔驗證打卡、登入保留與 Excel 下載。
const assert = require("node:assert/strict");
const {spawn} = require("node:child_process");
const {randomBytes} = require("node:crypto");
const {once} = require("node:events");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const {chromium} = require("playwright-core");

async function main() {
  const password = randomBytes(24).toString("hex");
  const server = spawn(process.env.PYTHON || "python",[path.join(__dirname,"browser_server.py")],{
    env:{...process.env,ATTENDANCE_TEST_PASSWORD:password},stdio:["ignore","pipe","inherit"]});
  const profile = await fs.mkdtemp(path.join(os.tmpdir(),"attendance-browser-"));
  const browserOptions = {executablePath:process.env.CHROME_PATH,headless:true};
  let browser, mobile;
  try {
    const url = await Promise.race([
      once(server.stdout,"data").then(([data])=>data.toString().trim()),
      once(server,"exit").then(()=>{throw new Error("測試服務已提前停止");})
    ]);
    const errors = [];
    const watch = (page) => page.on("pageerror",error=>errors.push(error.message));
    async function login(page) {
      await page.goto(url);
      await page.getByLabel("員工帳號").fill("qa-staff");
      await page.getByLabel("密碼",{exact:true}).fill(password);
      await page.getByRole("button",{name:"登入",exact:true}).click();
      await page.locator("#workspace").waitFor();
    }
    async function checkWidth(page) {
      assert(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth),"頁面出現水平捲軸");
    }
    async function capture(page,file) {
      if (process.env.QA_SCREENSHOTS) {
        await fs.mkdir(process.env.QA_SCREENSHOTS,{recursive:true});
        await page.screenshot({path:path.join(process.env.QA_SCREENSHOTS,file),fullPage:true});
      }
    }
    function openPhone(width) {
      return chromium.launchPersistentContext(profile,{...browserOptions,
        viewport:{width,height:812},isMobile:true,hasTouch:true});
    }
    mobile = await openPhone(375);
    let phone = await mobile.newPage(); watch(phone);
    await login(phone); await checkWidth(phone);
    await phone.getByRole("button",{name:"上班打卡",exact:true}).click();
    await phone.getByRole("button",{name:"下班打卡",exact:true}).waitFor();
    await capture(phone,"mobile.png");
    await mobile.close();
    mobile = await openPhone(320);
    phone = await mobile.newPage(); watch(phone);
    await phone.goto(url);
    await phone.getByRole("button",{name:"下班打卡",exact:true}).waitFor();
    await checkWidth(phone);
    await phone.getByRole("button",{name:"出勤紀錄",exact:true}).click();
    await phone.locator(".record").waitFor(); await checkWidth(phone);
    await capture(phone,"mobile-records.png");
    const [download] = await Promise.all([
      phone.waitForEvent("download"),phone.getByRole("button",{name:"匯出 Excel"}).click()
    ]);
    assert(download.suggestedFilename().endsWith(".xlsx"));
    assert.equal(await download.failure(),null);
    const bytes = await fs.readFile(await download.path());
    assert.equal(bytes.subarray(0,2).toString(),"PK");

    browser = await chromium.launch(browserOptions);
    const desktop = await browser.newPage({viewport:{width:1366,height:800}}); watch(desktop);
    await login(desktop);
    await desktop.getByRole("button",{name:"下班打卡",exact:true}).click();
    await desktop.getByRole("button",{name:"上班打卡",exact:true}).waitFor();
    await checkWidth(desktop); await capture(desktop,"desktop.png");

    await phone.getByRole("button",{name:"查詢",exact:true}).click();
    await phone.getByText("已下班",{exact:true}).waitFor();
    await phone.getByRole("button",{name:/^編輯/}).click();
    const dialog = phone.getByRole("dialog",{name:"編輯出勤紀錄"});
    await dialog.waitFor();
    await checkWidth(phone);
    assert(await dialog.evaluate(el => el.scrollWidth <= el.clientWidth));
    await capture(phone,"mobile-edit.png");
    await dialog.getByLabel("下班時間（台灣）").fill("");
    await dialog.getByLabel("備註說明").fill("測試版更正");
    await dialog.getByRole("button",{name:"儲存變更"}).click();
    await dialog.waitFor({state:"hidden"});
    await phone.getByText("已儲存出勤紀錄變更。",{exact:true}).waitFor();
    await phone.getByText("待下班",{exact:true}).waitFor();
    await phone.getByRole("button",{name:"登出",exact:true}).click();
    await phone.locator("#login-panel").waitFor();
    await phone.reload(); await phone.locator("#login-panel").waitFor();
    assert.deepEqual(errors,[]);
    console.log("手機打卡、瀏覽器重啟、跨裝置下班、下載、登出與三種版面寬度均通過。");
  } finally {
    if (mobile) await mobile.close();
    if (browser) await browser.close();
    server.kill();
    await fs.rm(profile,{recursive:true,force:true});
  }
}
main().catch(error=>{console.error(error);process.exitCode=1;});
