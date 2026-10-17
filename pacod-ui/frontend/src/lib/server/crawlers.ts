/**
 * Crawler identification from the User-Agent header.
 *
 * Pure and synchronous by design: this runs on every request, so it must cost
 * nothing measurable. Network verification of a claimed identity is a separate,
 * asynchronous concern — see `crawler-verify.ts`.
 *
 * Why this exists at all: `pacod_frontend_http_requests_total` splits traffic
 * into `registered` and `anonymous`, which silently files every crawler under
 * "anonymous humans". Nothing else in the stack can tell the difference —
 * Caddy's metrics carry `{server, handler}` only, with no User-Agent dimension.
 */

export type CrawlerCategory = 'search' | 'ai' | 'seo' | 'social';

export interface CrawlerIdentity {
	/**
	 * Stable, lowercase id. This becomes a Prometheus label, so the set must stay
	 * BOUNDED — never derive it from the User-Agent string itself.
	 */
	bot: string;
	category: CrawlerCategory;
	/**
	 * Domain suffixes a genuine instance must reverse-resolve to. Absent means the
	 * operator publishes no reverse-DNS contract, so the bot is unverifiable —
	 * which is reported as `unknown`, NOT as `unverified`.
	 */
	verifyHosts?: string[];
}

interface CrawlerRule extends CrawlerIdentity {
	/** Lowercase substring to look for in the User-Agent. */
	match: string;
}

/**
 * Matched in order, so **most specific first**. The overlaps are real and each
 * one has bitten someone:
 *
 *  - `Google-Extended` (the AI training opt-out token) and `Googlebot-Image`
 *    both contain neither... but `Googlebot-News`/`Googlebot-Video` DO contain
 *    `googlebot`, so they must precede the bare `googlebot` rule or they all
 *    collapse into one bucket.
 *  - `ChatGPT-User` (a live user-initiated fetch) and `OAI-SearchBot` (indexing)
 *    are different products from `GPTBot` (training) and must not be merged —
 *    blocking one in robots.txt does not block the others.
 *  - `AdsBot-Google-Mobile` precedes `AdsBot-Google`.
 */
