import { existsSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { SITE_ENV_KEYS, SITE_ENV_PREFIX, neutralSiteEnv } from './site-env';

const here = (rel: string) => fileURLToPath(new URL(rel, import.meta.url));
const read = (path: string) => readFileSync(path, 'utf-8');

// The combined stack lives in the sibling repo dir; a standalone pacod-ui
// checkout skips the cross-repo checks rather than failing them.
const API_DIR = here('../../../../../pacod-api/');
const hasApi = existsSync(`${API_DIR}docker-compose.yml`);

/** Keys that load a tag or claim domain ownership — must be blanked in the local stack. */
const ID_KEYS = SITE_ENV_KEYS.filter(
	(k) => /(_ID|_IDS|_VERIFICATION|_LABEL)$/.test(k) || k === 'INDEXNOW_KEY'
);

/** The `environment:` body of one compose service. */
function serviceBlock(compose: string, service: string): string {
	const start = compose.indexOf(`\n  ${service}:\n`);
	expect(start, `service ${service} not found`).toBeGreaterThan(-1);
	const rest = compose.slice(start + service.length + 5);
	const next = rest.search(/\n {2}[a-z][\w-]*:\n/);
	const body = next === -1 ? rest : rest.slice(0, next);
	// Comments may name a key; only real mappings count.
	return body
		.split('\n')
		.filter((line) => !line.trim().startsWith('#'))
		.join('\n');
}

describe('PACOD_ mapping', () => {
	it('maps the prefixed keys onto neutral names', () => {
		const env = {
			PACOD_GTM_ID: 'GTM-P',
			PACOD_SITE_URL: '',
			OTHER_GTM_ID: 'ignored',
			GTM_ID: 'legacy-ignored'
		};
		expect(neutralSiteEnv(env)).toEqual({ GTM_ID: 'GTM-P', SITE_URL: '' });
	});
});

describe('no drift between the key list and its consumers', () => {
	it('covers every per-site key site-config.ts reads', () => {
		const src = read(here('./site-config.ts'));
		const reads = new Set([...src.matchAll(/env\.([A-Z][A-Z0-9_]*)/g)].map((m) => m[1]));
		expect([...reads].sort()).toEqual([...SITE_ENV_KEYS].sort());
	});

	it('lists every key in the frontend .env.example', () => {
		const example = read(here('../../../.env.example'));
		for (const key of SITE_ENV_KEYS) {
			expect(example).toMatch(new RegExp(`^${SITE_ENV_PREFIX}${key}=`, 'm'));
		}
	});

	it.skipIf(!hasApi)('lists every key in pacod-api/.env.example', () => {
		const example = read(`${API_DIR}.env.example`);
		for (const key of SITE_ENV_KEYS) {
			expect(example).toMatch(new RegExp(`^${SITE_ENV_PREFIX}${key}=`, 'm'));
		}
	});

	it.skipIf(!hasApi)('maps every PACOD_ key into the ap-frontend service', () => {
		const compose = read(`${API_DIR}docker-compose.yml`);
		const block = serviceBlock(compose, 'ap-frontend');
		for (const key of SITE_ENV_KEYS) {
			expect(block).toContain(`${key}: \${${SITE_ENV_PREFIX}${key}:-`);
		}
	});

	it.skipIf(!hasApi)('blanks every tag id and token in the local ap-frontend service', () => {
		const local = read(`${API_DIR}docker-compose.local.yml`);
		const block = serviceBlock(local, 'ap-frontend');
		for (const key of ID_KEYS) expect(block).toContain(`${key}: ""`);
	});
});
