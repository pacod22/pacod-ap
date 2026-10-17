<script lang="ts">
	import { Bot, AlertTriangle, ExternalLink } from 'lucide-svelte';
	import { createLlmModelsQuery } from '$lib/features/llm-models/queries';
	import { m } from '$lib/paraglide/messages';

	let { data } = $props();

	// Read-only: the gateway owns the catalogue (and prices + provider keys).
	const modelsQuery = createLlmModelsQuery();

	const resp = $derived($modelsQuery.data ?? data.initial);
	const models = $derived(resp.models);
</script>

<svelte:head><title>{m.llm_models_title()}</title></svelte:head>

<div class="space-y-8">
	<div>
		<h1 class="text-2xl font-semibold tracking-tight">{m.llm_models_heading()}</h1>
		<p class="mt-1 max-w-3xl text-sm text-[var(--color-text-muted)]">
			{m.llm_models_intro()}
		</p>
		{#if resp.console_url}
			<!-- New tab, never an iframe: the gateway console forbids framing. -->
			<a
				href={resp.console_url}
				target="_blank"
				rel="noopener"
				class="mt-3 inline-flex items-center gap-1.5 text-sm font-medium text-[var(--color-brand)] hover:underline"
			>
				{m.llm_gateway_console_open()}
				<ExternalLink size={13} />
			</a>
		{/if}
	</div>

	{#if !resp.reachable}
		<div
			class="flex items-start gap-3 rounded-xl border p-4 text-sm"
			style="border-color: var(--color-warning, #b45309); background: var(--color-surface-2);"
		>
			<AlertTriangle size={16} class="mt-0.5 shrink-0" />
			<p>{m.llm_models_gateway_unreachable()}</p>
		</div>
	{/if}

	<section class="rounded-xl border p-5" style="border-color: var(--color-border);">
		<div class="mb-4 flex items-center gap-2">
			<Bot size={16} />
			<h2 class="text-base font-semibold">{m.llm_models_count({ n: models.length })}</h2>
		</div>

		<div class="overflow-x-auto rounded-lg" style="border: 1px solid var(--color-border);">
			<table class="w-full text-sm">
				<thead>
					<tr
						class="text-left text-[var(--color-text-muted)]"
						style="background: var(--color-surface-2);"
					>
						<th class="px-3 py-2 font-medium">{m.llm_models_col_model()}</th>
						<th class="px-3 py-2 font-medium">{m.llm_models_col_provider()}</th>
					</tr>
				</thead>
				<tbody>
					{#each models as row (row.id)}
						<tr style="border-top: 1px solid var(--color-border);">
							<td class="px-3 py-2 font-mono text-xs text-[var(--color-text)]">{row.id}</td>
							<td class="px-3 py-2 text-xs text-[var(--color-text-muted)]">{row.owned_by ?? '—'}</td
							>
						</tr>
					{/each}
					{#if models.length === 0}
						<tr
							><td colspan="2" class="px-3 py-6 text-center text-sm text-[var(--color-text-muted)]"
								>{m.llm_models_none()}</td
							></tr
						>
					{/if}
				</tbody>
			</table>
		</div>
	</section>
</div>
