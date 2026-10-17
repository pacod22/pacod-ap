<script lang="ts">
	// Renders one localized legal/info document using the shared `.legal` styles
	// defined in (marketing)/+layout.svelte. Prose strings are trusted, static,
	// hand-authored content (see types.ts) rendered with {@html}; interactive
	// pieces are typed blocks rendered as real markup.
	import { m } from '$lib/paraglide/messages';
	import { cookieConsent } from '$lib/stores/cookie-consent.svelte';
	import type { LegalDoc } from './types';

	let { doc }: { doc: LegalDoc } = $props();
</script>

<article class="legal">
	<p class="eyebrow">{doc.eyebrow}</p>
	<h1>{doc.title}</h1>
	<p class="lead">{doc.lead}</p>
	{#if doc.updatedLine}
		<p class="meta">{doc.updatedLine}</p>
	{/if}

	{#each doc.sections as section (section.heading ?? section.blocks[0])}
		{#if section.heading}
			<h2>{section.heading}</h2>
		{/if}
		{#each section.blocks as block}
			{#if block.k === 'p'}
				<p>{@html block.html}</p>
			{:else if block.k === 'note'}
				<div class="note">{@html block.html}</div>
			{:else if block.k === 'ul'}
				<ul>
					{#each block.items as item}<li>{@html item}</li>{/each}
				</ul>
			{:else if block.k === 'ol'}
				<ol>
					{#each block.items as item}<li>{@html item}</li>{/each}
				</ol>
			{:else if block.k === 'table'}
				<div class="table-wrap">
					<table>
						{#if block.kind === 'cols' && block.head}
							<thead>
								<tr
									>{#each block.head as h}<th>{@html h}</th>{/each}</tr
								>
							</thead>
						{/if}
						<tbody>
							{#each block.rows as row}
								<tr>
									{#if block.kind === 'keyval'}
										<th>{@html row[0]}</th>
										<td>{@html row[1]}</td>
									{:else}
										{#each row as cell}<td>{@html cell}</td>{/each}
									{/if}
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			{:else if block.k === 'manage'}
				<p>
					<button type="button" class="manage-btn" onclick={() => cookieConsent.open()}>
						{m.cookie_consent_manage()}
					</button>
				</p>
			{/if}
		{/each}
	{/each}
</article>

<style>
	.manage-btn {
		font: inherit;
		font-size: 0.92rem;
		font-weight: 600;
		background: var(--color-surface-2);
		color: var(--color-text);
		border: 1px solid var(--color-border);
		border-radius: var(--r-md, 10px);
		padding: 9px 18px;
		cursor: pointer;
		transition:
			border-color 0.15s,
			color 0.15s;
	}
	.manage-btn:hover {
		border-color: var(--color-brand);
		color: var(--color-brand);
	}
</style>
