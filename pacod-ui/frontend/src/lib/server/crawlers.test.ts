import { describe, expect, it } from 'vitest';
import { classifyUserAgent, knownBots } from './crawlers';

/** Real User-Agent strings, as sent by each operator. */
const UA = {
	googlebot: 'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)',
	googlebotImage: 'Googlebot-Image/1.0',
	googlebotNews: 'Googlebot-News',
	googleExtended: 'Mozilla/5.0 (compatible; Google-Extended/1.0)',
	adsbotMobile:
		'Mozilla/5.0 (Linux; Android 6.0.1;) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/41 Mobile Safari/537.36 (compatible; AdsBot-Google-Mobile; +http://www.google.com/mobile/adsbot.html)',
	bingbot: 'Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)',
	yandex: 'Mozilla/5.0 (compatible; YandexBot/3.0; +http://yandex.com/bots)',
	duckduck: 'DuckDuckBot/1.1; (+http://duckduckgo.com/duckduckbot.html)',
	baidu: 'Mozilla/5.0 (compatible; Baiduspider/2.0; +http://www.baidu.com/search/spider.html)',
	applebot: 'Mozilla/5.0 (compatible; Applebot/0.1; +http://www.apple.com/go/applebot)',
	applebotExtended: 'Mozilla/5.0 (compatible; Applebot-Extended/0.1)',
	gptbot:
		'Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; GPTBot/1.1; +https://openai.com/gptbot',
	chatgptUser:
		'Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; ChatGPT-User/1.0; +https://openai.com/bot',
	oaiSearch:
		'Mozilla/5.0 AppleWebKit/537.36 (compatible; OAI-SearchBot/1.0; +https://openai.com/searchbot)',
	claudebot:
		'Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; ClaudeBot/1.0; +claudebot@anthropic.com',
	perplexity:
		'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) PerplexityBot/1.0',
	ccbot: 'CCBot/2.0 (https://commoncrawl.org/faq/)',
	bytespider: 'Mozilla/5.0 (compatible; Bytespider; spider-feedback@bytedance.com)',
	ahrefs: 'Mozilla/5.0 (compatible; AhrefsBot/7.0; +http://ahrefs.com/robot/)',
	semrush: 'Mozilla/5.0 (compatible; SemrushBot/7~bl; +http://www.semrush.com/bot.html)',
	facebook: 'facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)',
	whatsapp: 'WhatsApp/2.23.20.0 A',
	twitterbot: 'Twitterbot/1.0',
	linkedin: 'LinkedInBot/1.0 (compatible; Mozilla/5.0; Apache-HttpClient +http://www.linkedin.com)',
	slack: 'Slackbot-LinkExpanding 1.0 (+https://api.slack.com/robots)',
	chrome:
		'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
	safariIphone:
		'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1'
};

describe('classifyUserAgent — ordering traps', () => {
	// Each of these pairs shares a substring or a vendor, and getting the rule
	// order wrong silently collapses them into one bucket.
	it('keeps Googlebot variants distinct from plain Googlebot', () => {
		expect(classifyUserAgent(UA.googlebot)?.bot).toBe('googlebot');
		expect(classifyUserAgent(UA.googlebotImage)?.bot).toBe('googlebot-image');
		expect(classifyUserAgent(UA.googlebotNews)?.bot).toBe('googlebot-news');
		expect(classifyUserAgent(UA.adsbotMobile)?.bot).toBe('adsbot-google-mobile');
	});

	it('classifies Google-Extended as an AI crawler, not as Googlebot', () => {
		// Blocking Google-Extended in robots.txt opts out of AI training WITHOUT
		// deindexing the site; conflating the two would hide that distinction.
		const g = classifyUserAgent(UA.googleExtended);
		expect(g?.bot).toBe('google-extended');
		expect(g?.category).toBe('ai');
	});

	it('keeps the three OpenAI agents separate', () => {
		expect(classifyUserAgent(UA.gptbot)?.bot).toBe('gptbot');
		expect(classifyUserAgent(UA.chatgptUser)?.bot).toBe('chatgpt-user');
		expect(classifyUserAgent(UA.oaiSearch)?.bot).toBe('oai-searchbot');
	});

	it('distinguishes Applebot-Extended (AI) from Applebot (search)', () => {
		expect(classifyUserAgent(UA.applebot)?.category).toBe('search');
		expect(classifyUserAgent(UA.applebotExtended)?.category).toBe('ai');
	});
});

describe('classifyUserAgent — categories', () => {
	it.each([
		[UA.googlebot, 'search'],
		[UA.bingbot, 'search'],
		[UA.yandex, 'search'],
		[UA.duckduck, 'search'],
		[UA.baidu, 'search'],
		[UA.claudebot, 'ai'],
		[UA.perplexity, 'ai'],
		[UA.ccbot, 'ai'],
		[UA.bytespider, 'ai'],
		[UA.ahrefs, 'seo'],
		[UA.semrush, 'seo'],
		[UA.facebook, 'social'],
		[UA.whatsapp, 'social'],
		[UA.twitterbot, 'social'],
		[UA.linkedin, 'social'],
		[UA.slack, 'social']
	])('categorises %s', (ua, category) => {
		expect(classifyUserAgent(ua)?.category).toBe(category);
	});
});

describe('classifyUserAgent — non-crawlers', () => {
	it('returns null for ordinary browsers', () => {
		expect(classifyUserAgent(UA.chrome)).toBeNull();
		expect(classifyUserAgent(UA.safariIphone)).toBeNull();
	});

	it('returns null for a missing or empty User-Agent', () => {
		expect(classifyUserAgent(null)).toBeNull();
		expect(classifyUserAgent(undefined)).toBeNull();
		expect(classifyUserAgent('')).toBeNull();
	});

	it('is case-insensitive', () => {
		expect(classifyUserAgent('GOOGLEBOT/2.1')?.bot).toBe('googlebot');
	});
});

describe('verification contracts', () => {
	it('gives the major search engines a reverse-DNS contract', () => {
		for (const ua of [UA.googlebot, UA.bingbot, UA.yandex, UA.baidu, UA.applebot]) {
			expect(classifyUserAgent(ua)?.verifyHosts?.length).toBeGreaterThan(0);
		}
	});

	it('leaves unverifiable crawlers without one, so they report `unknown` not `unverified`', () => {
		for (const ua of [UA.ahrefs, UA.semrush, UA.ccbot, UA.facebook, UA.gptbot]) {
			expect(classifyUserAgent(ua)?.verifyHosts).toBeUndefined();
		}
	});
});

describe('knownBots', () => {
	it('is a bounded, deduplicated set — it becomes a Prometheus label', () => {
		const bots = knownBots();
		expect(new Set(bots).size).toBe(bots.length);
		expect(bots.length).toBeLessThan(80);
	});
});
