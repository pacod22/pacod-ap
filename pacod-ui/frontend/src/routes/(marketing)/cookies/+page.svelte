<script lang="ts">
	// Cookie Policy — content lives in ./content.ts (per-locale), rendered by the
	// shared <LegalDocument>. The cookie table reflects the cookies this app
	// actually sets; the "Manage preferences" button is emitted via the `manage`
	// block inside <LegalDocument>. Entity facts come from $lib/legal/company.ts.
	import { getLocale } from '$lib/paraglide/runtime';
	import LegalDocument from '$lib/legal/LegalDocument.svelte';
	import Seo from '$lib/seo/Seo.svelte';
	import { cookies } from './content';

	let doc = $derived(cookies[getLocale()]);
</script>

<Seo title={doc.title} description={doc.metaDescription} canonicalPath="/cookies" localized />

<LegalDocument {doc} />
