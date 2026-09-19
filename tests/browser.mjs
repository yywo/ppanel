import assert from "node:assert/strict";
import { chromium } from "playwright";

const base = process.env.BASE_URL;
assert.ok(base, "BASE_URL is required");

const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage();
  const staticFailures = [];
  const pageErrors = [];
  page.on("pageerror", (error) => pageErrors.push(error));
  page.on("response", (response) => {
    const url = new URL(response.url());
    const contentType = response.headers()["content-type"] || "";
    if (
      (url.pathname.startsWith("/static/") || url.pathname.startsWith("/admin/static/")) &&
      contentType.includes("text/html")
    ) {
      console.error("static served as HTML:", response.status(), url.pathname);
    }
    if (
      (url.pathname.startsWith("/static/") ||
        url.pathname.startsWith("/admin/static/") ||
        url.pathname.startsWith("/assets/") ||
        url.pathname.startsWith("/admin/assets/")) &&
      response.status() >= 400
    ) {
      staticFailures.push(`${response.status()} ${url.pathname}`);
    }
  });

  async function open(path, expectedPath) {
    await page.goto(`${base}${path}`, { waitUntil: "domcontentloaded" });
    try {
      await page.waitForFunction(
        () => document.querySelector("#app")?.childElementCount > 0,
        undefined,
        { timeout: 30_000 }
      );
    } catch (error) {
      console.error("body after timeout:", (await page.locator("body").innerHTML()).slice(0, 500));
      throw error;
    }
    assert.equal(new URL(page.url()).pathname, expectedPath);
  }

  await open("/", "/");
  await open("/admin/", "/admin/");
  assert.equal(pageErrors.length, 0, pageErrors.join("\n"));
  assert.equal(staticFailures.length, 0, staticFailures.join(", "));
  console.log("PASS: browser loaded user and admin applications with static assets");
} finally {
  await browser.close();
}
