<script lang="ts">
	/** Agent-level settings (graph `meta`): display names, description, reference year. */
	import { m } from '$lib/paraglide/messages';
	import type { AgentGraph, Issue } from '$lib/features/agent-editor/queries';

	interface Props {
		graph: AgentGraph;
		issues: Issue[];
		readonly: boolean;
		onmeta: (patch: Record<string, unknown>) => void;
	}

	let { graph, issues, readonly, onmeta }: Props = $props();

	const LOCALES = [
		{ code: 'en', label: 'English' },
		{ code: 'bg', label: 'Български' },
		{ code: 'de', label: 'Deutsch' },
		{ code: 'ru', label: 'Русский' }
	] as const;

	const meta = $derived(graph.meta as Record<string, unknown>);
	const shortNames = $derived((meta.short_names ?? {}) as Record<string, string>);
	const graphIssues = $derived(
		issues.filter((i) => !i.node_id && !i.path.startsWith('/meta/marketing'))
	);
</script>

<div class="space-y-3">
	<h2 class="text-sm font-semibold" style="color: var(--color-text);">
		{m.agent_editor_settings_title()}
	</h2>

	{#each graphIssues as issue, i (i)}
		<p
			class="rounded px-2 py-1 text-[11px]"
			style="background: {issue.severity === 'error'
				? 'var(--color-danger-soft)'
				: 'var(--color-warning-soft)'};
				color: {issue.severity === 'error' ? 'var(--color-danger)' : 'var(--color-text)'};"
		>
			{issue.message}
		</p>
	{/each}

	<label class="block">
		<span class="field-label">{m.agent_editor_meta_name()}</span>
		<input
			class="field-input w-full text-xs"
			disabled={readonly}
			value={String(meta.name ?? '')}
			oninput={(e) => onmeta({ name: e.currentTarget.value })}
		/>
	</label>

	<label class="block">
		<span class="field-label">{m.agent_editor_meta_description()}</span>
		<textarea
			class="field-textarea w-full text-xs"
			rows="3"
			disabled={readonly}
			value={String(meta.description ?? '')}
			oninput={(e) => onmeta({ description: e.currentTarget.value })}></textarea>
	</label>

	<div>
		<span class="field-label">{m.agent_editor_meta_short_names()}</span>
		<div class="grid grid-cols-2 gap-2">
			{#each LOCALES as loc (loc.code)}
				<label class="block">
					<span class="text-[10px]" style="color: var(--color-text-faint);">{loc.label}</span>
					<input
						class="field-input w-full text-xs"
						maxlength="64"
						disabled={readonly}
						value={shortNames[loc.code] ?? ''}
						oninput={(e) =>
							onmeta({ short_names: { ...shortNames, [loc.code]: e.currentTarget.value } })}
					/>
				</label>
			{/each}
		</div>
	</div>

	<label class="block">
		<span class="field-label">{m.agent_editor_meta_legal_year()}</span>
		<input
			class="field-input w-32 text-xs"
			type="number"
			min="2000"
			max="2100"
			disabled={readonly}
			value={meta.legal_year as number | undefined}
			oninput={(e) => {
				const n = e.currentTarget.valueAsNumber;
				onmeta({ legal_year: Number.isNaN(n) ? null : Math.round(n) });
			}}
		/>
	</label>
	<p class="text-[11px]" style="color: var(--color-text-faint);">{m.agent_editor_meta_note()}</p>
</div>
