<script lang="ts">
	/**
	 * Picks the LLM template of one role. "" = Legacy (the role keeps its
	 * individual settings). Shows what the role actually sends underneath, and
	 * flags a role a published agent-editor version owns (its template is then
	 * ignored by the runtime).
	 */
	import { m } from '$lib/paraglide/messages';
	import {
		paramsSummary,
		templateSummary,
		type CallParams,
		type LlmTemplate
	} from '$lib/features/llm-config/templates';

	let {
		id,
		label,
		value = $bindable(''),
		templates,
		params = null,
		managed = false,
		disabled = false
	}: {
		id: string;
		label: string;
		value?: string;
		templates: LlmTemplate[];
		params?: CallParams | null;
		managed?: boolean;
		disabled?: boolean;
	} = $props();

	const selected = $derived(templates.find((t) => t.id === value));
	const summary = $derived(
		!managed && selected ? templateSummary(selected) : params ? paramsSummary(params) : ''
	);
</script>

<div>
	<label class="field-label" for={id}>{label}</label>
	<select {id} bind:value class="field-input" {disabled}>
		<option value="">{m.llmtpl_legacy_option()}</option>
		{#each templates as t (t.id)}
			<option value={t.id}>{t.name}</option>
		{/each}
		{#if value && !selected}
			<!-- A deleted/unknown id must stay visible rather than silently switch. -->
			<option {value}>{value}</option>
		{/if}
	</select>
	{#if managed}
		<p class="mt-1 text-xs" style="color: var(--color-warning);">{m.llmtpl_managed_role()}</p>
	{/if}
	<!-- What the role sends: the chosen template (live, even before Save), else the
	     server-resolved params (legacy values, or the overlay for a managed role). -->
	{#if summary}
		<p class="mt-1 truncate font-mono text-[11px] text-[var(--color-text-muted)]" title={summary}>
			{m.llmtpl_sends({ summary })}
		</p>
	{/if}
</div>
