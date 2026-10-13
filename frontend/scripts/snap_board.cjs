// Screenshot the dashboard with one ticket selected, as the board looks right now.
// Usage (from frontend/, with the dashboard on :5173 and the backend on :8000):
//   node scripts/snap_board.cjs <ticketId> <out.jpg>
const puppeteer = require('puppeteer-core')

const CHROME = process.env.CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const [ticket, out] = process.argv.slice(2)
if (!ticket || !out) {
  console.error('usage: node scripts/snap_board.cjs <ticketId> <out.jpg>')
  process.exit(2)
}

;(async () => {
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: true,
    args: ['--no-first-run', '--no-default-browser-check', '--hide-scrollbars'],
    defaultViewport: { width: 1440, height: 900, deviceScaleFactor: 1 },
  })
  try {
    const page = await browser.newPage()
    await page.goto('http://localhost:5173', { waitUntil: 'networkidle0' })
    await page.waitForSelector('.ticket-card')
    await page.evaluate((n) => {
      const card = [...document.querySelectorAll('.ticket-card')].find((c) => c.querySelector('.ticket-no').textContent === `#${n}`)
      card.click()
    }, ticket)
    // Wait until the selected ticket's run (feed + summary) has loaded, if it has one.
    await page.waitForFunction(
      () => document.querySelector('.summary') || document.querySelector('.feed .panel-sub')?.textContent.startsWith('No run'),
      { timeout: 20000 },
    )
    await new Promise((r) => setTimeout(r, 1200)) // let the count-up and stamp animations settle
    await page.evaluate(() => window.scrollTo(0, 0))
    await page.screenshot({ path: out, fullPage: true, type: 'jpeg', quality: 82 })
    console.log(`saved ${out}`)
  } finally {
    await browser.close()
  }
})().catch((err) => {
  console.error(err.message)
  process.exit(1)
})
