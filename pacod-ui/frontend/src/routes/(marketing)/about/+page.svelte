<script lang="ts">
	// About — content lives in ./content.ts (per-locale), rendered by the shared
	// <LegalDocument>. Entity facts come from $lib/legal/company.ts.
	import { getLocale } from '$lib/paraglide/runtime';
	import LegalDocument from '$lib/legal/LegalDocument.svelte';
	import Seo from '$lib/seo/Seo.svelte';
	import { about } from './content';

	let doc = $derived(about[getLocale()]);
</script>

<Seo title={doc.title} description={doc.metaDescription} canonicalPath="/about" localized />

<LegalDocument {doc} />
