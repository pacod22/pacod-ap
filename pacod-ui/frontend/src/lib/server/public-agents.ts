/**
 * The released agents' public copy, from the backend's unauthenticated
 * `GET /api/v1/public/agents[/{slug}]` (agent editor → published version →
 * `meta.marketing`). Server-only: SSR loads (the landing page, `/agents/[slug]`,
 * the sitemap) call the chat API directly, without a session.
 *
 * The API is authoritative whenever it answers: an agent an admin hid or
 * unpublished must disappear from the landing page, its /agents page and the
 * sitemap. Only when it is UNREACHABLE (`null`) do callers fall back to the
 * hand-written modules in `$lib/marketing/agents`, so a backend blip never
 * blanks the public site.
 */
import { env } from '$env/dynamic/private';
import type { AgentDefinition, AgentPage } from '$lib/marketing/agents';

const TIMEOUT_MS = 1500;

export interface PublicAgentCard {
	slug: string;
	profile_id: string;
	order: number;
	accent: string;
	soft: string;
	icon: string;
	card: Record<string, { name?: string; desc?: string; tag?: string | null }>;
}

interface PublicAgentFull extends PublicAgentCard {
	page: Record<string, Partial<AgentPage>>;
}

type Fetch = typeof fetch;

async function getJson(
	fetchFn: Fetch,
	path: string
): Promise<{ status: number; body: unknown } | null> {
	const base = env.API_INTERNAL_URL ?? 'http://localhost:8000';
	try {
		const res = await fetchFn(`${base}${path}`, {
			signal: AbortSignal.timeout(TIMEOUT_MS),
			// SSR call: not a second user request in the analytics.
			headers: { 'X-Pacod-Internal': '1' }
		});
		if (res.status === 404) return { status: 404, body: null };
		if (!res.ok) return null;
		return { status: res.status, body: await res.json() };
	} catch {
		return null;
	}
}

/** Landing cards in display order; `null` when the API is unreachable. */
export async function fetchPublicAgents(fetchFn: Fetch): Promise<PublicAgentCard[] | null> {
	const res = await getJson(fetchFn, '/api/v1/public/agents');
	return res && Array.isArray(res.body) ? (res.body as PublicAgentCard[]) : null;
}

function uniqueBy<T>(items: T[], key: (t: T) => string): T[] {
	const seen = new Set<string>();
	return items.filter((t) => {
		const k = key(t);
		if (!k || seen.has(k)) return false;
		seen.add(k);
		return true;
	});
}

/** Fill a possibly sparse page document so the page template can render it
 *  (keyed `{#each}` blocks also need unique, non-empty keys). */
export function normalizePage(
	p: Partial<AgentPage> | undefined,
	fallback?: Partial<AgentPage>
): AgentPage {
	const s = (k: keyof AgentPage) => String(p?.[k] ?? fallback?.[k] ?? '');
	return {
		eyebrow: s('eyebrow'),
		title: s('title'),
		lead: s('lead'),
		metaTitle: s('metaTitle') || s('title'),
		metaDescription: s('metaDescription') || s('lead'),
		valuePropsTitle: s('valuePropsTitle'),
		valueProps: uniqueBy(p?.valueProps ?? fallback?.valueProps ?? [], (v) => v.title),
		useCasesTitle: s('useCasesTitle'),
		useCases: uniqueBy(p?.useCases ?? fallback?.useCases ?? [], (u) => u),
		faqTitle: s('faqTitle'),
		faq: uniqueBy(p?.faq ?? fallback?.faq ?? [], (f) => f.q),
		ctaTitle: s('ctaTitle'),
		ctaBody: s('ctaBody'),
		ctaLabel: s('ctaLabel'),
		homeLabel: s('homeLabel')
	};
}

/**
 * One agent page for `locale`:
 *   - `AgentDefinition` (doc holding just that locale) when the API lists it,
 *   - `'not_found'` when the API answered and does not list it,
 *   - `null` when the API is unreachable (caller falls back to static modules).
 */
export async function fetchPublicAgent(
	fetchFn: Fetch,
	slug: string,
	locale: string
): Promise<AgentDefinition | 'not_found' | null> {
	const res = await getJson(fetchFn, `/api/v1/public/agents/${encodeURIComponent(slug)}`);
	if (!res) return null;
	if (res.status === 404 || !res.body) return 'not_found';
	const a = res.body as PublicAgentFull;
	const doc = normalizePage(a.page?.[locale], a.page?.en);
	return {
		slug: a.slug,
		profileId: a.profile_id,
		accent: a.accent || '#0e6e57',
		doc: { [locale]: doc } as AgentDefinition['doc']
	};
}