const RULES: CrawlerRule[] = [
	// --- Search engines -----------------------------------------------------
	// Google verifies via *.googlebot.com; some crawlers (AdsBot, Google-Extended,
	// the Google-Site-Verification fetcher) come from *.google.com instead, so
	// both suffixes are accepted for every Google property.
	{
		match: 'googlebot-image',
		bot: 'googlebot-image',
		category: 'search',
		verifyHosts: ['googlebot.com', 'google.com']
	},
	{
		match: 'googlebot-news',
		bot: 'googlebot-news',
		category: 'search',
		verifyHosts: ['googlebot.com', 'google.com']
	},
	{
		match: 'googlebot-video',
		bot: 'googlebot-video',
		category: 'search',
		verifyHosts: ['googlebot.com', 'google.com']
	},
	{
		match: 'adsbot-google-mobile',
		bot: 'adsbot-google-mobile',
		category: 'search',
		verifyHosts: ['googlebot.com', 'google.com']
	},
	{
		match: 'adsbot-google',
		bot: 'adsbot-google',
		category: 'search',
		verifyHosts: ['googlebot.com', 'google.com']
	},
	{
		match: 'storebot-google',
		bot: 'storebot-google',
		category: 'search',
		verifyHosts: ['googlebot.com', 'google.com']
	},
	{
		match: 'googlebot',
		bot: 'googlebot',
		category: 'search',
		verifyHosts: ['googlebot.com', 'google.com']
	},

	{ match: 'bingpreview', bot: 'bingpreview', category: 'search', verifyHosts: ['search.msn.com'] },
	{ match: 'adidxbot', bot: 'adidxbot', category: 'search', verifyHosts: ['search.msn.com'] },
	{ match: 'bingbot', bot: 'bingbot', category: 'search', verifyHosts: ['search.msn.com'] },

	{
		match: 'yandexbot',
		bot: 'yandexbot',
		category: 'search',
		verifyHosts: ['yandex.ru', 'yandex.net', 'yandex.com']
	},
	{
		match: 'yandeximages',
		bot: 'yandeximages',
		category: 'search',
		verifyHosts: ['yandex.ru', 'yandex.net', 'yandex.com']
	},
	{ match: 'duckduckbot', bot: 'duckduckbot', category: 'search' },
	{ match: 'duckduckgo-favicons-bot', bot: 'duckduckbot', category: 'search' },
	{
		match: 'baiduspider',
		bot: 'baiduspider',
		category: 'search',
		verifyHosts: ['baidu.com', 'baidu.jp']
	},
	{
		match: 'applebot-extended',
		bot: 'applebot-extended',
		category: 'ai',
		verifyHosts: ['applebot.apple.com']
	},
	{ match: 'applebot', bot: 'applebot', category: 'search', verifyHosts: ['applebot.apple.com'] },
	{ match: 'slurp', bot: 'yahoo-slurp', category: 'search' },
	{ match: 'seznambot', bot: 'seznambot', category: 'search' },
	{ match: 'petalbot', bot: 'petalbot', category: 'search' },
	{ match: 'naver', bot: 'naverbot', category: 'search' },

	// --- AI / LLM crawlers ---------------------------------------------------
	// Three distinct OpenAI agents; see the note above about not merging them.
	{ match: 'oai-searchbot', bot: 'oai-searchbot', category: 'ai' },
	{ match: 'chatgpt-user', bot: 'chatgpt-user', category: 'ai' },
	{ match: 'gptbot', bot: 'gptbot', category: 'ai' },
	{ match: 'claudebot', bot: 'claudebot', category: 'ai' },
	{ match: 'claude-web', bot: 'claude-web', category: 'ai' },
	{ match: 'anthropic-ai', bot: 'anthropic-ai', category: 'ai' },
	{ match: 'perplexitybot', bot: 'perplexitybot', category: 'ai' },
	{ match: 'perplexity-user', bot: 'perplexity-user', category: 'ai' },
	{
		match: 'google-extended',
		bot: 'google-extended',
		category: 'ai',
		verifyHosts: ['googlebot.com', 'google.com']
	},
	{ match: 'ccbot', bot: 'ccbot', category: 'ai' },
	{ match: 'bytespider', bot: 'bytespider', category: 'ai' },
	{ match: 'amazonbot', bot: 'amazonbot', category: 'ai' },
	{ match: 'meta-externalagent', bot: 'meta-externalagent', category: 'ai' },
	{ match: 'meta-externalfetcher', bot: 'meta-externalfetcher', category: 'ai' },
	{ match: 'cohere-ai', bot: 'cohere-ai', category: 'ai' },
	{ match: 'diffbot', bot: 'diffbot', category: 'ai' },
	{ match: 'timpibot', bot: 'timpibot', category: 'ai' },
	{ match: 'youbot', bot: 'youbot', category: 'ai' },

	// --- SEO / backlink tools ------------------------------------------------
	{ match: 'ahrefsbot', bot: 'ahrefsbot', category: 'seo' },
	{ match: 'ahrefssiteaudit', bot: 'ahrefs-site-audit', category: 'seo' },
	{ match: 'semrushbot', bot: 'semrushbot', category: 'seo' },
	{ match: 'mj12bot', bot: 'mj12bot', category: 'seo' },
	{ match: 'dotbot', bot: 'dotbot', category: 'seo' },
	{ match: 'rogerbot', bot: 'rogerbot', category: 'seo' },
	{ match: 'dataforseobot', bot: 'dataforseobot', category: 'seo' },
	{ match: 'screaming frog', bot: 'screaming-frog', category: 'seo' },
	{ match: 'blexbot', bot: 'blexbot', category: 'seo' },
	{ match: 'serpstatbot', bot: 'serpstatbot', category: 'seo' },
	{ match: 'sitecheckerbotcrawler', bot: 'sitechecker', category: 'seo' },

	// --- Social / link-preview fetchers --------------------------------------
	// These read the Open Graph tags, so this category doubles as evidence that
	// the share-preview metadata is actually being requested.
	{ match: 'facebookexternalhit', bot: 'facebook', category: 'social' },
	{ match: 'facebookcatalog', bot: 'facebook', category: 'social' },
	{ match: 'meta-externalhit', bot: 'facebook', category: 'social' },
	{ match: 'whatsapp', bot: 'whatsapp', category: 'social' },
	{ match: 'twitterbot', bot: 'twitterbot', category: 'social' },
	{ match: 'linkedinbot', bot: 'linkedinbot', category: 'social' },
	{ match: 'slackbot', bot: 'slackbot', category: 'social' },
	{ match: 'telegrambot', bot: 'telegrambot', category: 'social' },
	{ match: 'discordbot', bot: 'discordbot', category: 'social' },
	{ match: 'pinterest', bot: 'pinterestbot', category: 'social' },
	{ match: 'redditbot', bot: 'redditbot', category: 'social' },
	{ match: 'embedly', bot: 'embedly', category: 'social' },
	{ match: 'skypeuripreview', bot: 'skype-preview', category: 'social' }
];

/**
 * Identify a crawler from its User-Agent, or `null` for ordinary traffic.
 *
 * `null` is the hot path — every human request lands here — so the only work
 * done before the loop is one `toLowerCase()`.
 */
export function classifyUserAgent(ua: string | null | undefined): CrawlerIdentity | null {
	if (!ua) return null;
	const needle = ua.toLowerCase();
	for (const rule of RULES) {
		if (needle.includes(rule.match)) {
			return { bot: rule.bot, category: rule.category, verifyHosts: rule.verifyHosts };
		}
	}
	return null;
}

/** Every bot id this module can emit — the bounded label set, for dashboards. */
export function knownBots(): string[] {
	return Array.from(new Set(RULES.map((r) => r.bot))).sort();
}
