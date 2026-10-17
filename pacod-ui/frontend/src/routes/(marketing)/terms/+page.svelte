<script lang="ts">
	// Terms of Service — content lives in ./content.ts (per-locale), rendered by
	// the shared <LegalDocument>. Entity facts come from $lib/legal/company.ts.
	import { getLocale } from '$lib/paraglide/runtime';
	import LegalDocument from '$lib/legal/LegalDocument.svelte';
	import Seo from '$lib/seo/Seo.svelte';
	import { terms } from './content';

	let doc = $derived(terms[getLocale()]);
</script>

<Seo title={doc.title} description={doc.metaDescription} canonicalPath="/terms" localized />

<LegalDocument {doc} />
