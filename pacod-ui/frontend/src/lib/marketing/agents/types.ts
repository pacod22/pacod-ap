/**
 * Per-agent marketing pages.
 *
 * Content lives in per-locale TypeScript records, mirroring
 * `src/routes/(marketing)/*\/content.ts` — NOT in the database and NOT in
 * Paraglide. Two reasons:
 *
 *  1. The `profiles` table has no localized marketing copy to render from:
 *     `description` is a single untranslated column and `topic_description_*`
 *     is topic-guard classifier text, not prose for humans.
 *  2. Long-form marketing copy in four languages reads far better as a
 *     structured document than as a hundred flat message keys.
 *
 * Consequence to know about: a profile created through the admin UI gets no
 * landing page until someone adds a module here. That is deliberate — an
 * auto-generated page with no real copy is worse than no page.
 */
import type { Locale } from '$lib/paraglide/runtime';

export interface AgentFaq {
	q: string;
	a: string;
}

export interface AgentValueProp {
	title: string;
	body: string;
}

/** One agent's page, in one language. */
export interface AgentPage {
	/** Small kicker above the title. */
	eyebrow: string;
	/** The `<h1>`. */
	title: string;
	/** Intro paragraph under the title. */
	lead: string;
	/** `<title>` — without the brand suffix, which `<Seo>` appends. */
	metaTitle: string;
	/** `<meta name="description">`, ~150-160 chars. */
	metaDescription: string;
	valuePropsTitle: string;
	valueProps: AgentValueProp[];
	useCasesTitle: string;
	useCases: string[];
	faqTitle: string;
	/** Rendered visibly on the page — required, since it also becomes FAQPage JSON-LD. */
	faq: AgentFaq[];
	ctaTitle: string;
	ctaBody: string;
	ctaLabel: string;
	/** Breadcrumb label for the site root. */
	homeLabel: string;
}

export interface AgentDefinition {
	/** URL slug: `/agents/<slug>`. A marketing URL, not the profile id. */
	slug: string;
	/**
	 * The backend profile this page sells, used for the `/chat?agent=<id>` deep
	 * link. Decoupled from `slug` on purpose: a profile id need not make a good
	 * URL (the tax example's profile id is `taxes`, its page is `/agents/tax`).
	 */
	profileId: string;
	/** Accent colour, matching this agent's card on the landing page. */
	accent: string;
	doc: Record<Locale, AgentPage>;
}
