/**
 * Renders the share images and app icons in design/og/*.html to static/*.png.
 *
 * Uses the Chromium that Playwright (already a devDependency, for the e2e
 * suite) has cached — so regenerating needs no extra install and, crucially,
 * adds no runtime dependency to the app. The PNGs are committed; this script
 * only runs when the artwork changes.
 *
 * Imported from `@playwright/test` — the DECLARED dependency — rather than the
 * bare `playwright` package it happens to hoist, so a hoisting change cannot
 * break the documented regeneration path.
 *
 *   node design/og/render.mjs
 */
import { chromium } from '@playwright/test';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const staticDir = resolve(here, '../../static');

// [source, output, width, height] — sizes are fixed by the platforms:
// 1200x630 is the Open Graph card, the rest are manifest / iOS icons.
const TARGETS = [
	['og-pacod.html', 'og-pacod.png', 1200, 630],
	['icon-pacod.html', 'icon-512.png', 512, 512],
	['icon-pacod.html', 'icon-192.png', 192, 192],
	['icon-pacod.html', 'apple-touch-icon.png', 180, 180]
];

const browser = await chromium.launch();
for (const [src, out, width, height] of TARGETS) {
	const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 1 });
	await page.goto(`file://${resolve(here, src)}`, { waitUntil: 'load' });
	await page.screenshot({ path: resolve(staticDir, out) });
	await page.close();
	console.log(`${out}  ${width}x${height}`);
}
await browser.close();
