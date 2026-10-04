import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const playwright = await import(process.env.PLAYWRIGHT_MODULE_PATH
  ? pathToFileURL(process.env.PLAYWRIGHT_MODULE_PATH).href
  : 'playwright');
const { chromium } = playwright.default || playwright;
const fixtureDirectory = dirname(fileURLToPath(import.meta.url));

async function main() {
  const browser = await chromium.launch({
    headless: true,
    ...(process.env.BROWSER_EXECUTABLE_PATH ? { executablePath: process.env.BROWSER_EXECUTABLE_PATH } : {}),
  });
  try {
    const files = process.argv.slice(2);
    for (const file of files.length ? files : ['scan-controls.js', 'live-camera.js', 'eye-diagnostics.js', 'voice-session.js']) {
      const page = await browser.newPage();
      const failures = [];
      page.on('pageerror', error => failures.push(error.message));
      // Block local API and provider traffic before hydration installs its pollers.
      await page.route('**/v1/**', route => route.fulfill({
        status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'Browser test fixture not installed yet' }),
      }));
      await page.route('https://api.openai.com/**', route => route.abort());
      await page.goto(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:3000', { waitUntil: 'networkidle', timeout: 120000 });
      const source = readFileSync(join(fixtureDirectory, file), 'utf8');
      try {
        const result = await page.evaluate(source => new Function(source)(), source);
        if (failures.length) throw new Error(failures.join('\n'));
        console.log(`${file}: ${JSON.stringify(result)}`);
      } finally {
        await page.close();
      }
    }
  } finally {
    await browser.close();
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
