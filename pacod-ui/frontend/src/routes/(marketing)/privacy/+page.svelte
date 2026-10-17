<script lang="ts">
	// Privacy Policy — content lives in ./content.ts (per-locale), rendered by
	// the shared <LegalDocument>. Entity facts come from $lib/legal/company.ts.
	import { getLocale } from '$lib/paraglide/runtime';
	import LegalDocument from '$lib/legal/LegalDocument.svelte';
	import Seo from '$lib/seo/Seo.svelte';
	import { privacy } from './content';

	let doc = $derived(privacy[getLocale()]);
</script>

<Seo title={doc.title} description={doc.metaDescription} canonicalPath="/privacy" localized />

<LegalDocument {doc} />
