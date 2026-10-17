/**
 * The `llms.txt` body (https://llmstxt.org).
 *
 * Markdown for language models: an H1 with the site name, a `>` summary, then
 * `## Section` lists of `- [Title](absolute URL): note`. An `## Optional`
 * section marks what a model may skip when its context is short.
 *
 * Built from the same sources as the pages themselves (`AGENT_PAGES`,
 * `COMPANY`) so a new agent module appears here without touching this file.
 * English only: the `?lang=en` URLs are linked so a fetcher sending no
 * `Accept-Language` still lands on the text described here.
 */
import { AGENT_PAGES } from '$lib/marketing/agents';
import { COMPANY, legalName } from '$lib/legal/company';
import { localizedUrl } from './types';

export interface LlmsInput {
	/** Canonical origin of this host, no trailing slash. */
	origin: string;
	siteName: string;
	/** One-sentence summary for the `>` line (main site only). */
	summary?: string;
}

const LANGUAGES = 'Bulgarian, English, German and Russian';

function link(title: string, url: string, note: string): string {
	return `- [${title}](${url}): ${note}`;
}

/** The site's legal documents. */
function legalSection(origin: string): string[] {
	return [
		'## Optional',
		'',
		link(
			'Privacy Policy',
			localizedUrl(origin, '/privacy', 'en'),
			'How personal data is processed.'
		),
		link(
			'Terms and Conditions',
			localizedUrl(origin, '/terms', 'en'),
			'Terms of use of the service.'
		),
		link('Cookie Policy', localizedUrl(origin, '/cookies', 'en'), 'Cookies and consent categories.')
	];
}

/** www.pacod.io */
export function mainLlmsTxt({ origin, siteName, summary }: LlmsInput): string {
	const agents = Object.values(AGENT_PAGES).map((agent) =>
		link(
			agent.doc.en.title,
			localizedUrl(origin, `/agents/${agent.slug}`, 'en'),
			agent.doc.en.metaDescription
		)
	);

	return [
		`# ${siteName}`,
		'',
		`> ${summary ?? `${siteName} is a platform for building and running specialist AI agents.`}`,
		'',
		`${siteName} is a platform for building and running specialist AI agents. Each agent is grounded in its own curated knowledge base and a table of exact reference facts; answers are retrieved from source documents and cite what they rest on. Agents are configured in a visual editor and improve through a review loop. They are an information service, not professional advice. The interface is available in ${LANGUAGES}; every public page also exists at \`?lang=bg\`, \`?lang=de\` and \`?lang=ru\`. Using an agent requires an account.`,
		'',
		`${siteName} is operated by ${legalName.en}, Sofia, Bulgaria. Contact: ${COMPANY.email}.`,
		'',
		'## Agents',
		'',
		...agents,
		'',
		'## Company',
		'',
		link(
			'About',
			localizedUrl(origin, '/about', 'en'),
			`Mission, approach and the company behind ${siteName}.`
		),
		link('Contact', localizedUrl(origin, '/contact', 'en'), 'How to reach the team.'),
		'',
		...legalSection(origin),
		''
	].join('\n');
}
